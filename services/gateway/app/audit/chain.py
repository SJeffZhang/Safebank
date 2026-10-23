"""Per-customer audit hash chain (PRD §7.12).

Append takes ``pg_advisory_xact_lock(hashtext('audit'), hashtext(customer_id))``,
reads the latest ``prev_hash`` for the customer, then inserts with
``hash = sha256(prev_hash || canonical(event_without_hash))``. The hash covers
the whole event except ``hash``/``seq`` — including ``created_at_wall`` (an
RFC3339 string from the injected clock), so a tampered timestamp is detected.
``UNIQUE(customer_id, prev_hash)`` is a second guard against forks.

Use :func:`append_tx` for ordinary appends: it opens and commits its own small
transaction. Never call :func:`append` inside a transaction that also makes an
L3 HTTP call — the advisory lock would be held for the whole HTTP round trip.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from .. import tables as m


def utcnow() -> datetime:
    return datetime.now(UTC)


def canonical(event_without_hash: dict[str, Any]) -> bytes:
    return json.dumps(
        event_without_hash, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()


def digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(payload)).hexdigest()


def chain_hash(prev_hash: str, event_without_hash: dict[str, Any]) -> str:
    return hashlib.sha256(prev_hash.encode() + canonical(event_without_hash)).hexdigest()


def _event(
    *,
    event_id: str,
    customer_id: str,
    trace_id: str | None,
    event_type: str,
    payload: dict[str, Any],
    prev_hash: str,
    plan_id: str | None,
    created_at_wall: datetime,
) -> dict[str, Any]:
    return {
        "id": event_id,
        "customer_id": customer_id,
        "trace_id": trace_id,
        "event_type": event_type,
        "payload_digest": digest(payload),
        "payload": payload,
        "prev_hash": prev_hash,
        "plan_id": plan_id,
        "created_at_wall": created_at_wall.isoformat(),
    }


async def _lock(conn: AsyncConnection, customer_id: str) -> None:
    await conn.execute(
        text("SELECT pg_advisory_xact_lock(hashtext('audit'), hashtext(:cid))"),
        {"cid": customer_id},
    )


async def append(
    conn: AsyncConnection,
    *,
    customer_id: str,
    event_type: str,
    payload: dict[str, Any],
    event_id: str,
    trace_id: str | None = None,
    plan_id: str | None = None,
    now_wall: datetime | None = None,
) -> dict[str, str]:
    """Append one audit event inside the caller's transaction."""
    await _lock(conn, customer_id)
    prev_hash = await conn.scalar(
        select(m.audit_events.c.hash)
        .where(m.audit_events.c.customer_id == customer_id)
        .order_by(m.audit_events.c.seq.desc())
        .limit(1)
    )
    if prev_hash is None:
        prev_hash = "0" * 64
    now_wall = now_wall or utcnow()
    event = _event(
        event_id=event_id,
        customer_id=customer_id,
        trace_id=trace_id,
        event_type=event_type,
        payload=payload,
        prev_hash=prev_hash,
        plan_id=plan_id,
        created_at_wall=now_wall,
    )
    hash_value = chain_hash(prev_hash, event)
    await conn.execute(
        m.audit_events.insert().values(
            id=event_id,
            customer_id=customer_id,
            trace_id=trace_id,
            event_type=event_type,
            payload_digest=event["payload_digest"],
            payload=payload,
            prev_hash=prev_hash,
            hash=hash_value,
            plan_id=plan_id,
            created_at_wall=now_wall,
        )
    )
    return {"id": event_id, "hash": hash_value}


async def append_tx(
    engine: AsyncEngine,
    *,
    customer_id: str,
    event_type: str,
    payload: dict[str, Any],
    event_id: str,
    trace_id: str | None = None,
    plan_id: str | None = None,
    now_wall: datetime | None = None,
) -> dict[str, str]:
    """Append in its own small transaction (the default call path)."""
    async with engine.begin() as conn:
        return await append(
            conn,
            customer_id=customer_id,
            event_type=event_type,
            payload=payload,
            event_id=event_id,
            trace_id=trace_id,
            plan_id=plan_id,
            now_wall=now_wall,
        )


async def verify_chain(conn: AsyncConnection, customer_id: str) -> tuple[bool, str]:
    """Recompute the chain for a customer; return (ok, detail)."""
    rows = (
        (
            await conn.execute(
                select(m.audit_events)
                .where(m.audit_events.c.customer_id == customer_id)
                .order_by(m.audit_events.c.seq)
            )
        )
        .mappings()
        .all()
    )
    if not rows:
        return True, "empty"
    prev_hash = "0" * 64
    for row in rows:
        event = _event(
            event_id=row["id"],
            customer_id=row["customer_id"],
            trace_id=row["trace_id"],
            event_type=row["event_type"],
            payload=row["payload"],
            prev_hash=row["prev_hash"],
            plan_id=row["plan_id"],
            created_at_wall=row["created_at_wall"],
        )
        if row["prev_hash"] != prev_hash:
            return (
                False,
                f"broken link at {row['id']}: prev_hash {row['prev_hash']!r} != {prev_hash!r}",
            )
        if chain_hash(prev_hash, event) != row["hash"]:
            return False, f"tampered content at {row['id']}"
        prev_hash = row["hash"]
    return True, f"{len(rows)} events"
