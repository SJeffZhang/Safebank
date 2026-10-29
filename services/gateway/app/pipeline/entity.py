"""Stage ②: authoritative ownership, stable entity references and visibility."""

from safebank_bankcore.banking import apply_plan
from safebank_bankcore.references import resolved_steps
from safebank_contracts.codes import Code

from ..policy.registry import action_kind, outflow, policy, step_base_risk
from ..schemas.enums import FindingEffect
from ..taint.entities import READ_HINTS, entity_ids, owned
from ..taint.records import contains_id, load_records
from .context import EvalContext, ResolvedStep, Resolver, Stage


class EntityStage(Stage):
    name = "entity"

    async def run(self, ctx: EvalContext) -> None:
        if ctx.plan_draft is None:
            return
        ctx.steps = ()
        state = await ctx.bank_state()
        records = await load_records(ctx.conn, ctx.session_id, ctx.customer_id)
        # Bankcore owns deterministic IDs, duplicate-fingerprint reuse and reference typing.
        try:
            resolved = resolved_steps(state, ctx.plan_draft.steps, ctx.customer_id, ctx.plan_id)
        except KeyError:
            ctx.add(Code.NOT_FOUND, FindingEffect.DENY)
            return
        built = []
        created = set()
        shadow = state
        for step, data in zip(ctx.plan_draft.steps, resolved, strict=True):
            args = data["args"]
            invalid = False
            for field, entity_id in entity_ids(args):
                if entity_id in created and field == "payee_id":
                    continue
                if not owned(state, field, entity_id, ctx.customer_id):
                    ctx.add(Code.NOT_FOUND, FindingEffect.DENY, step_id=step.step_id)
                    invalid = True
                    continue
                if ctx.origin == "agent" and not any(
                    contains_id(r["response"], entity_id, tuple(r["untrusted_paths"]))
                    for r in records.values()
                ):
                    hint = (
                        "list_subscriptions"
                        if step.tool == "cancel_subscription"
                        else READ_HINTS[field]
                    )
                    ctx.add(
                        Code.ENTITY_UNSEEN,
                        FindingEffect.REPLAN,
                        step_id=step.step_id,
                        resolver=Resolver.AGENT,
                        evidence_refs=(hint,),
                    )
            if invalid:
                continue
            rule = policy(step.tool)
            if ctx.origin not in rule.allowed_origins:
                ctx.add(Code.ORIGIN_NOT_ALLOWED, FindingEffect.DENY, step_id=step.step_id)
            kind = action_kind(step.tool, args, shadow)
            built.append(
                ResolvedStep(
                    step.step_id,
                    step.tool,
                    args,
                    kind.value,
                    step_base_risk(step.tool, args, shadow),
                    outflow(step.tool, args, shadow),
                )
            )
            # Reuse the same pure rules used by execution to classify subsequent card steps.
            simulation = apply_plan(shadow, (step,), ctx.customer_id, ctx.plan_id)
            if simulation.error is None:
                shadow = simulation.state
            if step.tool == "add_payee" and simulation.error is None:
                created.add(simulation.results[0].created_ids["payee_id"])
        ctx.steps = tuple(built)


stage = EntityStage()
