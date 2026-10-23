"""Execute-time re-evaluation using a fresh context and fresh snapshot every time."""

from __future__ import annotations

from ..pipeline import evaluate
from ..pipeline.context import EvalContext, EvalMode
from ..schemas.decision import Decision


async def replay(ctx: EvalContext) -> Decision:
    """Run ①–⑨ in EXECUTE_REPLAY; copy fresh result slots back for build_view.

    No cached state, findings, stage statuses or old decisions enter the new
    context. Authorization must be refreshed by W4 before calling; stages also
    re-check its persisted sources. The caller appends the evaluation and audits
    only after the fresh Decision has been assigned its persistent revision.
    """
    fresh = EvalContext(
        request=ctx.request,
        identity=ctx.identity,
        authorization=ctx.authorization,
        plan_draft=ctx.plan_draft,
        plan_id=ctx.plan_id,
        origin=ctx.origin,
        customer_id=ctx.customer_id,
        session_id=ctx.session_id,
        mode=EvalMode.EXECUTE_REPLAY,
        profile=ctx.profile,
        wall_now=ctx.wall_now,
        conn=ctx.conn,
        l3=ctx.l3,
    )
    await evaluate(fresh, mode=EvalMode.EXECUTE_REPLAY)
    if fresh.decision is None:
        raise RuntimeError("replay did not produce a Decision")
    for name in (
        "snapshot",
        "preflight",
        "workflow",
        "provenance",
        "intent",
        "steps",
        "findings",
        "stage_status",
        "decision",
        "short_circuit",
    ):
        setattr(ctx, name, getattr(fresh, name))
    return fresh.decision
