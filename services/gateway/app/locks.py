"""Dedicated, bounded customer locks shared by G8, S1 and demo (PRD §7.8)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from weakref import WeakKeyDictionary

from safebank_contracts.codes import Code
from sqlalchemy import Connection, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from .errors import GatewayError

_engine: AsyncEngine | None = None
_slots: asyncio.Semaphore | None = None
_max_executions: int | None = None
_requests = 0
_OWNER_RECORD_KEY = "safebank.gateway.customer_lock.owner"


def configure(db: AsyncEngine, max_executions: int) -> None:
    """Bind the centrally owned engine and semaphore; do not create another pool."""
    if type(max_executions) is not int or max_executions < 1:
        raise ValueError("max_executions must be a positive integer")
    global _engine, _slots, _max_executions
    if db is _engine and max_executions == _max_executions and _slots is not None:
        return
    if _requests:
        raise RuntimeError("customer lock requests are active or waiting; cannot reconfigure")
    # Validate the complete replacement before touching globals or installing
    # anything on the candidate pool. Waiting requests count as users too.
    pool = db.sync_engine.pool
    new_slots = asyncio.Semaphore(max_executions)
    # dispose() recreates a pool with inherited listeners. SQLAlchemy's event
    # registry keys the original target by id(), which can later be reused by
    # an unrelated pool. Only this pool's actual dispatcher proves protection.
    if _premature_return not in pool.dispatch.checkin:
        # One stable dispatcher per pool lifetime: never mutate this shared
        # listener collection while another checkin may be suspended in I/O.
        event.listen(pool, "checkin", _premature_return)
    _engine, _slots, _max_executions = db, new_slots, max_executions


def _customer_ids(values: Iterable[str]) -> list[str]:
    if isinstance(values, (str, bytes)):
        raise ValueError("customer_ids must be a collection of nonempty IDs")
    ids = list(values)
    if not ids or any(
        not isinstance(cid, str) or not cid.strip() or len(cid) > 256 or "\x00" in cid
        for cid in ids
    ):
        raise ValueError("customer_ids must be nonempty strings of at most 256 characters")
    return sorted(set(ids))


class LockOwnershipLost(GatewayError):
    """This owner is permanently invalid; caller must use UNKNOWN/reconcile.

    Never reacquire a connection and continue this attempt. A pre-send guard
    proves current ownership only; it cannot eliminate the check-to-send gap.
    """

    def __init__(self) -> None:
        super().__init__(Code.DEPENDENCY_UNAVAILABLE, 503, "customer lock ownership lost")


@dataclass
class _Owner:
    dbapi: Any
    driver: Any
    pid: int
    ids: tuple[str, ...]
    record_info: dict[str, Any]
    reported: bool = False
    active: bool = True
    held: bool = False
    lost: bool = False


_owners: WeakKeyDictionary[AsyncConnection, _Owner] = WeakKeyDictionary()


def _physical_lost(conn: Connection, owner: _Owner) -> bool:
    # Inspect original objects before touching Connection.connection: that
    # property can reconnect after rollback. The original asyncpg driver never
    # becomes live again, even if SQLAlchemy checks out another DBAPI session.
    return (
        conn.closed
        or conn.invalidated
        or owner.driver.is_closed()
        or conn.connection.dbapi_connection is not owner.dbapi
    )


def _check(conn: Connection, owner: _Owner) -> None:
    if not owner.active or owner.lost or _physical_lost(conn, owner):
        owner.lost = True
        owner.reported = True
        raise LockOwnershipLost()


def _listen(conn: Connection, owner: _Owner) -> list[tuple[str, Any]]:
    def transaction_guard(connection: Connection) -> None:
        _check(connection, owner)

    def statement_guard(connection: Connection, *_args: Any) -> None:
        _check(connection, owner)

    listeners = [
        ("begin", transaction_guard),
        ("commit", transaction_guard),
        ("before_cursor_execute", statement_guard),
    ]
    for name, callback in listeners:
        event.listen(conn, name, callback)
    return listeners


def _premature_return(dbapi: Any, record: Any) -> None:
    # AsyncConnection.close() has no ConnectionEvents hook. Checkin is the
    # final point before this physical backend is lent to another borrower.
    # record_info survives physical invalidation, unlike record.info.
    owner = record.record_info.get(_OWNER_RECORD_KEY)
    if owner is not None and owner.active and (dbapi is None or dbapi is owner.dbapi):
        owner.lost = True
        record.record_info.pop(_OWNER_RECORD_KEY, None)
        # None means SQLAlchemy already discarded the physical backend. Never
        # invalidate a replacement belonging to another owner or the whole pool.
        if dbapi is not None:
            record.invalidate()


async def _discard(conn: AsyncConnection) -> None:
    if not conn.closed:
        await conn.invalidate()


async def assert_lock_owner(conn: AsyncConnection) -> None:
    """Round-trip on the ORIGINAL session before a new external submission.

    This helper does not replace sticky statement/commit guards. It must be
    called while this context is open and must not be caught to reacquire/retry
    submission. A failure after a request was sent belongs to UNKNOWN/reconcile.
    It leaves a caller transaction intact, or opens/closes its own read-only
    short transaction when called between transactions.
    """
    owner = _owners.get(conn)
    if owner is None or not owner.held:
        raise LockOwnershipLost()
    _check(conn.sync_connection, owner)

    async def verify() -> None:
        row = (
            (
                await conn.execute(
                    text("""
            WITH keys AS (
                SELECT hashtextextended('cus:' || cid, 0) AS key
                FROM unnest(CAST(:ids AS text[])) AS cid
            )
            SELECT pg_backend_pid() AS pid, NOT EXISTS (
                SELECT 1 FROM keys WHERE NOT EXISTS (
                    SELECT 1 FROM pg_locks l
                    WHERE l.locktype='advisory' AND l.granted
                    AND l.pid=pg_backend_pid() AND l.objsubid=1
                    AND l.classid=((keys.key >> 32) & 4294967295)::oid
                    AND l.objid=(keys.key & 4294967295)::oid
                )
            ) AS held
        """),
                    {"ids": list(owner.ids)},
                )
            )
            .mappings()
            .one()
        )
        if row["pid"] != owner.pid or not row["held"]:
            owner.lost = True
            owner.reported = True
            raise LockOwnershipLost()

    try:
        if conn.in_transaction():
            await verify()
        else:
            async with conn.begin():
                await verify()
    except DBAPIError as exc:
        owner.lost = True
        owner.reported = True
        await _discard(conn)
        raise LockOwnershipLost() from exc


@asynccontextmanager
async def customer_lock(customer_ids: Iterable[str]) -> AsyncIterator[AsyncConnection]:
    """Hold sorted customer locks across short transactions/transaction-free HTTP.

    Transport loss is normalized to LockOwnershipLost (503); lock contention is
    EXECUTION_IN_PROGRESS (409). Other SQL errors retain their original type.
    Rollback cannot clear owner.lost. Every instance listener is detached before
    closing; uncertain connections are invalidated instead of returning to pool.
    """
    ids = _customer_ids(customer_ids)
    db, slots = _engine, _slots
    if db is None or slots is None:
        raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503)
    global _requests
    # No await between capturing resources and registering their use. The count
    # spans semaphore/pool queues and cleanup, not just yielded lock owners.
    _requests += 1
    try:
        async with slots:
            async with db.connect() as conn:
                previous_timeout = None
                owner = None
                listeners = []
                failed = False
                try:
                    raw = await conn.get_raw_connection()
                    async with conn.begin():
                        previous_timeout = await conn.scalar(text("SHOW lock_timeout"))
                        await conn.execute(text("SET lock_timeout = '5s'"))
                        pid = await conn.scalar(text("SELECT pg_backend_pid()"))
                    owner = _Owner(
                        raw.dbapi_connection,
                        raw.driver_connection,
                        pid,
                        tuple(ids),
                        raw.record_info,
                    )
                    _owners[conn] = owner
                    listeners = _listen(conn.sync_connection, owner)
                    owner.record_info[_OWNER_RECORD_KEY] = owner
                    for cid in ids:
                        try:
                            async with conn.begin():
                                await conn.execute(
                                    text(
                                        "SELECT pg_advisory_lock("
                                        "hashtextextended('cus:' || :cid, 0))"
                                    ),
                                    {"cid": cid},
                                )
                        except DBAPIError as exc:
                            if getattr(exc.orig, "sqlstate", None) == "55P03":
                                raise GatewayError(Code.EXECUTION_IN_PROGRESS, 409) from exc
                            raise
                    owner.held = True
                    yield conn
                except BaseException as exc:
                    failed = True
                    if isinstance(exc, DBAPIError) and (
                        exc.connection_invalidated
                        or (owner and _physical_lost(conn.sync_connection, owner))
                    ):
                        if owner:
                            owner.lost = True
                            owner.reported = True
                        raise LockOwnershipLost() from exc
                    raise
                finally:
                    try:
                        physical_loss = owner and _physical_lost(conn.sync_connection, owner)
                        if (
                            conn.closed
                            or conn.invalidated
                            or (owner and (owner.lost or physical_loss))
                        ):
                            first_report = owner is not None and not owner.reported
                            if owner:
                                owner.lost = True
                            await _discard(conn)
                            if first_report and not failed:
                                owner.reported = True
                                raise LockOwnershipLost()
                        else:
                            if conn.in_transaction():
                                await conn.rollback()
                            async with conn.begin():
                                await conn.execute(text("SELECT pg_advisory_unlock_all()"))
                                if previous_timeout is not None:
                                    await conn.execute(
                                        text("SELECT set_config('lock_timeout', :value, false)"),
                                        {"value": previous_timeout},
                                    )
                    except BaseException as cleanup_error:
                        disconnected = isinstance(cleanup_error, DBAPIError) and (
                            cleanup_error.connection_invalidated
                            or (owner is not None and _physical_lost(conn.sync_connection, owner))
                        )
                        if owner:
                            owner.lost = True
                        # Always try to discard, but never let a secondary disposal
                        # failure replace a pending business error/cancellation.
                        discard_error = None
                        try:
                            await _discard(conn)
                        except BaseException as error:
                            discard_error = error
                        if isinstance(cleanup_error, asyncio.CancelledError):
                            raise
                        if isinstance(discard_error, asyncio.CancelledError):
                            raise discard_error from cleanup_error
                        if not failed:
                            if disconnected:
                                if owner:
                                    owner.reported = True
                                raise LockOwnershipLost() from cleanup_error
                            # A healthy-session SQL/programming failure keeps its
                            # original type, even though this connection is discarded.
                            raise
                        # An exception is already propagating out of the body.
                        # Cleanup transport/SQL/disposal failures must not mask it.
                    finally:
                        if owner:
                            owner.active = False
                            if owner.record_info.get(_OWNER_RECORD_KEY) is owner:
                                owner.record_info.pop(_OWNER_RECORD_KEY)
                        for name, callback in listeners:
                            event.remove(conn.sync_connection, name, callback)
    finally:
        _requests -= 1
