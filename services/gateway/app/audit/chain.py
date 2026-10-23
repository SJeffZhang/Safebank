"""Per-customer audit hash chain (PRD §7.12).

Every append runs in its own small transaction: take
``pg_advisory_xact_lock(hashtext('audit'), hashtext(customer_id))``, read the
latest ``prev_hash`` for the customer, then insert the new event with
``hash = sha256(prev_hash || canonical(event_without_hash))``. The
``UNIQUE(customer_id, prev_hash)`` constraint is a second guard against forks.

The chain detects tampering; it does not claim resistance to an administrator
who can rewrite the whole database.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from .. import models as m


def canonical(event_without_hash: dict[str, Any]) -> bytes:
    return json.dumps(
        event_without_hash, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()


def digest(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(payload)).hexdigest()


def chain_hash(prev_hash: str, event_without_hash: dict[str, Any]) -> str:
    return hashlib.sha256(prev_hash.encode() + canonical(event_without_hash)).hexdigest()


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
    created_at_wall: Any = None,
) -> dict[str, str]:
    """Append one audit event and return its id and hash."""
    await _lock(conn, customer_id)
    prev_hash = await conn.scalar(
        select(m.audit_events.c.hash)
        .where(m.audit_events.c.customer_id == customer_id)
        .order_by(m.audit_events.c.seq.desc())
        .limit(1)
    )
    if prev_hash is None:
        prev_hash = "0" * 64
    if created_at_wall is None:
        created_at_wall = text("now()")
    event = {
        "id": event_id,
        "customer_id": customer_id,
        "trace_id": trace_id,
        "event_type": event_type,
        "payload_digest": digest(payload),
        "payload": payload,
        "prev_hash": prev_hash,
        "created_at_wall": None,
    }
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
            created_at_wall=created_at_wall,
        )
    )
    return {"id": event_id, "hash": hash_value}


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
        event = {
            "id": row["id"],
            "customer_id": row["customer_id"],
            "trace_id": row["trace_id"],
            "event_type": row["event_type"],
            "payload_digest": row["payload_digest"],
            "payload": row["payload"],
            "prev_hash": row["prev_hash"],
            "created_at_wall": None,
        }
        if row["prev_hash"] != prev_hash:
            return (
                False,
                f"broken link at {row['id']}: prev_hash {row['prev_hash']!r} != {prev_hash!r}",
            )
        if chain_hash(prev_hash, event) != row["hash"]:
            return False, f"tampered content at {row['id']}"
        prev_hash = row["hash"]
    return True, f"{len(rows)} events"
