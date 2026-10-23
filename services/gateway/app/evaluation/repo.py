"""Append-only evaluation storage; caller owns the small HTTP-free transaction."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from safebank_contracts.codes import Code
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from .. import tables as t
from ..approval import planview
from ..errors import GatewayError

if TYPE_CHECKING:
    from ..pipeline.context import EvalContext


async def append_evaluation(conn: AsyncConnection, ctx: EvalContext) -> Any:
    """Lock the plan, assign revision+1, update ctx.decision and return the row.

    Builds every decision view after assigning its revision and stores view_hash.
    The returned mapping includes an additional ``view`` (PlanView) for W4/G4.
    No approval, status transition, HTTP or audit side effect is performed here.
    """
    if ctx.decision is None or ctx.plan_id is None:
        raise ValueError("a completed decision is required")
    plan = (
        (
            await conn.execute(
                select(t.plans)
                .where(
                    t.plans.c.id == ctx.plan_id,
                    t.plans.c.customer_id == ctx.customer_id,
                )
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if plan is None:
        raise GatewayError(Code.NOT_FOUND, 404)
    revision = 1 + (
        await conn.scalar(
            select(func.max(t.evaluations.c.revision)).where(
                t.evaluations.c.plan_id == ctx.plan_id,
            )
        )
        or 0
    )
    ctx.decision = ctx.decision.model_copy(update={"revision": revision})
    view, view_hash = planview.build_view(ctx)
    decision = ctx.decision.model_dump(mode="json", exclude={"shadow_b1"})
    row = dict(
        id=ctx.decision.evaluation_id,
        plan_id=ctx.plan_id,
        revision=revision,
        decision=decision,
        risk=ctx.decision.risk.value,
        findings=decision["findings"],
        preflight=decision["preflight"],
        versions=dict(ctx.decision.versions),
        clock_version=ctx.decision.clock_version,
        policy_version=ctx.decision.policy_version,
        created_at_wall=ctx.wall_now(),
        view_hash=view_hash,
        equivalent_to=None,
    )
    await conn.execute(t.evaluations.insert().values(**row))
    return row | {"view": view}
