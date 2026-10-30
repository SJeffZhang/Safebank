"""G11 / G12 / G13 / G16 (W5): audit and notification read endpoints.

All endpoints require F+app JWT and only ever read the caller's own rows.
Audit pages by the append sequence (newest first); notifications by id.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from safebank_contracts.codes import Code
from sqlalchemy import func, select

from .. import tables as t
from ..audit import chain
from ..errors import GatewayError
from ..idempotency import request_idempotent
from ..utterance.service import new_id

router = APIRouter()

# ── Sensitive-key patterns to strip from audit payloads ───────────────────

_SENSITIVE_KEY_SUBSTRINGS = (
    "ciphertext",
    "code_digest",
    "otp",
    "token",
    "secret",
    "password",
    "pin",
    "private_key",
    "hmac_key",
    "binding_mac",
)


def _redact_sensitive(data: Any) -> Any:
    """Recursively strip sensitive keys from an audit payload."""
    if isinstance(data, dict):
        return {
            k: _redact_sensitive(v)
            for k, v in data.items()
            if not any(pat in k.lower() for pat in _SENSITIVE_KEY_SUBSTRINGS)
        }
    if isinstance(data, list):
        return [_redact_sensitive(v) for v in data]
    return data


# ── Response models ───────────────────────────────────────────────────────


class AuditView(BaseModel):
    id: str
    trace_id: str | None
    event_type: str
    plan_id: str | None = None
    step_id: str | None = None
    code: str | None = None
    message: str | None = None
    payload: Any
    prev_hash: str
    hash: str
    created_at_wall: str


class AuditListResponse(BaseModel):
    items: list[AuditView]
    next_cursor: str | None = None


class NotificationView(BaseModel):
    id: str
    type: str
    title: str
    body: str
    related_plan_id: str | None = None
    related_event_id: str | None = None
    created_at: str
    read_at_wall: str | None = None


class NotificationListResponse(BaseModel):
    items: list[NotificationView]
    next_cursor: str | None = None
    unread_count: int


class ReadNotificationRequest(BaseModel):
    request_id: str


class ReadNotificationResponse(BaseModel):
    id: str
    read_at_wall: str


# ── Helpers ───────────────────────────────────────────────────────────────


def _caller_customer(request: Request) -> str:
    caller = getattr(request.state, "caller", None)
    if caller is None or caller.customer_id is None:
        raise GatewayError(Code.UNAUTHENTICATED, 401)
    return caller.customer_id


def _seq_cursor(cursor: str | None) -> int | None:
    if cursor is None:
        return None
    if not cursor.isascii() or not cursor.isdecimal() or len(cursor) > 19:
        raise GatewayError(Code.CONTRACT_INVALID, 422, "invalid cursor")
    value = int(cursor)
    if value > 2**63 - 1:
        raise GatewayError(Code.CONTRACT_INVALID, 422, "invalid cursor")
    return value


def _wall(value: str | None, name: str) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None:
        raise GatewayError(Code.CONTRACT_INVALID, 422, f"{name} must be RFC3339 with offset")
    return parsed


async def _audit_page(conn, where, cursor: str | None, limit: int) -> dict[str, Any]:
    after = _seq_cursor(cursor)
    query = select(t.audit_events).where(*where)
    if after is not None:
        query = query.where(t.audit_events.c.seq < after)
    rows = (
        (await conn.execute(query.order_by(t.audit_events.c.seq.desc()).limit(limit)))
        .mappings()
        .all()
    )
    items = [_audit_row_to_view(r) for r in rows]
    next_cursor = str(rows[-1]["seq"]) if len(rows) == limit else None
    return AuditListResponse(items=items, next_cursor=next_cursor).model_dump(mode="json")


def _audit_row_to_view(row: dict[str, Any]) -> AuditView:
    payload = row.get("payload") or {}
    redacted = _redact_sensitive(payload)
    return AuditView(
        id=row["id"],
        trace_id=row.get("trace_id"),
        event_type=row["event_type"],
        plan_id=row.get("plan_id"),
        step_id=payload.get("step_id"),
        code=payload.get("code"),
        message=payload.get("message"),
        payload=redacted,
        prev_hash=row["prev_hash"],
        hash=row["hash"],
        created_at_wall=row["created_at_wall"].isoformat(),
    )


def _notification_row_to_view(row: dict[str, Any]) -> NotificationView:
    payload = row.get("payload") or {}
    ntype = row["type"]
    # Use payload fields if present; otherwise template-stich from type.
    title = payload.get("title") or ntype.replace("_", " ").title()
    body = payload.get("body") or ""
    return NotificationView(
        id=row["id"],
        type=ntype,
        title=title,
        body=body,
        related_plan_id=payload.get("related_plan_id"),
        related_event_id=payload.get("related_event_id"),
        created_at=row["created_at"].isoformat(),
        read_at_wall=row["read_at_wall"].isoformat() if row.get("read_at_wall") else None,
    )


# ── G11 GET /plans/{plan_id}/audit ────────────────────────────────────────


@router.get("/plans/{plan_id}/audit")
async def plan_audit(
    plan_id: str,
    request: Request,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
):
    """G11 — audit trail for a specific plan (customer-scoped)."""
    customer_id = _caller_customer(request)
    db = request.app.state.db

    async with db.connect() as conn:
        return await _audit_page(
            conn,
            (
                t.audit_events.c.customer_id == customer_id,
                t.audit_events.c.plan_id == plan_id,
            ),
            cursor,
            limit,
        )


# ── G12 GET /notifications ────────────────────────────────────────────────


@router.get("/notifications")
async def list_notifications(
    request: Request,
    unread_only: bool = Query(default=False),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
):
    """G12 — list notifications for the caller's customer."""
    customer_id = _caller_customer(request)
    db = request.app.state.db

    async with db.connect() as conn:
        # Base query: customer-scoped.
        base = select(t.notifications).where(t.notifications.c.customer_id == customer_id)

        if unread_only:
            base = base.where(t.notifications.c.read_at_wall.is_(None))

        # Unread count: always unfiltered (full customer count).
        unread_count = (
            await conn.scalar(
                select(func.count())
                .select_from(t.notifications)
                .where(
                    t.notifications.c.customer_id == customer_id,
                    t.notifications.c.read_at_wall.is_(None),
                )
            )
            or 0
        )

        if cursor:
            base = base.where(t.notifications.c.id < cursor)

        base = base.order_by(t.notifications.c.id.desc()).limit(limit)
        rows = (await conn.execute(base)).mappings().all()

    items = [_notification_row_to_view(r) for r in rows]
    next_cursor = items[-1].id if len(items) == limit else None
    return NotificationListResponse(
        items=items,
        next_cursor=next_cursor,
        unread_count=unread_count,
    ).model_dump(mode="json")


# ── G13 POST /notifications/{notification_id}/read ────────────────────────


@router.post("/notifications/{notification_id}/read")
async def mark_notification_read(
    notification_id: str,
    body: ReadNotificationRequest,
    request: Request,
):
    """G13 — mark a notification as read (idempotent)."""
    customer_id = _caller_customer(request)
    db = request.app.state.db
    now = datetime.now(UTC)
    route = f"/notifications/{notification_id}/read"

    async def produce():
        # Verify ownership: notification must belong to this customer.
        row = (
            (
                await conn.execute(
                    select(
                        t.notifications.c.id,
                        t.notifications.c.customer_id,
                        t.notifications.c.read_at_wall,
                    )
                    .where(t.notifications.c.id == notification_id)
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None or row["customer_id"] != customer_id:
            raise GatewayError(Code.OWNERSHIP_MISMATCH, 404, "notification not found")

        # If already read, return existing read_at_wall (idempotent).
        if row["read_at_wall"] is not None:
            read_ts = row["read_at_wall"].isoformat()
            return 200, {"id": notification_id, "read_at_wall": read_ts}

        # Mark as read.
        await conn.execute(
            t.notifications.update()
            .where(t.notifications.c.id == notification_id)
            .values(read_at_wall=now)
        )
        read_ts = now.isoformat()
        await chain.append(
            conn,
            customer_id=customer_id,
            event_type="NOTIFICATION_READ",
            payload={"notification_id": notification_id},
            event_id=new_id("evt"),
            now_wall=now,
        )
        return 200, {"id": notification_id, "read_at_wall": read_ts}

    async with db.begin() as conn:
        _status, response, _replayed = await request_idempotent(
            conn,
            customer_id=customer_id,
            route=route,
            request_id=body.request_id,
            body=body.model_dump(mode="json"),
            produce=produce,
        )

    return response


# ── G16 GET /audit ────────────────────────────────────────────────────────


@router.get("/audit")
async def customer_audit(
    request: Request,
    from_at_wall: str | None = Query(default=None),
    to_at_wall: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
):
    """G16 — all audit events for the caller's customer, with optional time range."""
    customer_id = _caller_customer(request)
    db = request.app.state.db

    start = _wall(from_at_wall, "from_at_wall")
    end = _wall(to_at_wall, "to_at_wall")
    where = [t.audit_events.c.customer_id == customer_id]
    if start is not None:
        where.append(t.audit_events.c.created_at_wall >= start)
    if end is not None:
        where.append(t.audit_events.c.created_at_wall <= end)
    async with db.connect() as conn:
        return await _audit_page(conn, where, cursor, limit)
