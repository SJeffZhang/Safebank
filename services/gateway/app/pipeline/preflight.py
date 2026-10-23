"""Stage ⑥: shared bankcore simulation; this module has no domain arithmetic.

B1 starts each business step from the original snapshot. Only newly created
payees are registered structurally, so references work without carrying card,
balance or counter changes across steps. Unsupported domain paths retain the
bankcore TOOL_NOT_ALLOWED denial rather than inventing a successful simulation.
"""

from safebank_bankcore.banking import Simulation, apply_plan
from safebank_bankcore.ledger import replace_state
from safebank_contracts.codes import Code
from safebank_contracts.plan import EntityRef, PlanBody
from safebank_contracts.state import BankState

from ..errors import GatewayError
from ..schemas.decision import BalanceView, PreflightView, StepPreflight
from ..schemas.enums import FindingEffect
from .context import EvalContext, PreflightResult, Resolver, Stage

DENY_CODES = frozenset(
    {
        Code.DAILY_OUTFLOW_LIMIT,
        Code.SUITABILITY_MISMATCH,
        Code.ASSESSMENT_MISSING,
        Code.MANDATE_NOT_ACTIVE,
        Code.MANDATE_AMOUNT_EXCEEDED,
        Code.TOOL_NOT_ALLOWED,
        Code.OWNERSHIP_MISMATCH,
        Code.NOT_FOUND,
    }
)
AGENT_READS = {
    Code.PAYEE_NOT_FOUND: ("list_payees",),
    Code.DEPENDENCY_UNMET: ("get_accounts", "list_payees", "get_holdings"),
    Code.SPLIT_INVALID: ("list_payees",),
    Code.SCHEDULE_INVALID: ("list_scheduled",),
}


def independent(state: BankState, steps, customer_id: str, plan_id: str) -> Simulation:
    """B1 structurally registers payees; all financial state stays initial."""
    structural = state
    results, effects, refs = [], [], {}
    first_error = None
    for step in steps:
        args = step.args
        if isinstance(getattr(args, "payee_id", None), EntityRef):
            ref = refs.get(args.payee_id.ref)
            if ref is not None:
                args = args.model_copy(update={"payee_id": ref})
                step = step.model_copy(update={"args": args})
        sim = apply_plan(structural, (step,), customer_id, plan_id)
        first_error = first_error or sim.error
        results.extend(sim.results)
        effects.extend(sim.effects)
        if step.tool == "add_payee" and sim.error is None:
            structural = replace_state(structural, payees=dict(sim.state.payees))
            refs[step.step_id] = sim.results[0].result["payee_id"]
    return Simulation(state, tuple(effects), tuple(results), first_error)


def public_view(ctx: EvalContext, sim: Simulation) -> PreflightView:
    before, after = ctx.snapshot, sim.state
    return PreflightView(
        executable=sim.error is None,
        steps=tuple(
            StepPreflight(
                step_id=r.step_id,
                status=r.status,
                effects=tuple(e.model_dump(mode="json") for e in r.effects),
                violations=({"code": r.code.value, "step_id": r.step_id},)
                if r.status == "FAILED" and r.code
                else (),
            )
            for r in sim.results
        ),
        balances=tuple(
            BalanceView(
                account_id=a.id,
                balance_before_cents=a.balance_cents,
                balance_after_cents=after.accounts[a.id].balance_cents,
                available_before_cents=a.available_cents,
                available_after_cents=after.accounts[a.id].available_cents,
                daily_used_before_cents=before.counters.daily_used_cents.get(a.id, 0),
                daily_used_after_cents=after.counters.daily_used_cents.get(a.id, 0),
            )
            for a in before.accounts.values()
            if a.customer_id == ctx.customer_id
        ),
    )


class PreflightStage(Stage):
    name = "preflight"

    async def run(self, ctx: EvalContext) -> None:
        if ctx.plan_id is None:
            return
        if ctx.snapshot is None:
            raise RuntimeError("snapshot is required before preflight")
        steps = (
            ctx.plan_draft.steps
            if ctx.plan_draft
            else PlanBody(
                intents=(),
                steps=[
                    dict(step_id=s.step_id, tool=s.tool, args=s.args, depends_on=(), arg_sources=())
                    for s in ctx.steps
                ],
                rationale="",
            ).steps
        )
        simulate = independent if ctx.profile == "B1" else apply_plan
        sim = simulate(ctx.snapshot, steps, ctx.customer_id, ctx.plan_id)
        if (
            sim.error is None
            and ctx.profile != "B1"
            and any(s.tool in ("transfer", "schedule_transfer", "subscribe_product") for s in steps)
        ):
            # Shared reserve is not implemented on this baseline. Empty snapshot
            # obligation lists do not prove there are no obligations. Never
            # return an executable Decision after silently omitting this policy.
            raise GatewayError(
                Code.DEPENDENCY_UNAVAILABLE, 503, "shared reservation projection unavailable"
            )
        violations = []
        for result in sim.results:
            if result.status != "FAILED" or result.code is None:
                continue
            code = result.code
            violation = {"code": code.value, "step_id": result.step_id}
            violations.append(violation)
            if code in DENY_CODES:
                ctx.add(code, FindingEffect.DENY, step_id=result.step_id)
            else:
                reads = AGENT_READS.get(code, ())
                ctx.add(
                    code,
                    FindingEffect.REPLAN,
                    step_id=result.step_id,
                    resolver=Resolver.AGENT if reads else Resolver.USER,
                    evidence_refs=reads,
                )
        ctx.preflight = PreflightResult(
            executable=sim.error is None,
            violations=tuple(violations),
            view=public_view(ctx, sim),
            shadow_state=sim.state,
        )


stage = PreflightStage()
