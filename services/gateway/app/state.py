"""Single entry point for status transitions on shared tables (conductor decision).

W1, W3 and W4 all write ``plans.status`` / ``approvals.status`` /
``utterances.status`` / ``execution_jobs.status``. They must go through
:func:`transition`, which validates the requested edge against the legal tables
below and then performs a conditional ``UPDATE … WHERE status = ANY(:from)
RETURNING`` (0 rows -> 409). A task that needs a different edge must raise it in
its progress「待决」for the conductor; it must not edit this file.

Before the first prepare, §7.10 re-evaluation may change ordinary decision
states. After prepare, ADR-008 requires a verified same-attempt fence and a
persisted PAUSED job; only the dedicated CAS helpers below may publish a new
decision or persist attempt+1. Ordinary EXECUTING/UNKNOWN fallback stays illegal.
HTTP ACK/terminal authenticity, realtime authorization and actual customer lock
ownership are caller responsibilities; these helpers enforce persistent guards.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import ValidationError
from safebank_contracts.codes import Code
from safebank_contracts.plan import ExecutionResult, InteractiveEvidence
from sqlalchemy import Table, func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from . import tables as t
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
    ExecutionJobStatus.FENCING: frozenset({ExecutionJobStatus.UNKNOWN}),
    ExecutionJobStatus.PAUSED: frozenset({ExecutionJobStatus.UNKNOWN}),
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


class _ClaimStatus(StrEnum):
    CLAIMED = "CLAIMED"
    CONSUMED = "CONSUMED"


CLAIM_TRANSITIONS: dict[_ClaimStatus, frozenset[_ClaimStatus]] = {
    _ClaimStatus.CLAIMED: frozenset({_ClaimStatus.CONSUMED}),
    _ClaimStatus.CONSUMED: frozenset(),
}

TRANSITIONS: dict[str, dict] = {
    "authorization_claims": CLAIM_TRANSITIONS,
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
    primary_keys = list(table.primary_key.columns)
    if len(primary_keys) != 1:
        raise ValueError("status transitions require a single-column primary key")
    primary_key = primary_keys[0]
    table_rules = TRANSITIONS.get(table.name)
    if table_rules is None:
        raise ValueError(f"no transition table for {table.name!r}")
    from_list = list(from_statuses)
    for from_status in from_list:
        allowed = table_rules.get(_member(table_rules, from_status))
        if allowed is None or _member(table_rules, to) not in allowed:
            raise ValueError(f"illegal {table.name} transition {from_status} -> {to}")
    await _protect_transition(conn, table.name, id_, to)
    result = await conn.execute(
        table.update()
        .where(primary_key == id_, table.c.status.in_(from_list))
        .values(status=to)
        .returning(primary_key)
    )
    if result.rowcount == 0:
        raise GatewayError(code, 409)


def _member(table: dict, value: str):
    for member in table:
        if member.value == value:
            return member
    return None


# A PAUSED row is accepted only after W4 has verified the corresponding
# same-attempt PREPARED fence ACK. This module performs no HTTP and cannot prove
# that the caller holds the real customer advisory lock or has a genuine ACK.
FENCE_AT = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class ExecutionIdentity:
    """Identity captured before sending HTTP; never rebuild it from a callback."""

    customer_id: str
    plan_id: str
    origin: Literal["agent", "app_form"]
    attempt: int
    revision: int

    def __post_init__(self) -> None:
        if self.origin not in {"agent", "app_form"} or any(
            type(value) is not int or value < 1 for value in (self.attempt, self.revision)
        ):
            raise ValueError("invalid execution identity")
        if any(
            not isinstance(value, str) or not value for value in (self.customer_id, self.plan_id)
        ):
            raise ValueError("empty execution identity")


def _conflict() -> None:
    raise GatewayError(Code.STATE_CHANGED, 409)


def _transaction(conn: AsyncConnection) -> None:
    if not conn.in_transaction():
        raise ValueError("execution CAS requires the caller's active short transaction")


async def _locked_execution(
    conn: AsyncConnection, expected: ExecutionIdentity, status: ExecutionJobStatus
) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]:
    _transaction(conn)
    if not isinstance(expected, ExecutionIdentity):
        raise ValueError("regular execution requires a revision-bound ExecutionIdentity")
    # All consumers must keep this lock order, on the same customer-lock conn.
    plan = (
        (
            await conn.execute(
                select(t.plans)
                .where(
                    t.plans.c.id == expected.plan_id,
                    t.plans.c.customer_id == expected.customer_id,
                    t.plans.c.origin == expected.origin,
                )
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if plan is None:
        _conflict()
    job = (
        (
            await conn.execute(
                select(t.execution_jobs)
                .where(
                    t.execution_jobs.c.plan_id == expected.plan_id,
                    t.execution_jobs.c.l3_attempt == expected.attempt,
                    t.execution_jobs.c.status == status,
                )
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    evaluation = (
        (
            await conn.execute(
                select(t.evaluations)
                .where(
                    t.evaluations.c.plan_id == expected.plan_id,
                )
                .order_by(t.evaluations.c.revision.desc())
                .limit(1)
            )
        )
        .mappings()
        .one_or_none()
    )
    if job is None or evaluation is None or evaluation["revision"] != expected.revision:
        _conflict()
    return plan, job, evaluation


async def _claims(conn: AsyncConnection, plan: Mapping[str, Any]) -> None:
    claims = (
        (
            await conn.execute(
                select(t.authorization_claims)
                .where(t.authorization_claims.c.plan_id == plan["id"])
                .order_by(t.authorization_claims.c.scope_id)
                .with_for_update()
            )
        )
        .mappings()
        .all()
    )
    scopes = plan["scope_ids"]
    if len(set(scopes)) != len(scopes) or {r["scope_id"] for r in claims} != set(scopes):
        _conflict()
    if any(r["status"] != "CLAIMED" for r in claims):
        _conflict()


def _stored_submission(plan: Mapping[str, Any], job: Mapping[str, Any]) -> InteractiveEvidence:
    if job["deadline_cap_wall"] is None or job["deadline_wall"] is None:
        _conflict()
    try:
        evidence = InteractiveEvidence.model_validate(job["authorization_evidence"])
    except ValidationError:
        _conflict()
    if (
        not plan["scope_ids"]
        or evidence.origin != plan["origin"]
        or evidence.plan_hash != plan["plan_hash"]
        or set(evidence.scope_ids) != set(plan["scope_ids"])
        or set(evidence.utterance_ids) != set(plan["utterance_ids"])
        or job["deadline_wall"] > job["deadline_cap_wall"]
    ):
        _conflict()
    return evidence


async def _job_update(
    conn: AsyncConnection,
    expected: ExecutionIdentity,
    from_status: ExecutionJobStatus,
    **values: Any,
) -> None:
    latest = (
        select(func.max(t.evaluations.c.revision))
        .where(t.evaluations.c.plan_id == expected.plan_id)
        .scalar_subquery()
    )
    changed = await conn.execute(
        t.execution_jobs.update()
        .where(
            t.execution_jobs.c.plan_id == expected.plan_id,
            t.execution_jobs.c.status == from_status,
            t.execution_jobs.c.l3_attempt == expected.attempt,
            latest == expected.revision,
        )
        .values(**values, updated_at_wall=func.now())
        .returning(t.execution_jobs.c.plan_id)
    )
    if changed.scalar_one_or_none() is None:
        _conflict()


async def begin_fencing(conn: AsyncConnection, *, expected: ExecutionIdentity) -> None:
    """W4 first proves explicit STATE_CHANGED after successful prepare.

    Does not validate that HTTP claim. Checks only the original persisted job,
    owner, current evaluation and claims. Caller owns commit and customer lock.
    """
    plan, job, _ = await _locked_execution(conn, expected, ExecutionJobStatus.EXECUTING)
    if plan["status"] != PlanStatus.EXECUTING:
        _conflict()
    _stored_submission(plan, job)
    await _claims(conn, plan)
    await _job_update(conn, expected, ExecutionJobStatus.EXECUTING, status="FENCING")


async def acknowledge_fence(conn: AsyncConnection, *, expected: ExecutionIdentity) -> None:
    """W4 must verify the captured same-attempt PREPARED ACK before calling.

    Local PAUSED data alone is never evidence of an actual L3 fence. ACK loss or
    uncertainty must use mark_execution_unknown instead.
    """
    plan, job, _ = await _locked_execution(conn, expected, ExecutionJobStatus.FENCING)
    if plan["status"] != PlanStatus.EXECUTING:
        _conflict()
    _stored_submission(plan, job)
    await _claims(conn, plan)
    await _job_update(
        conn, expected, ExecutionJobStatus.FENCING, status="PAUSED", deadline_wall=FENCE_AT
    )


async def guard_paused_execution(conn: AsyncConnection, *, expected: ExecutionIdentity) -> None:
    """Lock old latest before W3 appends evaluation in this same transaction.

    publish_paused_decision then receives an identity with the new latest
    revision. Job attempt and old evidence remain unchanged until resume.
    """
    plan, job, _ = await _locked_execution(conn, expected, ExecutionJobStatus.PAUSED)
    if job["deadline_wall"] != FENCE_AT or plan["status"] not in {
        PlanStatus.EXECUTING,
        PlanStatus.EVALUATED,
        PlanStatus.CONFIRMED,
        PlanStatus.PENDING_APPROVAL,
        PlanStatus.DENIED,
        PlanStatus.REPLAN,
    }:
        _conflict()
    _stored_submission(plan, job)
    await _claims(conn, plan)


def _time(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("execution times require a timezone")


async def _approval(
    conn: AsyncConnection,
    plan: Mapping[str, Any],
    evaluation: Mapping[str, Any],
    approval_id: str,
    status: ApprovalStatus,
    now: datetime,
) -> Mapping[str, Any]:
    approval = (
        (
            await conn.execute(
                select(t.approvals).where(t.approvals.c.id == approval_id).with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if (
        approval is None
        or any(
            approval[key] != value
            for key, value in {
                "plan_id": plan["id"],
                "customer_id": plan["customer_id"],
                "sid": plan["sid"],
                "plan_hash": plan["plan_hash"],
                "status": status,
                "view_hash": evaluation["view_hash"],
            }.items()
        )
        or approval["expires_at_wall"] <= now
    ):
        _conflict()
    approved = (
        (
            await conn.execute(
                select(t.evaluations).where(
                    t.evaluations.c.id == approval["evaluation_id"],
                    t.evaluations.c.plan_id == plan["id"],
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if (
        approved is None
        or approved["view_hash"] != evaluation["view_hash"]
        or (approved["id"] != evaluation["id"] and evaluation["equivalent_to"] != approved["id"])
    ):
        _conflict()
    if status == ApprovalStatus.CONFIRMED and (
        approval["confirmed_at_wall"] is None or approval["confirmed_at_wall"] > now
    ):
        _conflict()
    return approval


async def publish_paused_decision(
    conn: AsyncConnection,
    *,
    expected: ExecutionIdentity,
    to: PlanStatus,
    approval_id: str | None = None,
    now: datetime,
) -> None:
    """Publish W3's new latest revision within the guarded append transaction.

    Job/evidence/attempt remain paused. Caller verifies deterministic evaluation,
    real authorization, approval MAC and customer lock; no HTTP occurs here.
    """
    _time(now)
    await guard_paused_execution(conn, expected=expected)
    plan, job, evaluation = await _locked_execution(conn, expected, ExecutionJobStatus.PAUSED)
    old_evidence = _stored_submission(plan, job)
    old_revision = await conn.scalar(
        select(t.evaluations.c.revision).where(
            t.evaluations.c.id == old_evidence.evaluation_id,
            t.evaluations.c.plan_id == expected.plan_id,
        )
    )
    if old_revision is None or evaluation["revision"] <= old_revision:
        _conflict()
    if to in {PlanStatus.EVALUATED, PlanStatus.CONFIRMED, PlanStatus.PENDING_APPROVAL} and (
        now >= job["deadline_cap_wall"]
    ):
        _conflict()
    decisions = {
        PlanStatus.EVALUATED: "ALLOW",
        PlanStatus.CONFIRMED: "REQUIRE_APPROVAL",
        PlanStatus.PENDING_APPROVAL: "REQUIRE_APPROVAL",
        PlanStatus.DENIED: "DENY",
        PlanStatus.REPLAN: "REPLAN",
    }
    if (
        to not in decisions
        or evaluation["decision"].get("decision") != decisions[to]
        or (evaluation["view_json"] is None or evaluation["view_hash"] is None)
    ):
        _conflict()
    if to in {PlanStatus.PENDING_APPROVAL, PlanStatus.CONFIRMED}:
        if approval_id is None:
            _conflict()
        await _approval(
            conn,
            plan,
            evaluation,
            approval_id,
            ApprovalStatus.PENDING
            if to == PlanStatus.PENDING_APPROVAL
            else ApprovalStatus.CONFIRMED,
            now,
        )
    elif approval_id is not None or (to == PlanStatus.EVALUATED and evaluation["risk"] != "GREEN"):
        _conflict()
    await conn.execute(t.plans.update().where(t.plans.c.id == expected.plan_id).values(status=to))


async def resume_paused_execution(
    conn: AsyncConnection,
    *,
    expected: ExecutionIdentity,
    authorization_evidence: InteractiveEvidence,
    deadline_wall: datetime,
    deadline_cap_wall: datetime,
    approval_id: str | None,
    now: datetime,
) -> int:
    """Persist n+1 before W4 sends HTTP, returning that new attempt.

    Caller must recheck live scope/epoch/revocation, approval MAC, snapshot and
    actual customer lock on this connection. A passed model or local PAUSED row
    cannot prove those facts. No internal commit, connection change or HTTP.
    """
    for value in (now, deadline_wall, deadline_cap_wall):
        _time(value)
    await guard_paused_execution(conn, expected=expected)
    plan, job, evaluation = await _locked_execution(conn, expected, ExecutionJobStatus.PAUSED)
    old = _stored_submission(plan, job)
    try:
        evidence = InteractiveEvidence.model_validate(
            authorization_evidence.model_dump(mode="json")
        )
    except (ValidationError, AttributeError):
        _conflict()
    if (
        deadline_cap_wall != job["deadline_cap_wall"]
        or not now < deadline_wall <= deadline_cap_wall
        or evaluation["view_json"] is None
        or evidence.evaluation_id != evaluation["id"]
        or evidence.plan_hash != plan["plan_hash"]
        or evidence.origin != plan["origin"]
        or set(evidence.scope_ids) != set(plan["scope_ids"])
        or len(evidence.scope_ids) != len(plan["scope_ids"])
        or set(evidence.utterance_ids) != set(plan["utterance_ids"])
        or len(evidence.utterance_ids) != len(plan["utterance_ids"])
        or evidence.authorization_epoch != old.authorization_epoch
        or evidence.final_view_hash != evaluation["view_hash"]
        or evidence.risk != evaluation["risk"]
        or evidence.approval_id != approval_id
    ):
        _conflict()
    if approval_id is None:
        if (
            plan["status"] != PlanStatus.EVALUATED
            or evaluation["risk"] != "GREEN"
            or evaluation["decision"].get("decision") != "ALLOW"
            or evidence.approved_evaluation_id is not None
            or evidence.confirmed_view_hash is not None
            or evidence.confirmed_at_wall is not None
        ):
            _conflict()
    else:
        if plan["status"] != PlanStatus.CONFIRMED or (
            evaluation["decision"].get("decision") != "REQUIRE_APPROVAL"
        ):
            _conflict()
        approval = await _approval(
            conn, plan, evaluation, approval_id, ApprovalStatus.CONFIRMED, now
        )
        if (
            deadline_wall > approval["expires_at_wall"]
            or evidence.approved_evaluation_id != approval["evaluation_id"]
            or evidence.confirmed_view_hash != approval["view_hash"]
            or evidence.confirmed_at_wall != approval["confirmed_at_wall"]
        ):
            _conflict()
    await _job_update(
        conn,
        expected,
        ExecutionJobStatus.PAUSED,
        status="EXECUTING",
        l3_attempt=expected.attempt + 1,
        authorization_evidence=evidence.model_dump(mode="json"),
        deadline_wall=deadline_wall,
        last_error=None,
    )
    await conn.execute(
        t.plans.update().where(t.plans.c.id == expected.plan_id).values(status="EXECUTING")
    )
    return expected.attempt + 1


LIVE_JOB_STATUSES = frozenset(
    {
        ExecutionJobStatus.EXECUTING,
        ExecutionJobStatus.FENCING,
        ExecutionJobStatus.PAUSED,
        ExecutionJobStatus.UNKNOWN,
    }
)


async def _protect_transition(conn: AsyncConnection, table_name: str, id_: str, to: str) -> None:
    """Ordinary DML cannot fake a barrier, archive a job or free its claim."""
    if table_name == "execution_jobs" and to in {"EXECUTED", "FAILED", "CANCELLED"}:
        _conflict()  # Only archive_execution_terminal checks the caller's L3 proof.
    if table_name == "execution_jobs":
        plan_id = id_
    elif table_name == "authorization_claims" and to == "CONSUMED":
        plan_id = await conn.scalar(
            select(t.authorization_claims.c.plan_id).where(t.authorization_claims.c.scope_id == id_)
        )
    elif table_name == "utterances" and to == "CONSUMED":
        plan_id = await conn.scalar(
            select(t.authorization_claims.c.plan_id).where(t.authorization_claims.c.scope_id == id_)
        )
    elif table_name == "plans" and to in {
        "EXECUTING",
        "EXECUTED",
        "FAILED",
        "DENIED",
        "REPLAN",
        "REJECTED",
        "EXPIRED",
        "SUPERSEDED",
    }:
        plan_id = id_
    else:
        return
    if plan_id is None:
        return
    await conn.execute(select(t.plans.c.id).where(t.plans.c.id == plan_id).with_for_update())
    job_status = await conn.scalar(
        select(t.execution_jobs.c.status)
        .where(t.execution_jobs.c.plan_id == plan_id)
        .with_for_update()
    )
    if job_status is not None and (
        table_name in {"authorization_claims", "utterances"}
        or (table_name == "plans" and job_status in LIVE_JOB_STATUSES)
    ):
        _conflict()


async def mark_execution_unknown(
    conn: AsyncConnection,
    *,
    expected: ExecutionIdentity,
    from_status: ExecutionJobStatus,
    reason: dict[str, Any],
) -> None:
    """Cancel/revoke/timeout requires reconciliation, retaining every claim.

    Works even for legacy jobs lacking evidence/cap: they may be reconciled but
    can never be resubmitted. W4 performs HTTP reconciliation after commit.
    """
    if from_status not in LIVE_JOB_STATUSES:
        _conflict()
    plan, _, _ = await _locked_execution(conn, expected, from_status)
    if plan["status"] in {PlanStatus.EXECUTED, PlanStatus.FAILED}:
        _conflict()
    await _job_update(conn, expected, from_status, status="UNKNOWN", last_error=reason)
    await conn.execute(
        t.plans.update().where(t.plans.c.id == expected.plan_id).values(status="UNKNOWN")
    )


async def archive_execution_terminal(
    conn: AsyncConnection,
    *,
    expected: ExecutionIdentity,
    from_status: ExecutionJobStatus,
    l3_status: Literal["COMMITTED", "CANCELLED"],
    result: ExecutionResult | None,
) -> None:
    """Archive a W4-verified L3 terminal response, then consume its claims.

    A local model does not prove a true L3 terminal response. W4 must verify the
    response belongs to the captured customer/plan/attempt before invoking this
    operation. CANCELLED tombstones legitimately have result=None.
    """
    if from_status not in LIVE_JOB_STATUSES or l3_status not in {"COMMITTED", "CANCELLED"}:
        _conflict()
    plan, _, _ = await _locked_execution(conn, expected, from_status)
    await _claims(conn, plan)
    terminal, result = _terminal_result(expected.plan_id, l3_status, result)
    if plan["status"] in {PlanStatus.EXECUTED, PlanStatus.FAILED}:
        _conflict()
    await _job_update(
        conn,
        expected,
        from_status,
        status=terminal,
        result=result.model_dump(mode="json") if result else None,
    )
    await _finish_terminal(conn, plan, terminal)


@dataclass(frozen=True)
class OrphanExecutionIdentity:
    """Cleanup-only identity: explicitly expects no evaluation for this plan.

    Never use this type for prepare, fence, replay or resume. Missing evaluation
    data is not authorization, including when evidence/cap survived migration.
    """

    customer_id: str
    plan_id: str
    origin: Literal["agent", "app_form"]
    attempt: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.origin, str)
            or self.origin not in {"agent", "app_form"}
            or type(self.attempt) is not int
            or self.attempt < 1
        ):
            raise ValueError("invalid orphan execution identity")
        if any(
            not isinstance(value, str) or not value for value in (self.customer_id, self.plan_id)
        ):
            raise ValueError("empty orphan execution identity")


async def _locked_orphan_execution(
    conn: AsyncConnection,
    expected: OrphanExecutionIdentity,
    status: ExecutionJobStatus,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    _transaction(conn)
    if not isinstance(expected, OrphanExecutionIdentity):
        raise ValueError("orphan cleanup requires OrphanExecutionIdentity")
    plan = (
        (
            await conn.execute(
                select(t.plans)
                .where(
                    t.plans.c.id == expected.plan_id,
                    t.plans.c.customer_id == expected.customer_id,
                    t.plans.c.origin == expected.origin,
                )
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if plan is None:
        _conflict()
    job = (
        (
            await conn.execute(
                select(t.execution_jobs)
                .where(
                    t.execution_jobs.c.plan_id == expected.plan_id,
                    t.execution_jobs.c.l3_attempt == expected.attempt,
                    t.execution_jobs.c.status == status,
                )
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    has_evaluation = await conn.scalar(
        select(
            select(t.evaluations.c.id).where(t.evaluations.c.plan_id == expected.plan_id).exists()
        )
    )
    if job is None or has_evaluation:
        _conflict()
    scopes = plan["scope_ids"]
    if not isinstance(scopes, list) or any(
        not isinstance(scope, str) or not scope for scope in scopes
    ):
        _conflict()
    # Keep the existing strict duplicate/ownership/status checks. This orphan-
    # only shape guard makes malformed legacy JSON a controlled 409, not a
    # set()/iteration TypeError, and never repairs missing authorization data.
    await _claims(conn, plan)
    return plan, job


async def _orphan_job_update(
    conn: AsyncConnection,
    expected: OrphanExecutionIdentity,
    from_status: ExecutionJobStatus,
    **values: Any,
) -> None:
    # Keep absence in the actual CAS, not just the preceding read. The same
    # plan row lock serializes compliant evaluation writers; they must recheck
    # current status after waiting, never append a replay to an UNKNOWN job.
    no_evaluation = (
        ~select(t.evaluations.c.id).where(t.evaluations.c.plan_id == expected.plan_id).exists()
    )
    owner = select(t.plans.c.id).where(
        t.plans.c.id == expected.plan_id,
        t.plans.c.customer_id == expected.customer_id,
        t.plans.c.origin == expected.origin,
    )
    changed = await conn.execute(
        t.execution_jobs.update()
        .where(
            t.execution_jobs.c.plan_id.in_(owner),
            t.execution_jobs.c.status == from_status,
            t.execution_jobs.c.l3_attempt == expected.attempt,
            no_evaluation,
        )
        .values(**values, updated_at_wall=func.now())
        .returning(t.execution_jobs.c.plan_id)
    )
    if changed.scalar_one_or_none() is None:
        _conflict()


async def mark_orphan_execution_unknown(
    conn: AsyncConnection,
    *,
    expected: OrphanExecutionIdentity,
    from_status: ExecutionJobStatus,
    reason: dict,
) -> None:
    """Persist cleanup work for a legacy job with explicitly absent evaluation.

    W1 isolates expected CAS conflicts in a savepoint so revocation is not
    rolled back. W4 proves actual customer-lock ownership and reconciles only
    after commit. Evidence, cap, deadline, attempt and claims remain untouched.
    """
    if from_status not in LIVE_JOB_STATUSES:
        _conflict()
    plan, _ = await _locked_orphan_execution(conn, expected, from_status)
    if plan["status"] in {PlanStatus.EXECUTED, PlanStatus.FAILED}:
        _conflict()
    await _orphan_job_update(conn, expected, from_status, status="UNKNOWN", last_error=reason)
    await conn.execute(
        t.plans.update().where(t.plans.c.id == expected.plan_id).values(status="UNKNOWN")
    )


def _terminal_result(
    plan_id: str,
    l3_status: Literal["COMMITTED", "CANCELLED"],
    result: ExecutionResult | None,
) -> tuple[str, ExecutionResult | None]:
    """Shared result validation; the caller must prove true L3 authenticity."""
    if l3_status not in {"COMMITTED", "CANCELLED"}:
        _conflict()
    if result is not None:
        try:
            result = ExecutionResult.model_validate(result.model_dump(mode="json"))
        except (ValidationError, AttributeError):
            _conflict()
        if result.plan_id != plan_id:
            _conflict()
    if l3_status == "COMMITTED":
        if (
            result is None
            or result.status != "EXECUTED"
            or result.committed_at is None
            or any(step.status != "EXECUTED" for step in result.steps)
        ):
            _conflict()
        terminal = "EXECUTED"
    else:
        if result is not None and (
            result.status != "FAILED" or any(step.status == "EXECUTED" for step in result.steps)
        ):
            _conflict()
        terminal = "FAILED"
    return terminal, result


async def _finish_terminal(
    conn: AsyncConnection,
    plan: Mapping[str, Any],
    terminal: str,
) -> None:
    """Called only after guarded terminal CAS with the claims locked."""
    await conn.execute(t.plans.update().where(t.plans.c.id == plan["id"]).values(status=terminal))
    await conn.execute(
        t.authorization_claims.update()
        .where(
            t.authorization_claims.c.plan_id == plan["id"],
            t.authorization_claims.c.status == "CLAIMED",
        )
        .values(status="CONSUMED")
    )
    # Revoked/CANCELLED/SUPERSEDED originals remain revoked, including when a
    # genuine COMMITTED result wins a race against cancellation.
    await conn.execute(
        t.utterances.update()
        .where(
            t.utterances.c.id.in_(plan["utterance_ids"]),
            t.utterances.c.status == "ACTIVE",
        )
        .values(status="CONSUMED", consumed_by=plan["id"])
    )


async def archive_orphan_execution_terminal(
    conn: AsyncConnection,
    *,
    expected: OrphanExecutionIdentity,
    l3_status: Literal["COMMITTED", "CANCELLED"],
    result: ExecutionResult | None,
) -> None:
    """Archive a W4-verified terminal only for an UNKNOWN orphan job/plan.

    Missing evaluation/evidence never permits a new submission. W4 proves the
    response's authenticity, captured identity and actual customer lock. If an
    evaluation appears, discard the orphan update and obtain a fresh ordinary
    identity and fresh L3 query/reconcile; never attach a revision to old ACKs.
    """
    plan, _ = await _locked_orphan_execution(conn, expected, ExecutionJobStatus.UNKNOWN)
    if plan["status"] != PlanStatus.UNKNOWN:
        _conflict()
    terminal, result = _terminal_result(expected.plan_id, l3_status, result)
    await _orphan_job_update(
        conn,
        expected,
        ExecutionJobStatus.UNKNOWN,
        status=terminal,
        result=result.model_dump(mode="json") if result else None,
    )
    await _finish_terminal(conn, plan, terminal)
