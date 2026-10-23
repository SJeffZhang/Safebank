"""Customer-lock protected cancellation and supersession; state is the sole writer."""

from safebank_contracts.codes import Code
from sqlalchemy import select, update

from .. import state
from .. import tables as t
from ..errors import GatewayError
from ..schemas.enums import ExecutionJobStatus


async def cancel_clarifications(conn, sid: str, *, status: str = "CANCELLED") -> None:
    rows = (
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
    for id_ in rows:
        await state.transition(
            conn, t.pending_clarifications, id_=id_, from_statuses=["OPEN"], to=status
        )


async def supersede(conn, sid: str, *, keep: tuple[str, ...] = ()) -> None:
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
    replaced = False
    for row in rows:
        if row["normalized_scope"].get("action_kinds"):
            replaced = True
            await state.transition(
                conn, t.utterances, id_=row["id"], from_statuses=["ACTIVE"], to="SUPERSEDED"
            )
    if replaced:
        await _invalidate_plans(conn, sid)


async def cancel_session(conn, sid: str, now) -> None:
    await conn.execute(
        update(t.sessions)
        .where(t.sessions.c.id == sid)
        .values(authorization_epoch=t.sessions.c.authorization_epoch + 1)
    )
    rows = (
        (
            await conn.execute(
                select(t.utterances.c.id).where(
                    t.utterances.c.sid == sid,
                    t.utterances.c.status == "ACTIVE",
                )
            )
        )
        .scalars()
        .all()
    )
    for id_ in rows:
        await state.transition(
            conn, t.utterances, id_=id_, from_statuses=["ACTIVE"], to="CANCELLED"
        )
    await _invalidate_pending(conn, sid, now)


async def supersede_session(conn, sid: str, now) -> None:
    """Explicit correction invalidates the previous instruction before parsing."""
    await supersede(conn, sid)
    await _invalidate_pending(conn, sid, now)


async def _invalidate_plans(conn, sid: str) -> None:
    rows = (
        (await conn.execute(select(t.plans).where(t.plans.c.sid == sid).order_by(t.plans.c.id)))
        .mappings()
        .all()
    )
    for row in rows:
        job = (
            (
                await conn.execute(
                    select(t.execution_jobs).where(t.execution_jobs.c.plan_id == row["id"])
                )
            )
            .mappings()
            .one_or_none()
        )
        if job is not None and job["status"] in state.LIVE_JOB_STATUSES:
            revision = await conn.scalar(
                select(t.evaluations.c.revision)
                .where(t.evaluations.c.plan_id == row["id"])
                .order_by(t.evaluations.c.revision.desc())
                .limit(1)
            )
            if revision is None:
                # Explicit blocker: the shared absent-evaluation CAS is pending.
                # No fabricated revision or direct DML may bypass shared guards.
                raise GatewayError(Code.STATE_CHANGED, 409)
            await state.mark_execution_unknown(
                conn,
                expected=state.ExecutionIdentity(
                    row["customer_id"], row["id"], row["origin"], job["l3_attempt"], revision
                ),
                from_status=ExecutionJobStatus(job["status"]),
                reason={"code": Code.AUTHORIZATION_CANCELLED.value},
            )
        elif state.legal("plans", row["status"], "SUPERSEDED"):
            await state.transition(
                conn, t.plans, id_=row["id"], from_statuses=[row["status"]], to="SUPERSEDED"
            )
    rows = (
        (
            await conn.execute(
                select(t.approvals).where(
                    t.approvals.c.sid == sid,
                    t.approvals.c.status.in_(["PENDING", "CONFIRMED"]),
                )
            )
        )
        .mappings()
        .all()
    )
    for row in rows:
        await state.transition(
            conn, t.approvals, id_=row["id"], from_statuses=[row["status"]], to="SUPERSEDED"
        )


async def _invalidate_pending(conn, sid: str, now) -> None:
    await _invalidate_plans(conn, sid)
    await cancel_clarifications(conn, sid)
    # These tables have no status column. Epoch cancellation invalidates all old UI sources.
    for table in (t.form_actions, t.hints):
        await conn.execute(
            update(table)
            .where(table.c.sid == sid, table.c.used_by.is_(None))
            .values(expires_at_wall=now)
        )
