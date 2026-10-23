"""Customer-lock protected cancellation and supersession; state is the sole writer."""

from sqlalchemy import select, update

from .. import state
from .. import tables as t


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
    for row in rows:
        if row["normalized_scope"].get("action_kinds"):
            await state.transition(
                conn, t.utterances, id_=row["id"], from_statuses=["ACTIVE"], to="SUPERSEDED"
            )


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


async def _invalidate_pending(conn, sid: str, now) -> None:
    # Executing and UNKNOWN submissions retain their true state and durable claim.
    rows = (await conn.execute(select(t.plans).where(t.plans.c.sid == sid))).mappings().all()
    for row in rows:
        if state.legal("plans", row["status"], "SUPERSEDED"):
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
    await cancel_clarifications(conn, sid)
    # These tables have no status column. Epoch cancellation invalidates all old UI sources.
    for table in (t.form_actions, t.hints):
        await conn.execute(
            update(table)
            .where(table.c.sid == sid, table.c.used_by.is_(None))
            .values(expires_at_wall=now)
        )
