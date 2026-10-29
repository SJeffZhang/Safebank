"""S1 (W5): scheduler callback, the only internal business route.

``POST /internal/scheduled/authorize`` (PRD §9.5a). Called by the L3 scheduler on
bank-net :8201 with ``SCHEDULER_SERVICE_TOKEN`` (caller kind S, no JWT). S1 never
trusts callback amounts: it re-fetches the L3 event definition and re-evaluates
the transfer on a fresh snapshot before authorizing execution.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel
from safebank_contracts.codes import Code
from sqlalchemy import select

from .. import tables as t
from ..errors import GatewayError
from ..l3.client import L3Error
from ..schemas.enums import ServiceKind

router = APIRouter()


class AuthorizeRequest(BaseModel):
    event_id: str
    request_id: str | None = None
    attempt: int = 1


@router.post("/internal/scheduled/authorize")
async def scheduled_authorize(request: Request, body: AuthorizeRequest) -> dict[str, Any]:
    caller = getattr(request.state, "caller", None)
    if caller is None or caller.kind is not ServiceKind.S:
        raise GatewayError(Code.UNAUTHENTICATED, 401)

    app = request.app.state
    db = app.db
    l3 = app.l3

    # Step 1: a terminal decision for this event is idempotently replayed without
    # re-evaluation and without touching L3.
    async with db.connect() as conn:
        row = (
            await conn.execute(
                select(t.event_decisions).where(t.event_decisions.c.event_id == body.event_id)
            )
        ).first()
    if row is not None:
        return {"event_id": body.event_id, "status": row.status, "code": row.code}

    # Step 2: fetch the L3 event definition (never trust callback amounts). Any
    # L3 transport failure is retryable: report RETRYING and persist nothing.
    try:
        event = await l3.event(body.event_id)
    except L3Error:
        return {
            "event_id": body.event_id,
            "status": "RETRYING",
            "code": Code.DEPENDENCY_UNAVAILABLE.value,
        }

    # Steps 3–5 (grant lookup/backfill, re-evaluation, block/execute, persist)
    # land in follow-up commits.
    raise GatewayError(
        Code.DEPENDENCY_UNAVAILABLE, 503, message="S1 grant handling not yet implemented"
    )
