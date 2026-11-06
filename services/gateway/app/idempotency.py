"""Persistent route-scoped request idempotency (PRD §9.1).

``status_code`` uses ``0`` as the "in-flight" sentinel, so a concurrent same-id
request observes 409 EXECUTION_IN_PROGRESS instead of duplicating the work.

The INSERT, ``produce()`` and the final UPDATE must all run in the SAME caller
transaction; on ``produce`` failure the in-flight row is deleted so the same
request_id can be retried. Do NOT put an L3/HTTP call inside this transaction —
G8's execution idempotency goes through ``plans`` / ``execution_jobs`` instead.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from safebank_contracts.codes import Code
from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
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
    409 EXECUTION_IN_PROGRESS.
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
        try:
            status, response = await produce()
        except BaseException:
            # A database error inside produce() leaves the transaction aborted;
            # the in-flight row then disappears with the rollback anyway, and a
            # failing cleanup must not replace the original error.
            try:
                await conn.execute(
                    delete(t.request_dedup).where(
                        t.request_dedup.c.customer_id == customer_id,
                        t.request_dedup.c.route == route,
                        t.request_dedup.c.request_id == request_id,
                    )
                )
            except SQLAlchemyError:
                pass
            raise
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
        raise GatewayError(Code.EXECUTION_IN_PROGRESS, 409)
    return row["status_code"], row["response"], True
