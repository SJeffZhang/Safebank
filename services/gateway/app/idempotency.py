"""Persistent route-scoped request idempotency (PRD §9.1).

The in-memory ``IdempotencyStore`` is gone; gateway request_id semantics are now
backed by the ``request_dedup`` table. ``status_code`` uses ``0`` as the
"in-flight" sentinel, so a concurrent same-id request observes RUN_IN_PROGRESS
instead of duplicating the work.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from safebank_contracts.codes import Code
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from . import tables as t
from .errors import GatewayError
from .middleware import digest_hex

IN_FLIGHT = 0


async def request_idempotent(
    conn: AsyncConnection,
    *,
    customer_id: str,
    route: str,
    request_id: str,
    body: Any,
    produce: Callable[[], Awaitable[tuple[int, dict[str, Any]]]],
) -> tuple[int, dict[str, Any], bool]:
    """Run ``produce`` once per ``(customer_id, route, request_id)``.

    Returns ``(status_code, response, replayed)``. A different body under the
    same id raises 409 IDEMPOTENCY_CONFLICT; a concurrent in-flight id raises
    409 RUN_IN_PROGRESS.
    """
    digest = digest_hex(body)
    inserted = await conn.execute(
        pg_insert(t.request_dedup)
        .values(
            customer_id=customer_id,
            route=route,
            request_id=request_id,
            request_hash=digest,
            status_code=IN_FLIGHT,
            response={},
            created_at_wall=text("now()"),
        )
        .on_conflict_do_nothing()
    )
    if inserted.rowcount == 1:
        status, response = await produce()
        await conn.execute(
            update(t.request_dedup)
            .where(
                t.request_dedup.c.customer_id == customer_id,
                t.request_dedup.c.route == route,
                t.request_dedup.c.request_id == request_id,
            )
            .values(status_code=status, response=response)
        )
        return status, response, False

    row = (
        (
            await conn.execute(
                select(t.request_dedup).where(
                    t.request_dedup.c.customer_id == customer_id,
                    t.request_dedup.c.route == route,
                    t.request_dedup.c.request_id == request_id,
                )
            )
        )
        .mappings()
        .one()
    )
    if row["request_hash"] != digest:
        raise GatewayError(Code.IDEMPOTENCY_CONFLICT, 409)
    if row["status_code"] == IN_FLIGHT:
        raise GatewayError(Code.RUN_IN_PROGRESS, 409)
    return row["status_code"], row["response"], True
