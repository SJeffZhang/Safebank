"""Execute-time re-evaluation entry (W3 implements, W4 calls).

W4 must re-run ①–⑧ on a fresh snapshot before execute (PRD §7.10.4); this is the
single entry point for that replay. It reuses ``pipeline.evaluate`` with
``mode=EXECUTE_REPLAY``; W3 documents the per-stage differences in that mode.
"""

from __future__ import annotations

from .. import pipeline
from ..pipeline.context import EvalContext, EvalMode
from ..schemas.decision import Decision


async def replay(ctx: EvalContext) -> Decision:
    """Re-run the evaluation pipeline in EXECUTE_REPLAY mode and return the Decision.

    Builds a fresh ``EvalContext`` carrying only identity/input fields — no
    stage results, findings or the lazy snapshot cache — so the pipeline
    re-runs ①–⑧ against the current L3 state.
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
        profile=ctx.profile,
        wall_now=ctx.wall_now,
        conn=ctx.conn,
        l3=ctx.l3,
        audit=ctx.audit,
    )
    result = await pipeline.evaluate(fresh, mode=EvalMode.EXECUTE_REPLAY)
    if result.decision is None:
        raise RuntimeError("replay pipeline produced no Decision")
    return result.decision
