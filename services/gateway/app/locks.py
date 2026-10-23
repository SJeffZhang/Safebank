"""Customer execution lock (W4 implements; W5 reuses).

PRD §7.8: the executor takes a dedicated ``lock_conn``, sets
``lock_timeout='5s'``, then takes advisory locks for all involved customer IDs
(sorted) and performs every gateway write on that connection in small
transactions. Timeout -> 409 EXECUTION_IN_PROGRESS.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncConnection


@asynccontextmanager
async def customer_lock(customer_ids: Iterable[str]) -> AsyncIterator[AsyncConnection]:
    """Acquire sorted customer advisory locks and yield the ``lock_conn``.

    W4 implements; the signature is fixed so W5's S1 and demo entry points can
    reuse the same lock.
    """
    raise NotImplementedError("customer_lock is W4 (PRD §7.8)")
    yield  # pragma: no cover - unreachable
