"""Single entry point for status transitions on shared tables (conductor decision).

W1, W3 and W4 all write ``plans.status`` / ``approvals.status`` /
``utterances.status`` / ``execution_jobs.status``. They must go through
:func:`transition`, which validates the requested edge against the legal tables
below and then performs a conditional ``UPDATE … WHERE status = ANY(:from)
RETURNING`` (0 rows -> 409). A task that needs a different edge must raise it in
its progress「待决」for the conductor; it must not edit this file.

EXECUTING timing: §7.10.4 re-runs ①–⑧ on a fresh snapshot *before* the plan is
marked EXECUTING, so the re-evaluation can change a CONFIRMED/EVALUATED plan to
PENDING_APPROVAL / DENIED / REPLAN; EXECUTING itself is only entered once the
final decision permits execution and never transitions back to a decision state.
"""

from __future__ import annotations

from collections.abc import Iterable

from safebank_contracts.codes import Code
from sqlalchemy import Table
from sqlalchemy.ext.asyncio import AsyncConnection

from .errors import GatewayError
from .schemas.enums import (
    ApprovalStatus,
    ClarificationStatus,
    ExecutionJobStatus,
    PlanStatus,
    UtteranceStatus,
)

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
    # §7.10.4-5: a re-run before execution can change the decision, sending a
    # previously CONFIRMED plan back to PENDING_APPROVAL / DENIED / REPLAN.
    PlanStatus.CONFIRMED: frozenset(
        {
            PlanStatus.EXECUTING,
            PlanStatus.EXPIRED,
            PlanStatus.SUPERSEDED,
            PlanStatus.REJECTED,
            PlanStatus.PENDING_APPROVAL,
            PlanStatus.DENIED,
            PlanStatus.REPLAN,
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

EXECUTION_JOB_TRANSITIONS: dict[ExecutionJobStatus, frozenset[ExecutionJobStatus]] = {
    ExecutionJobStatus.EXECUTING: frozenset(
        {
            ExecutionJobStatus.UNKNOWN,
            ExecutionJobStatus.EXECUTED,
            ExecutionJobStatus.FAILED,
            ExecutionJobStatus.CANCELLED,
        }
    ),
    ExecutionJobStatus.UNKNOWN: frozenset(
        {ExecutionJobStatus.EXECUTED, ExecutionJobStatus.FAILED, ExecutionJobStatus.CANCELLED}
    ),
    ExecutionJobStatus.EXECUTED: frozenset(),
    ExecutionJobStatus.FAILED: frozenset(),
    ExecutionJobStatus.CANCELLED: frozenset(),
}

CLARIFICATION_TRANSITIONS: dict[ClarificationStatus, frozenset[ClarificationStatus]] = {
    ClarificationStatus.OPEN: frozenset(
        {ClarificationStatus.RESOLVED, ClarificationStatus.CANCELLED}
    ),
    ClarificationStatus.RESOLVED: frozenset(),
    ClarificationStatus.CANCELLED: frozenset(),
}

TRANSITIONS: dict[str, dict] = {
    "pending_clarifications": CLARIFICATION_TRANSITIONS,
    "plans": PLAN_TRANSITIONS,
    "approvals": APPROVAL_TRANSITIONS,
    "utterances": UTTERANCE_TRANSITIONS,
    "execution_jobs": EXECUTION_JOB_TRANSITIONS,
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
    """Move ``table.status`` from one of ``from_statuses`` to ``to``.

    Every requested edge must be legal per ``TRANSITIONS[table.name]``; an
    illegal edge is a programming error and raises ``ValueError`` (not 409).
    """
    table_rules = TRANSITIONS.get(table.name)
    if table_rules is None:
        raise ValueError(f"no transition table for {table.name!r}")
    from_list = list(from_statuses)
    for from_status in from_list:
        allowed = table_rules.get(_member(table_rules, from_status))
        if allowed is None or _member(table_rules, to) not in allowed:
            raise ValueError(f"illegal {table.name} transition {from_status} -> {to}")
    result = await conn.execute(
        table.update()
        .where(table.c.id == id_, table.c.status.in_(from_list))
        .values(status=to)
        .returning(table.c.id)
    )
    if result.rowcount == 0:
        raise GatewayError(code, 409)


def _member(table: dict, value: str):
    for member in table:
        if member.value == value:
            return member
    return None
