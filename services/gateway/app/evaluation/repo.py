"""Evaluation append interface (W3 implements; W4 calls on re-evaluation).

Every evaluation (initial G4 or execute-time replay) is append-only with a
monotonic ``revision`` (``UNIQUE(plan_id, revision)``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from safebank_contracts.codes import Code
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from .. import tables as t
from ..errors import GatewayError


async def latest_revision(conn: AsyncConnection, plan_id: str) -> int:
    """Return the maximum revision for *plan_id*, or 0 if none exist."""
    rev = await conn.scalar(
        select(func.max(t.evaluations.c.revision)).where(t.evaluations.c.plan_id == plan_id)
    )
    return rev or 0


async def append_evaluation(
    conn: AsyncConnection,
    *,
    plan_id: str,
    evaluation_id: str,
    revision: int,
    decision: dict[str, Any],
    risk: str,
    findings: list[dict[str, Any]],
    preflight: dict[str, Any],
    versions: dict[str, int],
    policy_version: str,
    clock_version: int,
    view_hash: str | None = None,
    view_json: dict[str, Any] | None = None,
    equivalent_to: str | None = None,
    created_at_wall: datetime | None = None,
) -> None:
    """INSERT an evaluation row with revision=N+1.

    Raises 409 STATE_CHANGED when (plan_id, revision) already exists.
    """
    now = created_at_wall if created_at_wall is not None else datetime.now(UTC)
    # Defensive: the UNIQUE(plan_id, revision) constraint is the primary guard;
    # this early check gives a cleaner error for the common case.
    existing = await conn.scalar(
        select(t.evaluations.c.id).where(
            t.evaluations.c.plan_id == plan_id,
            t.evaluations.c.revision == revision,
        )
    )
    if existing is not None:
        raise GatewayError(Code.STATE_CHANGED, 409, "evaluation revision already exists")
    await conn.execute(
        t.evaluations.insert().values(
            id=evaluation_id,
            plan_id=plan_id,
            revision=revision,
            decision=decision,
            risk=risk,
            findings=findings,
            preflight=preflight,
            versions=versions,
            policy_version=policy_version,
            view_hash=view_hash,
            view_json=view_json,
            equivalent_to=equivalent_to,
            clock_version=clock_version,
            created_at_wall=now,
        )
    )


async def update_view_json(
    conn: AsyncConnection,
    evaluation_id: str,
    plan_id: str,
    view_hash: str,
    view_json: dict[str, Any],
) -> None:
    """UPDATE evaluations SET view_hash, view_json for the given evaluation."""
    updated = await conn.execute(
        t.evaluations.update()
        .where(
            t.evaluations.c.id == evaluation_id,
            t.evaluations.c.plan_id == plan_id,
        )
        .values(view_hash=view_hash, view_json=view_json)
    )
    if updated.rowcount != 1:
        raise GatewayError(Code.NOT_FOUND, 404, "evaluation not found")
