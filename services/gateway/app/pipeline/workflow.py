"""Stage ⑦: committed L3 operations plus current-plan shadow actions.

Only COMMITTED gateway events supplement successful sequences. BLOCKED history
is used exclusively for retry escalation. No findings are persisted here;
SHADOW_B1 is therefore side-effect free. Cooldowns are persisted with G4.
"""

from datetime import timedelta

from safebank_bankcore.banking import apply_plan
from safebank_bankcore.references import request_digest
from safebank_contracts.codes import Code
from safebank_contracts.state import Operation
from safebank_contracts.tools import EntityRef
from sqlalchemy import select

from .. import tables as t
from ..policy.registry import action_kind, outflow
from ..schemas.enums import FindingEffect
from ..workflow.sequence import THRESHOLDS, risky_sequence
from .context import EvalContext, Stage, WorkflowResult


async def history(ctx: EvalContext):
    """Merge event IDs once; preserve L3 truth when both stores contain an event."""
    state = ctx.snapshot
    now, wall = state.sim_now, ctx.wall_now()
    events = {
        e.event_id: e
        for e in state.ops_7d
        if e.customer_id == ctx.customer_id and e.occurred_at <= now
    }
    cooldowns, recent = {}, []
    if ctx.conn is not None:
        rows = (
            await ctx.conn.execute(
                select(t.workflow_events).where(
                    t.workflow_events.c.customer_id == ctx.customer_id,
                    t.workflow_events.c.status == "COMMITTED",
                    t.workflow_events.c.occurred_at >= now - timedelta(days=7),
                    t.workflow_events.c.occurred_at <= now,
                )
            )
        ).mappings()
        for row in rows:
            events.setdefault(
                row["event_id"],
                Operation(
                    event_id=row["event_id"],
                    customer_id=ctx.customer_id,
                    plan_id=row["plan_id"] or "pln_event",
                    step_id=row["step_id"] or "step_event",
                    action_kind=row["action_kind"],
                    target_fingerprint=row["target_id"],
                    amount_cents=row["amount_cents"],
                    occurred_at=row["occurred_at"],
                ),
            )
        rows = (
            await ctx.conn.execute(
                select(t.cooldowns).where(
                    t.cooldowns.c.customer_id == ctx.customer_id,
                    t.cooldowns.c.until_at > now,
                )
            )
        ).mappings()
        cooldowns = {r["payee_fingerprint"]: r["until_at"] for r in rows}
        rows = (
            await ctx.conn.execute(
                select(t.plans).where(
                    t.plans.c.customer_id == ctx.customer_id,
                    t.plans.c.id != ctx.plan_id,
                    t.plans.c.created_at_wall
                    >= wall - timedelta(minutes=THRESHOLDS["retry_minutes"]),
                    t.plans.c.created_at_wall <= wall,
                    t.plans.c.status.in_(("EXECUTED", "DENIED", "REJECTED")),
                )
            )
        ).mappings()
        recent = list(rows)
    return sorted(events.values(), key=lambda e: (e.occurred_at, e.commit_seq)), cooldowns, recent


def planned(ctx: EvalContext):
    """Registry classifies against each prefix; bankcore resolves created targets."""
    state = ctx.snapshot
    refs = {}
    steps = ctx.plan_draft.steps if ctx.plan_draft else ()
    for step in steps:
        args = step.args
        target = None
        if step.tool == "add_payee":
            entry = state.directory.get("number:" + args.account_no)
            target = entry.target_fingerprint if entry else None
            refs[step.step_id] = target
        payee_id = getattr(args, "payee_id", None)
        if isinstance(payee_id, EntityRef):
            target = refs.get(payee_id.ref)
        elif isinstance(payee_id, str):
            payee = state.payees.get(payee_id)
            target = payee.target_fingerprint if payee else None
        try:
            kind = action_kind(step.tool, args, state)
        except LookupError:
            continue  # entity/preflight already fail closed for invisible cards
        external = outflow(step.tool, args, state)
        yield (
            step,
            Operation(
                event_id=f"shadow:{ctx.plan_id}:{step.step_id}",
                customer_id=ctx.customer_id,
                plan_id=ctx.plan_id,
                step_id=step.step_id,
                action_kind=kind.value,
                target_fingerprint=target,
                amount_cents=getattr(args, "amount_cents", 0),
                occurred_at=state.sim_now,
            ),
            external,
        )
        sim = apply_plan(state, (step,), ctx.customer_id, ctx.plan_id)
        if sim.error is None:
            state = sim.state


class WorkflowStage(Stage):
    name = "workflow"

    async def run(self, ctx: EvalContext) -> None:
        if ctx.plan_id is None:
            return
        if ctx.snapshot is None:
            raise RuntimeError("snapshot is required before workflow")
        events, active, recent = await history(ctx)
        now = ctx.snapshot.sim_now
        new_cooldowns = {}
        finding_start = len(ctx.findings)
        for step, event, external in planned(ctx):
            target = event.target_fingerprint
            if external and target:
                prior = [
                    e
                    for e in events
                    if e.action_kind == "TRANSFER_EXTERNAL"
                    and now - timedelta(hours=24) <= e.occurred_at <= now
                ]
                same = [e for e in prior if e.target_fingerprint == target]
                amount = event.amount_cents
                if amount > THRESHOLDS["yellow_cap_cents"]:
                    ctx.add(
                        Code.AMOUNT_ABOVE_YELLOW_CAP, FindingEffect.RISK_RED, step_id=step.step_id
                    )
                if (
                    len(same) + 1 >= THRESHOLDS["split_count"]
                    and sum(e.amount_cents for e in same) + amount > THRESHOLDS["split_total_cents"]
                ):
                    ctx.add(Code.SPLIT_TRANSFER, FindingEffect.RISK_RED, step_id=step.step_id)
                if (
                    sum(e.amount_cents for e in prior) + amount
                    > THRESHOLDS["cumulative_outflow_cents"]
                ):
                    ctx.add(Code.CUMULATIVE_OUTFLOW, FindingEffect.RISK_RED, step_id=step.step_id)
                # Successful first-transfer history uses the complete available 7d
                # operations plus current-plan predecessors, never rejected plans.
                historical_paid = any(
                    tx.customer_id == ctx.customer_id
                    and tx.direction == "OUT"
                    and tx.kind in ("P2P_INTERNAL", "P2P_EXTERNAL")
                    and tx.phase in ("POSTED", "POST_PENDING")
                    and tx.occurred_at <= now
                    and tx.payee_id in ctx.snapshot.payees
                    and ctx.snapshot.payees[tx.payee_id].target_fingerprint == target
                    for tx in ctx.snapshot.transactions
                )
                if not historical_paid and not any(
                    e.action_kind == "TRANSFER_EXTERNAL" and e.target_fingerprint == target
                    for e in events
                ):
                    ctx.add(
                        Code.FIRST_TRANSFER_TO_PAYEE,
                        FindingEffect.RISK_YELLOW,
                        step_id=step.step_id,
                    )
                payee = next(
                    (
                        p
                        for p in ctx.snapshot.payees.values()
                        if p.target_fingerprint == target and p.customer_id == ctx.customer_id
                    ),
                    None,
                )
                created = (
                    payee.created_at
                    if payee
                    else next(
                        (
                            e.occurred_at
                            for e in reversed(events)
                            if e.action_kind == "PAYEE_ADD" and e.target_fingerprint == target
                        ),
                        None,
                    )
                )
                if created and timedelta(0) <= now - created <= timedelta(
                    minutes=THRESHOLDS["new_payee_minutes"]
                ):
                    ctx.add(
                        Code.NEW_PAYEE_FIRST_TRANSFER, FindingEffect.RISK_RED, step_id=step.step_id
                    )
                if target in active or risky_sequence(events, target, now):
                    ctx.add(
                        Code.RISKY_SEQUENCE,
                        FindingEffect.DENY,
                        step_id=step.step_id,
                        evidence_refs=tuple(
                            e.event_id
                            for e in events
                            if e.action_kind
                            in {
                                "PAYEE_ADD",
                                "CARD_LIMIT_LOOSEN",
                                "CARD_CONTROL_LOOSEN",
                                "CARD_UNFREEZE",
                            }
                        ),
                    )
                    if target not in active:
                        new_cooldowns[target] = now + timedelta(hours=THRESHOLDS["cooldown_hours"])
            events.append(event)
        if ctx.plan_draft:
            digest = request_digest(ctx.plan_draft.steps)
            for row in recent:
                if row["status"] == "EXECUTED" and row["steps_hash"] == digest:
                    ctx.add(
                        Code.DUPLICATE_PLAN, FindingEffect.RISK_YELLOW, evidence_refs=(row["id"],)
                    )
                if row["status"] in ("DENIED", "REJECTED"):
                    # resolved_steps persist fingerprints to avoid trusting arbitrary IDs.
                    old_pairs = {
                        (s.get("action_kind"), s.get("target_fingerprint"))
                        for s in row["resolved_steps"]
                    }
                    new_pairs = {(e.action_kind, e.target_fingerprint) for _, e, _ in planned(ctx)}
                    if old_pairs & new_pairs:
                        ctx.add(
                            Code.REJECTED_RETRY, FindingEffect.RISK_RED, evidence_refs=(row["id"],)
                        )
        ctx.workflow = WorkflowResult(
            findings=tuple(f.model_dump(mode="json") for f in ctx.findings[finding_start:]),
            cooldowns=tuple(sorted(new_cooldowns.items())),
        )


stage = WorkflowStage()
