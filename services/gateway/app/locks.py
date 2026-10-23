"""Bounded, dedicated customer advisory locks shared by G8, S1 and demo."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager

from safebank_contracts.codes import Code
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from .errors import GatewayError

_engine: AsyncEngine | None = None
_slots: asyncio.Semaphore | None = None


def configure(db: AsyncEngine, max_executions: int) -> None:
    """Bind the centrally owned engine at application startup; no new pool."""
    if max_executions < 1:
        raise ValueError("max_executions must be positive")
    global _engine, _slots
    _engine, _slots = db, asyncio.Semaphore(max_executions)


@asynccontextmanager
async def customer_lock(customer_ids: Iterable[str]) -> AsyncIterator[AsyncConnection]:
    """Hold sorted session locks over small transactions and transaction-free HTTP."""
    ids = sorted(set(customer_ids))
    if not ids or any(not isinstance(id_, str) or not id_ for id_ in ids):
        raise ValueError("customer_ids must be nonempty strings")
    if _engine is None or _slots is None:
        raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503)
    async with _slots:
        async with _engine.connect() as conn:
            acquired = []
            try:
                async with conn.begin():
                    await conn.execute(text("SET lock_timeout = '5s'"))
                for cid in ids:
                    try:
                        async with conn.begin():
                            await conn.execute(
                                text(
                                    "SELECT pg_advisory_lock(hashtext('execution'), hashtext(:cid))"
                                ),
                                {"cid": cid},
                            )
                        acquired.append(cid)
                    except DBAPIError as exc:
                        if getattr(exc.orig, "sqlstate", None) == "55P03":
                            raise GatewayError(Code.EXECUTION_IN_PROGRESS, 409) from exc
                        raise
                yield conn
            finally:
                try:
                    if conn.in_transaction():
                        await conn.rollback()
                    async with conn.begin():
                        for cid in reversed(acquired):
                            await conn.execute(
                                text(
                                    "SELECT pg_advisory_unlock("
                                    "hashtext('execution'), hashtext(:cid))"
                                ),
                                {"cid": cid},
                            )
                        await conn.execute(text("RESET lock_timeout"))
                except BaseException:
                    await conn.invalidate()
                    raise
