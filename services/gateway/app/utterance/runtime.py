"""W1 adapters to the gateway's shared request resources; never create pools."""

from __future__ import annotations

from datetime import datetime

from fastapi import Request
from safebank_contracts.codes import Code
from sqlalchemy import select

from .. import tables as t
from ..auth.tokens import check_revocation
from ..errors import GatewayError
from ..schemas.enums import ServiceKind


def db(request: Request):
    value = getattr(request.app.state, "db", None)
    if value is None:
        raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503)
    return value


def l3(request: Request):
    value = getattr(request.app.state, "l3", None)
    if value is None:
        raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503)
    return value


def wall_now(request: Request) -> datetime:
    clock = getattr(request.app.state, "wall_now", None)
    if not callable(clock):
        raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503)
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503)
    return value


def private_key(request: Request) -> bytes:
    value = getattr(request.app.state, "jwt_private_key", None)
    if not isinstance(value, bytes) or not value:
        raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503, message="signing key unavailable")
    return value


async def session_for(conn, sid: str, caller, now: datetime, *, lock: bool = False):
    await check_revocation(conn, caller, now=now)
    if caller.kind is ServiceKind.A and caller.sid != sid:
        raise GatewayError(Code.NOT_FOUND, 404)
    query = select(t.sessions).where(
        t.sessions.c.id == sid, t.sessions.c.customer_id == caller.customer_id
    )
    if lock:
        query = query.with_for_update()
    row = (await conn.execute(query)).mappings().one_or_none()
    if row is None:
        raise GatewayError(Code.NOT_FOUND, 404)
    if row["revoked_at_wall"] is not None:
        raise GatewayError(Code.AUTHORIZATION_CANCELLED, 409)
    return row


def require_database(request: Request) -> None:
    """Reject an unwired deployment before FastAPI accepts a mutating body."""
    db(request)
