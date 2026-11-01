"""Stage ⑥ cumulative preflight (PRD §7.7, §7.7.1, §7.9).

The preflight is a pure application of the *same* ``bankcore.apply_plan`` rules
L3 executes, so preflight and execution cannot diverge on the P1 write subset.
It never writes L3. Hard violations are mapped to findings using the §7.7
"决策效果" column; the 7-day reserve projection (``projected_shortfall``) is
the single L2-policy source for ``SCHEDULED_SHORTFALL`` and the ``max_outflow``
used by replan hints.

B1 (PRD §10.1) runs each step against the *original* snapshot with only
structural payee-reference registration — no plan-internal balance/limit
accumulation — and skips the cumulative reserve comparison. B2 accumulates.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from safebank_bankcore.banking import Simulation, apply_plan
from safebank_bankcore.references import resolved_steps
from safebank_bankcore.reserve import projected_shortfall
from safebank_contracts.codes import Code

from ..errors import GatewayError
from ..schemas.enums import FindingEffect
from .context import EvalContext, EvalMode, PreflightResult, Resolver, Stage

_THRESHOLDS_PATH = Path(__file__).resolve().parents[2] / "policies" / "thresholds.yaml"

# §7.7 "决策效果" column for business violations surfaced by apply_plan.
# L3 强制 rows carry the effect the evaluation must produce; a REPLAN finding
# must name a resolver (agent|user).
_HARD_EFFECT: dict[Code, tuple[FindingEffect, Resolver | None]] = {
    Code.INSUFFICIENT_FUNDS: (FindingEffect.REPLAN, Resolver.USER),
    Code.CARD_SINGLE_LIMIT: (FindingEffect.REPLAN, Resolver.USER),
    Code.CARD_DAILY_LIMIT: (FindingEffect.REPLAN, Resolver.USER),
    Code.DAILY_OUTFLOW_LIMIT: (FindingEffect.DENY, None),
    Code.SUITABILITY_MISMATCH: (FindingEffect.DENY, None),
    Code.ASSESSMENT_MISSING: (FindingEffect.DENY, None),
    Code.CARD_STATE_CONFLICT: (FindingEffect.REPLAN, Resolver.USER),
    Code.DEPENDENCY_UNMET: (FindingEffect.REPLAN, Resolver.AGENT),
    Code.HOLDING_INSUFFICIENT: (FindingEffect.REPLAN, Resolver.USER),
    Code.MIN_PURCHASE_NOT_MET: (FindingEffect.REPLAN, Resolver.USER),
    Code.MANDATE_NOT_ACTIVE: (FindingEffect.DENY, None),
    Code.MANDATE_AMOUNT_EXCEEDED: (FindingEffect.DENY, None),
    Code.PENDING_ALREADY_RESOLVED: (FindingEffect.DENY, None),
    Code.PENDING_EXPIRED: (FindingEffect.DENY, None),
    # Structural codes that reach preflight only after ①②③④ could not reject.
    Code.TOOL_NOT_ALLOWED: (FindingEffect.DENY, None),
    Code.NOT_FOUND: (FindingEffect.DENY, None),
    Code.PAYEE_NOT_FOUND: (FindingEffect.REPLAN, Resolver.AGENT),
    Code.CONTRACT_INVALID: (FindingEffect.DENY, None),
    Code.IDEMPOTENCY_CONFLICT: (FindingEffect.DENY, None),
}

# The only findings for which a shrink-only replan hint is allowed (PRD §7.9).
_SHRINKABLE = frozenset(
    {
        Code.INSUFFICIENT_FUNDS,
        Code.CARD_SINGLE_LIMIT,
        Code.CARD_DAILY_LIMIT,
        Code.SCHEDULED_SHORTFALL,
        Code.HOLDING_INSUFFICIENT,
        Code.MIN_PURCHASE_NOT_MET,
    }
)

# IRREVERSIBLE_STEP is a risk escalation, not a preflight failure (PRD §7.7).
_IRREVERSIBLE = frozenset({"report_card_lost", "subscribe_product"})


def _load_thresholds() -> dict[str, int]:
    return {str(k): int(v) for k, v in yaml.safe_load(_THRESHOLDS_PATH.read_text()).items()}


class PreflightStage(Stage):
    name = "preflight"

    def __init__(self, *, reserve=None, thresholds: dict[str, int] | None = None) -> None:
        # The sole bankcore pure function. ``None`` means "no test override",
        # never an unavailable dependency: incomplete obligation data fails
        # closed inside the projection itself.
        self.reserve = projected_shortfall if reserve is None else reserve
        self.thresholds = _load_thresholds() if thresholds is None else thresholds

    async def run(self, ctx: EvalContext) -> None:
        ctx.preflight = PreflightResult()
        if ctx.plan_draft is None:
            return
        if ctx.has_hard_deny():
            ctx.preflight.executable = False
            return
        state = await ctx.bank_state()
        steps = ctx.plan_draft.steps
        cumulative = not ctx.profile.startswith("B1")
        affects_reserve = any(
            s.tool in {"transfer", "subscribe_product", "schedule_transfer"} for s in ctx.steps
        )

        self._single_thresholds(ctx)
        sim = self._simulate(state, steps, ctx.customer_id, ctx.plan_id, cumulative)
        if sim.error is not None:
            await self._record_hard(ctx, sim, cumulative)
            if cumulative and affects_reserve:
                self._display_reserve(ctx, state)
        else:
            self._record_success(ctx, state, sim)
            if cumulative and affects_reserve:
                await self._reserve(ctx, state, sim.state)
        self._irreversible(ctx)

    # ---- per-step single thresholds (B1 and B2) ---------------------------
    def _single_thresholds(self, ctx: EvalContext) -> None:
        cap = self.thresholds["amount_above_yellow_cap_cents"]
        for step in ctx.steps:
            if step.tool == "transfer" and step.outflow and step.args["amount_cents"] > cap:
                ctx.add(
                    Code.AMOUNT_ABOVE_YELLOW_CAP,
                    FindingEffect.RISK_RED,
                    step_id=step.step_id,
                    message="对外转账单笔超过红线",
                )

    def _irreversible(self, ctx: EvalContext) -> None:
        for step in ctx.steps:
            if step.tool in _IRREVERSIBLE:
                ctx.add(
                    Code.IRREVERSIBLE_STEP,
                    FindingEffect.RISK_RED,
                    step_id=step.step_id,
                    message="不可逆操作，需在面板中提示",
                )

    # ---- apply_plan (cumulative or B1 single-step) ------------------------
    def _simulate(self, state, steps, customer_id, plan_id, cumulative: bool) -> Simulation:
        if cumulative:
            return apply_plan(state, steps, customer_id, plan_id)
        # B1: structural payee-reference registration only; every business check
        # starts from the original snapshot (PRD §10.1).
        resolved = resolved_steps(state, steps, customer_id, plan_id)
        resolved_steps_models = [
            step.model_copy(update={"args": type(step.args).model_validate(data["args"])})
            for step, data in zip(steps, resolved, strict=True)
        ]
        results = []
        error: Code | None = None
        for step in resolved_steps_models:
            single = apply_plan(state, (step,), customer_id, plan_id)
            if single.error is not None and error is None:
                error = single.error
            results.extend(single.results)
        return Simulation(state, (), tuple(results), error)

    # ---- hard violation handling ------------------------------------------
    async def _record_hard(self, ctx: EvalContext, sim: Simulation, cumulative: bool) -> None:
        failed_step = next((r.step_id for r in sim.results if r.status == "FAILED"), None)
        code = sim.error
        effect, resolver = _HARD_EFFECT.get(code, (FindingEffect.DENY, None))
        ctx.add(code, effect, step_id=failed_step, resolver=resolver, message=code.value)
        ctx.preflight.executable = False
        ctx.preflight.steps = tuple(
            {
                "step_id": r.step_id,
                "status": r.status,
                "effects": [e.model_dump(mode="json") for e in r.effects],
                "violations": [{"code": r.code.value}] if r.code else [],
            }
            for r in sim.results
        )
        if (
            cumulative
            and ctx.mode is not EvalMode.SHADOW_B1
            and code in _SHRINKABLE
            and failed_step is not None
        ):
            maximum = await self._failed_step_max(ctx, failed_step, code)
            if maximum is not None and maximum > 0:
                ctx.preflight.hint_max_outflow[failed_step] = maximum

    def _record_success(self, ctx: EvalContext, before, sim: Simulation) -> None:
        ctx.preflight.steps = tuple(
            {
                "step_id": r.step_id,
                "status": r.status,
                "effects": [e.model_dump(mode="json") for e in r.effects],
                "violations": [],
            }
            for r in sim.results
        )
        ctx.preflight.balances = self._balance_views(ctx, before, sim.state)

    # ---- 7-day reserve (B2 only) ------------------------------------------
    def _display_reserve(self, ctx: EvalContext, before) -> None:
        """Best-effort display reserve for a plan that already failed.

        The failure finding (INSUFFICIENT_FUNDS etc.) must never be masked by a
        reserve dependency 503, so an unavailable projection just leaves the
        display fields at zero.
        """
        report = self._project_optional(ctx, before)
        if report is None:
            return
        ctx.preflight.scheduled_reserve_cents = report.total_reserve_cents
        ctx.preflight.shortfall_before_cents = report.total_shortfall_cents

    async def _reserve(self, ctx: EvalContext, before, after) -> None:
        report_before = self._project(ctx, before)
        report_after = self._project(ctx, after)
        if report_before is None or report_after is None:
            return
        ctx.preflight.scheduled_reserve_cents = report_before.total_reserve_cents
        ctx.preflight.shortfall_before_cents = report_before.total_shortfall_cents
        ctx.preflight.shortfall_after_cents = report_after.total_shortfall_cents
        if report_after.total_shortfall_cents > report_before.total_shortfall_cents:
            # Attribute the grown shortfall to the first step that pushed the
            # 7-day reserve over its boundary, so the shrink hint only lowers
            # the truly over-limit step (never enlarges an earlier one).
            step_id = self._offending_shortfall_step(ctx, before)
            ctx.add(
                Code.SCHEDULED_SHORTFALL,
                FindingEffect.REPLAN,
                step_id=step_id,
                resolver=Resolver.USER,
                message="计划使未来 7 天已授权扣费/转账的总缺口增大",
            )
            if ctx.mode is not EvalMode.SHADOW_B1 and step_id is not None:
                maximum = await self._failed_step_max(ctx, step_id, Code.SCHEDULED_SHORTFALL)
                if maximum is not None and maximum > 0:
                    ctx.preflight.hint_max_outflow[step_id] = maximum

    def _offending_shortfall_step(self, ctx: EvalContext, before) -> str | None:
        """First step whose cumulative prefix grows the 7-day total shortfall.

        Re-applies the shared ``bankcore.apply_plan`` on each prefix (never new
        arithmetic) and returns the step that crossed the boundary. ``None`` when
        the projection is unavailable (no new hint is fabricated).
        """
        report_before = self._project_optional(ctx, before)
        if report_before is None:
            return None
        baseline = report_before.total_shortfall_cents
        steps = ctx.plan_draft.steps
        for index in range(1, len(steps) + 1):
            shadow = apply_plan(before, steps[:index], ctx.customer_id, ctx.plan_id).state
            report = self._project_optional(ctx, shadow)
            if report is not None and report.total_shortfall_cents > baseline:
                return steps[index - 1].step_id
        return None

    def _project_optional(self, ctx: EvalContext, state):
        try:
            return self.reserve(state, days=7)
        except ValueError:
            return None

    def _project(self, ctx: EvalContext, state):
        try:
            return self.reserve(state, days=7)
        except ValueError as exc:
            if ctx.has_hard_deny():
                # A hard DENY already produced by this stage must not be masked
                # by a reserve dependency failure.
                return None
            raise GatewayError(
                Code.DEPENDENCY_UNAVAILABLE,
                503,
                message="obligation reserve is unavailable or incomplete",
            ) from exc

    # ---- shrink-only hint support -----------------------------------------
    async def _failed_step_max(self, ctx: EvalContext, step_id: str, code: Code) -> int | None:
        state = await ctx.bank_state()
        steps = ctx.plan_draft.steps
        index = next((i for i, s in enumerate(steps) if s.step_id == step_id), None)
        if index is None:
            return None
        prefix = apply_plan(state, steps[:index], ctx.customer_id, ctx.plan_id)
        if prefix.error is not None:
            return None
        shadow = prefix.state
        step = ctx.steps[index]
        args = step.args
        report = self._project_optional(ctx, shadow)
        if report is None:
            return None
        account = report.accounts.get(args.get("from_account_id", args.get("account_id")))
        if account is None:
            return None
        maximum = account.max_outflow_cents
        if step.tool != "transfer":
            return maximum
        if step.outflow:
            single = self._card_single_limit(shadow, args)
            daily = self._card_daily_remaining(shadow, args)
            if single is None or daily is None:
                return None
            customer_daily = self.thresholds["daily_outflow_limit_cents"] - (
                shadow.counters.daily_outflow_cents.get(ctx.customer_id, 0)
            )
            maximum = min(maximum, single, daily, customer_daily)
        if maximum <= 0:
            return None
        # A hint is a proposal, but must not promise an amount that fails some
        # other constraint or leaves a later unchanged step unable to execute.
        # Reuse the exact pure execution rules; never mutate the evaluated plan.
        candidate = steps[index].model_copy(
            update={"args": steps[index].args.model_copy(update={"amount_cents": maximum})}
        )
        trial = apply_plan(
            state, (*steps[:index], candidate, *steps[index + 1 :]), ctx.customer_id, ctx.plan_id
        )
        baseline = self._project_optional(ctx, state)
        projected = self._project_optional(ctx, trial.state) if trial.error is None else None
        if baseline is None or projected is None:
            return None
        if projected.total_shortfall_cents > baseline.total_shortfall_cents:
            return None
        return maximum

    @staticmethod
    def _card_single_limit(shadow, args) -> int | None:
        account = shadow.accounts.get(args["from_account_id"])
        if account is None:
            return None
        if account.current_card_id and account.current_card_id in shadow.cards:
            return shadow.cards[account.current_card_id].single_limit_cents
        return account.single_limit_cents

    @staticmethod
    def _card_daily_remaining(shadow, args) -> int | None:
        account = shadow.accounts.get(args["from_account_id"])
        if account is None:
            return None
        if account.current_card_id and account.current_card_id in shadow.cards:
            limit = shadow.cards[account.current_card_id].daily_limit_cents
        else:
            limit = account.daily_limit_cents
        used = shadow.counters.daily_used_cents.get(account.id, 0)
        return limit - used

    # ---- public view helpers ----------------------------------------------
    def _balance_views(self, ctx: EvalContext, before, after) -> tuple[dict, ...]:
        views = []
        for account in after.accounts.values():
            if account.customer_id != ctx.customer_id or account.type == "INTERNAL":
                continue
            old = before.accounts[account.id]
            views.append(
                {
                    "account_id": account.id,
                    "balance_before_cents": old.balance_cents,
                    "balance_after_cents": account.balance_cents,
                    "available_before_cents": old.available_cents,
                    "available_after_cents": account.available_cents,
                    "daily_used_before_cents": before.counters.daily_used_cents.get(account.id, 0),
                    "daily_used_after_cents": after.counters.daily_used_cents.get(account.id, 0),
                }
            )
        return tuple(views)


stage = PreflightStage()
