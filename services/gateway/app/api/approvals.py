"""G6/G7/G9/G10/G17: customer-bound confirmation and OTP."""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import Field, StrictBool
from safebank_contracts.base import Contract
from safebank_contracts.codes import Code
from safebank_contracts.tools import Id
from sqlalchemy import select

from .. import tables as t
from ..approval import service
from ..audit import chain as audit
from ..errors import GatewayError
from ..idempotency import request_idempotent
from ..locks import customer_lock
from ..schemas.enums import ApprovalStatus
from ..state import transition

router = APIRouter()


class ViewRequest(Contract):
    request_id: Id
    view_hash: Annotated[str, Field(min_length=64, max_length=64)]


class ConfirmRequest(ViewRequest):
    otp: Annotated[str, Field(pattern=r"^[0-9]{6}$")] | None = None
    risk_ack: StrictBool
    disclosure_token: str | None = None


def resources(request):
    db = getattr(request.app.state, "db", None)
    clock = getattr(request.app.state, "wall_now", None)
    if db is None or clock is None:
        raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503)
    return db, clock


@router.get("/approvals")
async def list_approvals(
    request: Request,
    status: str = "PENDING",
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    db, _ = resources(request)
    if status != "ALL" and status not in set(ApprovalStatus):
        raise GatewayError(Code.CONTRACT_INVALID, 422)
    query = (
        select(t.approvals, t.evaluations.c.risk)
        .join(t.evaluations, t.evaluations.c.id == t.approvals.c.evaluation_id)
        .where(t.approvals.c.customer_id == request.state.caller.customer_id)
    )
    if status != "ALL":
        query = query.where(t.approvals.c.status == status)
    if cursor:
        query = query.where(t.approvals.c.id > cursor)
    async with db.connect() as conn:
        rows = (
            (await conn.execute(query.order_by(t.approvals.c.id).limit(limit + 1))).mappings().all()
        )
    return dict(
        items=[
            dict(
                approval_id=r["id"],
                plan_id=r["plan_id"],
                session_id=r["sid"],
                status=r["status"],
                risk=r["risk"],
                expires_at_wall=r["expires_at_wall"].isoformat(),
            )
            for r in rows[:limit]
        ],
        next_cursor=rows[limit - 1]["id"] if len(rows) > limit else None,
    )


@router.get("/approvals/{approval_id}")
async def approval_panel(approval_id: str, request: Request):
    db, _ = resources(request)
    async with db.connect() as conn:
        row, plan, evaluation = await service.load(
            conn, approval_id, request.state.caller.customer_id
        )
        view = service.stored_view(evaluation)
    result = view.model_copy(
        update=dict(
            approval_id=row["id"],
            status=plan["status"],
            expires_at_wall=row["expires_at_wall"].isoformat(),
        )
    ).model_dump(mode="json")
    result["disclosure_token"] = service.disclosure_token(row, view)
    return result


async def mutate(request, approval_id, body, operation):
    db, clock = resources(request)
    caller = request.state.caller
    now = clock()
    event = None
    async with customer_lock([caller.customer_id]) as conn:
        async with conn.begin():

            async def produce():
                nonlocal event
                row, plan, evaluation = await service.load(
                    conn, approval_id, caller.customer_id, locked=True
                )
                if row["view_hash"] != body.view_hash:
                    raise GatewayError(Code.VIEW_HASH_MISMATCH, 409)
                view = service.stored_view(evaluation)
                session = (
                    (
                        await conn.execute(
                            select(t.sessions).where(
                                t.sessions.c.id == plan["sid"],
                                t.sessions.c.customer_id == caller.customer_id,
                                t.sessions.c.revoked_at_wall.is_(None),
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if session is None:
                    raise GatewayError(Code.AUTHORIZATION_CANCELLED, 409)
                if operation == "otp":
                    result = await service.send_otp(conn, row, view, now)
                elif operation == "confirm":
                    result = await service.confirm(
                        conn,
                        row,
                        plan,
                        view,
                        now,
                        otp=body.otp,
                        risk_ack=body.risk_ack,
                        token=body.disclosure_token,
                    )
                else:
                    result = await service.pending(conn, row, now)
                    if result is None:
                        await transition(
                            conn,
                            t.approvals,
                            id_=row["id"],
                            from_statuses=["PENDING"],
                            to="REJECTED",
                        )
                        await transition(
                            conn,
                            t.plans,
                            id_=plan["id"],
                            from_statuses=[plan["status"]],
                            to="REJECTED",
                        )
                        result = dict(approval_id=row["id"], plan_id=plan["id"], status="REJECTED")
                if isinstance(result, GatewayError):
                    return result.status, result.body(getattr(request.state, "trace_id", ""))
                if operation != "otp":
                    event = (
                        "APPROVAL_CONFIRMED" if operation == "confirm" else "APPROVAL_REJECTED",
                        plan["id"],
                    )
                return 200, result

            status, response, replayed = await request_idempotent(
                conn,
                customer_id=caller.customer_id,
                route=f"/approvals/{approval_id}/{operation}",
                request_id=body.request_id,
                body=body.model_dump(mode="json"),
                produce=produce,
            )
        if event and not replayed:
            await audit.append_tx(
                db,
                customer_id=caller.customer_id,
                event_type=event[0],
                payload=dict(approval_id=approval_id, view_hash=body.view_hash),
                event_id="evt_" + secrets.token_hex(16),
                plan_id=event[1],
                now_wall=now,
            )
    return JSONResponse(status_code=status, content=response)


@router.post("/approvals/{approval_id}/confirm")
async def confirm(approval_id: str, body: ConfirmRequest, request: Request):
    return await mutate(request, approval_id, body, "confirm")


@router.post("/approvals/{approval_id}/reject")
async def reject(approval_id: str, body: ViewRequest, request: Request):
    return await mutate(request, approval_id, body, "reject")


@router.post("/approvals/{approval_id}/otp")
async def otp(approval_id: str, body: ViewRequest, request: Request):
    return await mutate(request, approval_id, body, "otp")
