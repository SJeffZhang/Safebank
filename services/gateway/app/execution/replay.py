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
    decision, _fresh = await replay_with_context(ctx)
    return decision


async def replay_with_context(ctx: EvalContext) -> tuple[Decision, EvalContext]:
    """Same as :func:`replay`, but also returns the fresh context.

    The fresh context includes :attr:`EvalContext.steps` (resolved steps from
    the re-evaluation), which callers can use to recompute plan_hash in the
    same way G4 does.
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
    if fresh.origin == "app_form":
        from ..forms import load_forms

        fresh.authorization, _, _, _ = await load_forms(
            fresh.conn,
            fresh.session_id,
            fresh.customer_id,
            fresh.plan_draft.utterance_ids,
            fresh.wall_now(),
            plan_id=fresh.plan_id,
            bank=await fresh.bank_state(),
            require_bound=True,
        )
    result = await pipeline.evaluate(fresh, mode=EvalMode.EXECUTE_REPLAY)
    if result.decision is None:
        raise RuntimeError("replay pipeline produced no Decision")
    return result.decision, fresh
