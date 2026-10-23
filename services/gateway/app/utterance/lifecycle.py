"""Customer-lock protected revocation; state remains the sole lifecycle writer."""

from dataclasses import dataclass

from safebank_contracts.codes import Code
from sqlalchemy import select, update

from .. import state
from .. import tables as t
from ..errors import GatewayError
from ..schemas.enums import ExecutionJobStatus


@dataclass(frozen=True)
class Revocation:
    utterance_ids: tuple[str, ...] = ()
    pending_plan_ids: tuple[str, ...] = ()


def deferred_error() -> GatewayError:
    return GatewayError(
        Code.STATE_CHANGED,
        409,
        message="Original write authorization is revoked; old execution cleanup is pending",
        details={"authorization_revoked": True, "cleanup_pending": True},
    )


def raise_cached_error(status: int, result: dict) -> None:
    if status >= 400:
        error = result["error"]
        raise GatewayError(
            Code(error["code"]), status, message=error["message"], details=error["details"]
        )


async def _transition_pending(conn, table, id_: str, statuses: tuple[str, ...], to: str):
    # A compliant writer shares our customer lock; bounded rereads also tolerate
    # legacy direct SQL writers. Never absorb SQL/connection/programming faults.
    for _ in range(3):
        status = await conn.scalar(
            select(table.c.status).where(table.c.id == id_).with_for_update()
        )
        if status not in statuses:
            return
        try:
            async with conn.begin_nested():
                await state.transition(conn, table, id_=id_, from_statuses=[status], to=to)
            return
        except GatewayError as exc:
            if exc.code is not Code.STATE_CHANGED or exc.status != 409:
                raise
    raise GatewayError(Code.STATE_CHANGED, 409)


async def cancel_clarifications(conn, sid: str, *, status: str = "CANCELLED") -> None:
    ids = (
        (
            await conn.execute(
                select(t.pending_clarifications.c.id).where(
                    t.pending_clarifications.c.sid == sid,
                    t.pending_clarifications.c.status == "OPEN",
                )
            )
        )
        .scalars()
        .all()
    )
    for id_ in ids:
        await _transition_pending(conn, t.pending_clarifications, id_, ("OPEN",), status)


async def supersede(conn, sid: str, *, keep: tuple[str, ...] = ()) -> Revocation:
    rows = (
        (
            await conn.execute(
                select(t.utterances).where(
                    t.utterances.c.sid == sid,
                    t.utterances.c.status == "ACTIVE",
                    t.utterances.c.id.not_in(keep),
                )
            )
        )
        .mappings()
        .all()
    )
    ids = []
    for row in rows:
        if row["normalized_scope"].get("action_kinds"):
            await state.transition(
                conn, t.utterances, id_=row["id"], from_statuses=["ACTIVE"], to="SUPERSEDED"
            )
            ids.append(row["id"])
    # Recheck even after an earlier request has already superseded every root.
    pending = await _invalidate_plans(conn, sid)
    return Revocation(tuple(ids), pending)


async def revoke_session(conn, sid: str) -> tuple[str, ...]:
    """Authorization phase, separated so G14 can revoke every sid before cleanup."""
    await conn.execute(
        update(t.sessions)
        .where(t.sessions.c.id == sid)
        .values(authorization_epoch=t.sessions.c.authorization_epoch + 1)
    )
    ids = (
        (
            await conn.execute(
                select(t.utterances.c.id).where(
                    t.utterances.c.sid == sid, t.utterances.c.status == "ACTIVE"
                )
            )
        )
        .scalars()
        .all()
    )
    for id_ in ids:
        await state.transition(
            conn, t.utterances, id_=id_, from_statuses=["ACTIVE"], to="CANCELLED"
        )
    return tuple(ids)


async def cancel_session(conn, sid: str, now) -> Revocation:
    ids = await revoke_session(conn, sid)
    return Revocation(ids, await cleanup_session(conn, sid, now))


async def supersede_session(conn, sid: str, now) -> Revocation:
    """Explicit correction invalidates the previous instruction before parsing."""
    result = await supersede(conn, sid)
    await invalidate_ui(conn, sid, now)
    return result


async def _plan_snapshot(conn, plan_id: str):
    plan = (
        (await conn.execute(select(t.plans).where(t.plans.c.id == plan_id)))
        .mappings()
        .one_or_none()
    )
    if plan is None:
        return None, None, None
    job = (
        (await conn.execute(select(t.execution_jobs).where(t.execution_jobs.c.plan_id == plan_id)))
        .mappings()
        .one_or_none()
    )
    revision = await conn.scalar(
        select(t.evaluations.c.revision)
        .where(t.evaluations.c.plan_id == plan_id)
        .order_by(t.evaluations.c.revision.desc())
        .limit(1)
    )
    return plan, job, revision


async def _invalidate_plan(conn, plan_id: str) -> bool:
    for _ in range(3):
        before = await _plan_snapshot(conn, plan_id)
        plan, job, revision = before
        if plan is None:
            return False
        try:
            async with conn.begin_nested():
                if job is not None and job["status"] in state.LIVE_JOB_STATUSES:
                    arguments = {
                        "from_status": ExecutionJobStatus(job["status"]),
                        "reason": {"code": Code.AUTHORIZATION_CANCELLED.value},
                    }
                    identity = (plan["customer_id"], plan["id"], plan["origin"], job["l3_attempt"])
                    if revision is None:
                        await state.mark_orphan_execution_unknown(
                            conn, expected=state.OrphanExecutionIdentity(*identity), **arguments
                        )
                    else:
                        await state.mark_execution_unknown(
                            conn, expected=state.ExecutionIdentity(*identity, revision), **arguments
                        )
                elif state.legal("plans", plan["status"], "SUPERSEDED"):
                    await state.transition(
                        conn, t.plans, id_=plan_id, from_statuses=[plan["status"]], to="SUPERSEDED"
                    )
            return False
        except GatewayError as exc:
            if exc.code is not Code.STATE_CHANGED or exc.status != 409:
                raise
            if await _plan_snapshot(conn, plan_id) == before:
                return True
            # Retry with a fresh identity, never add a revision to an old HTTP ACK.
    return True


async def _invalidate_plans(conn, sid: str) -> tuple[str, ...]:
    ids = (
        (
            await conn.execute(
                select(t.plans.c.id).where(t.plans.c.sid == sid).order_by(t.plans.c.id)
            )
        )
        .scalars()
        .all()
    )
    pending = []
    for plan_id in ids:
        if await _invalidate_plan(conn, plan_id):
            pending.append(plan_id)
    approvals = (
        (
            await conn.execute(
                select(t.approvals.c.id).where(
                    t.approvals.c.sid == sid, t.approvals.c.status.in_(["PENDING", "CONFIRMED"])
                )
            )
        )
        .scalars()
        .all()
    )
    for id_ in approvals:
        await _transition_pending(conn, t.approvals, id_, ("PENDING", "CONFIRMED"), "SUPERSEDED")
    return tuple(pending)


async def invalidate_ui(conn, sid: str, now) -> None:
    await cancel_clarifications(conn, sid)
    for table in (t.form_actions, t.hints):
        await conn.execute(
            update(table)
            .where(table.c.sid == sid, table.c.used_by.is_(None))
            .values(expires_at_wall=now)
        )


async def cleanup_session(conn, sid: str, now) -> tuple[str, ...]:
    pending = await _invalidate_plans(conn, sid)
    await invalidate_ui(conn, sid, now)
    return pending
