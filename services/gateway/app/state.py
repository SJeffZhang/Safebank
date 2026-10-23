"""Single entry point for status transitions on shared tables (conductor decision).

W1, W3 and W4 all write ``plans.status`` / ``approvals.status`` /
``utterances.status``. They must go through :func:`transition`, which performs a
conditional ``UPDATE … WHERE status = ANY(:from) RETURNING`` and raises 409 when
no row matched. This pins the legal transitions in one place so the parallel
tasks cannot diverge.

The tables below are the initial legal set. A task that needs a different edge
must raise it in its progress「待决」for the conductor; it must not edit this file.
"""

from __future__ import annotations

from collections.abc import Iterable

from safebank_contracts.codes import Code
from sqlalchemy import Table
from sqlalchemy.ext.asyncio import AsyncConnection

from .errors import GatewayError
from .schemas.enums import ApprovalStatus, PlanStatus, UtteranceStatus

PLAN_TRANSITIONS: dict[PlanStatus, frozenset[PlanStatus]] = {
    PlanStatus.EVALUATED: frozenset(
        {
            PlanStatus.REPLAN,
            PlanStatus.DENIED,
            PlanStatus.PENDING_APPROVAL,
            PlanStatus.CONFIRMED,
            PlanStatus.EXECUTING,
            PlanStatus.EXPIRED,
            PlanStatus.SUPERSEDED,
        }
    ),
    PlanStatus.REPLAN: frozenset({PlanStatus.EVALUATED, PlanStatus.SUPERSEDED}),
    PlanStatus.DENIED: frozenset({PlanStatus.SUPERSEDED}),
    PlanStatus.PENDING_APPROVAL: frozenset(
        {
            PlanStatus.CONFIRMED,
            PlanStatus.REJECTED,
            PlanStatus.EXPIRED,
            PlanStatus.SUPERSEDED,
            PlanStatus.EXECUTING,
        }
    ),
    PlanStatus.CONFIRMED: frozenset(
        {
            PlanStatus.EXECUTING,
            PlanStatus.EXPIRED,
            PlanStatus.SUPERSEDED,
            PlanStatus.REJECTED,
        }
    ),
    PlanStatus.EXECUTING: frozenset({PlanStatus.UNKNOWN, PlanStatus.EXECUTED, PlanStatus.FAILED}),
    PlanStatus.UNKNOWN: frozenset({PlanStatus.EXECUTED, PlanStatus.FAILED}),
    PlanStatus.EXECUTED: frozenset(),
    PlanStatus.FAILED: frozenset(),
    PlanStatus.REJECTED: frozenset({PlanStatus.SUPERSEDED}),
    PlanStatus.EXPIRED: frozenset(),
    PlanStatus.SUPERSEDED: frozenset(),
}

APPROVAL_TRANSITIONS: dict[ApprovalStatus, frozenset[ApprovalStatus]] = {
    ApprovalStatus.PENDING: frozenset(
        {
            ApprovalStatus.CONFIRMED,
            ApprovalStatus.REJECTED,
            ApprovalStatus.EXPIRED,
            ApprovalStatus.SUPERSEDED,
            ApprovalStatus.LOCKED,
        }
    ),
    ApprovalStatus.CONFIRMED: frozenset(
        {ApprovalStatus.EXPIRED, ApprovalStatus.SUPERSEDED, ApprovalStatus.USED}
    ),
    ApprovalStatus.REJECTED: frozenset(),
    ApprovalStatus.EXPIRED: frozenset(),
    ApprovalStatus.SUPERSEDED: frozenset(),
    ApprovalStatus.USED: frozenset(),
    ApprovalStatus.LOCKED: frozenset({ApprovalStatus.EXPIRED, ApprovalStatus.SUPERSEDED}),
}

UTTERANCE_TRANSITIONS: dict[UtteranceStatus, frozenset[UtteranceStatus]] = {
    UtteranceStatus.ACTIVE: frozenset(
        {UtteranceStatus.SUPERSEDED, UtteranceStatus.CONSUMED, UtteranceStatus.CANCELLED}
    ),
    UtteranceStatus.SUPERSEDED: frozenset(),
    UtteranceStatus.CONSUMED: frozenset(),
    UtteranceStatus.CANCELLED: frozenset(),
}

TRANSITIONS: dict[str, dict] = {
    "plans": PLAN_TRANSITIONS,
    "approvals": APPROVAL_TRANSITIONS,
    "utterances": UTTERANCE_TRANSITIONS,
}


def legal(table_name: str, from_status: str, to_status: str) -> bool:
    table = TRANSITIONS[table_name]
    return any(
        to_status in allowed for current, allowed in table.items() if current.value == from_status
    )


async def transition(
    conn: AsyncConnection,
    table: Table,
    *,
    id_: str,
    from_statuses: Iterable[str],
    to: str,
    code: Code = Code.STATE_CHANGED,
) -> None:
    """Conditionally move ``table.status`` from one of ``from_statuses`` to ``to``."""
    result = await conn.execute(
        table.update()
        .where(table.c.id == id_, table.c.status.in_(list(from_statuses)))
        .values(status=to)
        .returning(table.c.id)
    )
    if result.rowcount == 0:
        raise GatewayError(code, 409)
