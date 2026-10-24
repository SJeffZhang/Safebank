"""G6 / G7 / G9 / G10 / G17 (W4): approvals, OTP, panel.

G9, G6, G7, G17 implemented in W4b2b. G10 reserved for W4b2c.
All write paths go through ``state.transition`` and ``audit.append_tx``.
Instance keys come from ``request.app.state`` — no global ``configure()``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from safebank_contracts.codes import Code
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from .. import state
from .. import tables as t
from ..approval.adapter import (
    approval_hmac_key as _adapter_key,
)
from ..approval.adapter import (
    profile as _adapter_profile,
)
from ..approval.adapter import (
    wall_now as _adapter_wall,
)
from ..approval.service import (
    ApprovalBinding,
    verify_binding,
)
from ..audit.chain import append_tx
from ..errors import GatewayError
from ..idempotency import request_idempotent
from ..schemas.enums import ApprovalStatus, PlanStatus
from ..utterance.service import new_id

router = APIRouter()


# ── Models ────────────────────────────────────────────────────────────────


class ConfirmRejectRequest(BaseModel):
    request_id: str
    view_hash: str


class ConfirmRequest(ConfirmRejectRequest):
    risk_ack: bool = False


class ApprovalListItem(BaseModel):
    approval_id: str
    plan_id: str
    session_id: str
    status: str
    risk: str
    expires_at_wall: str


class ListResponse(BaseModel):
    items: list[ApprovalListItem]
    next_cursor: str | None = None


# ── Shared helpers ─────────────────────────────────────────────────────────


def _now(request: Request) -> datetime:
    clock = _adapter_wall(request)
    now = clock()
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503, "wall clock unavailable")
    return now


async def _lock_approval(
    conn: AsyncConnection, approval_id: str, customer_id: str
) -> dict[str, Any]:
    """Lock and return the approval row, verifying customer ownership."""
    row = (
        (
            await conn.execute(
                select(t.approvals).where(t.approvals.c.id == approval_id).with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row["customer_id"] != customer_id:
        raise GatewayError(Code.OWNERSHIP_MISMATCH, 404, "approval not found")
    return row


async def _validate_confirmable(
    conn: AsyncConnection,
    approval: dict[str, Any],
    *,
    view_hash: str,
    now: datetime,
    request: Request,
) -> tuple[dict[str, Any], dict[str, Any], ApprovalBinding]:
    """Validate that an approval is confirmable/rejectable.

    Returns (plan_row, evaluation_row, binding). Raises GatewayError on any
    validation failure.
    """
    plan_id = approval["plan_id"]
    customer_id = approval["customer_id"]

    # Lock plan row.
    plan = (
        (
            await conn.execute(
                select(t.plans)
                .where(t.plans.c.id == plan_id, t.plans.c.customer_id == customer_id)
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if plan is None:
        raise GatewayError(Code.OWNERSHIP_MISMATCH, 404, "plan not found")

    # Lock evaluation row.
    evaluation = (
        (
            await conn.execute(
                select(t.evaluations)
                .where(
                    t.evaluations.c.id == approval["evaluation_id"],
                    t.evaluations.c.plan_id == plan_id,
                )
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if evaluation is None:
        raise GatewayError(Code.OWNERSHIP_MISMATCH, 404, "evaluation not found")

    # 1. Status must be PENDING.
    if approval["status"] != ApprovalStatus.PENDING.value:
        raise GatewayError(Code.STATE_CHANGED, 409, "approval is not pending")

    # 2. Not expired.
    if approval["expires_at_wall"] <= now:
        raise GatewayError(Code.APPROVAL_EXPIRED, 409)

    # 3. view_hash must match byte-for-byte.
    if approval["view_hash"] != view_hash:
        raise GatewayError(Code.VIEW_HASH_MISMATCH, 409)

    # 4. Session not revoked.
    session = (
        (
            await conn.execute(
                select(t.sessions.c.revoked_at_wall).where(t.sessions.c.id == approval["sid"])
            )
        )
        .mappings()
        .one_or_none()
    )
    if session is None or session["revoked_at_wall"] is not None:
        raise GatewayError(Code.AUTHORIZATION_CANCELLED, 409, "session has been revoked")

    # 5. Plan in PENDING_APPROVAL.
    if plan["status"] != PlanStatus.PENDING_APPROVAL.value:
        raise GatewayError(Code.STATE_CHANGED, 409, "plan is not pending approval")

    # 6. Verify binding MAC.
    key = _adapter_key(request)
    profile_str = _adapter_profile(request)
    binding = ApprovalBinding(
        customer_id=approval["customer_id"],
        sid=approval["sid"],
        plan_id=approval["plan_id"],
        plan_hash=approval["plan_hash"],
        evaluation_id=approval["evaluation_id"],
        versions=evaluation["versions"],
        clock_version=evaluation["clock_version"],
        policy_version=evaluation["policy_version"],
        profile=profile_str,
        view_hash=approval["view_hash"],
        expires_at_wall=approval["expires_at_wall"],
    )
    if not verify_binding(key, binding, approval["binding_mac"]):
        raise GatewayError(Code.APPROVAL_BINDING_MISMATCH, 409, "approval MAC does not verify")

    return plan, evaluation, binding


# ── G9 GET /approvals/{approval_id} ────────────────────────────────────────


@router.get("/approvals/{approval_id}")
async def get_approval_panel(approval_id: str, request: Request):
    """Return the approval panel (PlanView) for a given approval ID."""
    caller = getattr(request.state, "caller", None)
    if caller is None or caller.customer_id is None:
        raise GatewayError(Code.UNAUTHENTICATED, 401)

    db = request.app.state.db
    async with db.connect() as conn:
        approval = await _lock_approval(conn, approval_id, caller.customer_id)

        # LOCKED approvals are not readable (OTP locked).
        if approval["status"] == ApprovalStatus.LOCKED.value:
            raise GatewayError(Code.OTP_LOCKED, 409)

        evaluation = (
            (
                await conn.execute(
                    select(t.evaluations.c.view_json).where(
                        t.evaluations.c.id == approval["evaluation_id"],
                        t.evaluations.c.plan_id == approval["plan_id"],
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if evaluation is None or evaluation["view_json"] is None:
            raise GatewayError(Code.NOT_FOUND, 404, "panel not found")

    view = evaluation["view_json"]

    # D2: status and expires_at_wall are envelope fields (NOT in content hash
    # domain). Override them from the current approvals row so the panel always
    # reflects the real state.
    view["status"] = approval["status"]
    view["expires_at_wall"] = approval["expires_at_wall"].isoformat()

    return view


# ── G6 POST /approvals/{approval_id}/confirm ───────────────────────────────


@router.post("/approvals/{approval_id}/confirm")
async def confirm_approval(
    approval_id: str,
    body: ConfirmRequest,
    request: Request,
):
    """Confirm an approval (yellow: risk_ack required if panel asks; red: 503)."""
    caller = getattr(request.state, "caller", None)
    if caller is None or caller.customer_id is None:
        raise GatewayError(Code.UNAUTHENTICATED, 401)

    customer_id = caller.customer_id
    db = request.app.state.db
    now = _now(request)
    route = f"/approvals/{approval_id}/confirm"

    _result: dict[str, Any] = {}

    async def produce():
        # All DB work on the SAME connection as request_idempotent, so a failure
        # rolls back both the dedup row and the state transitions together.
        approval = await _lock_approval(conn, approval_id, customer_id)
        plan, evaluation, _binding = await _validate_confirmable(
            conn, approval, view_hash=body.view_hash, now=now, request=request
        )

        risk = evaluation["risk"]

        # D6: red approvals are not confirmable yet (OTP not implemented).
        if risk == "RED":
            raise GatewayError(
                Code.DEPENDENCY_UNAVAILABLE, 503, "red approval requires OTP (not yet implemented)"
            )

        # Read view_json for risk_ack_required.
        view_json = evaluation.get("view_json") or {}
        risk_ack_required = view_json.get("risk_ack_required", False)

        # D4: risk_ack must be true only when the panel says it's required.
        if risk_ack_required and not body.risk_ack:
            raise GatewayError(Code.RISK_ACK_REQUIRED, 409)

        # D1: Transition both approval and plan in the same short transaction.
        await state.transition(
            conn,
            t.approvals,
            id_=approval_id,
            from_statuses=[ApprovalStatus.PENDING.value],
            to=ApprovalStatus.CONFIRMED.value,
        )
        await state.transition(
            conn,
            t.plans,
            id_=plan["id"],
            from_statuses=[PlanStatus.PENDING_APPROVAL.value],
            to=PlanStatus.CONFIRMED.value,
        )

        # Persist risk_ack and confirmed_at_wall.
        await conn.execute(
            t.approvals.update()
            .where(t.approvals.c.id == approval_id)
            .values(risk_ack=body.risk_ack, confirmed_at_wall=now)
        )

        _result["plan_id"] = plan["id"]
        _result["payload"] = {
            "approval_id": approval_id,
            "plan_id": plan["id"],
            "view_hash": body.view_hash,
            "risk_ack": body.risk_ack,
        }
        return 200, {
            "approval_id": approval_id,
            "plan_id": plan["id"],
            "status": "CONFIRMED",
        }

    async with db.begin() as conn:
        status, response, replayed = await request_idempotent(
            conn,
            customer_id=customer_id,
            route=route,
            request_id=body.request_id,
            body=body.model_dump(mode="json"),
            produce=produce,
        )

    # Audit outside the main transaction, only for new (non-replayed) operations.
    if not replayed and "plan_id" in _result:
        await append_tx(
            db,
            customer_id=customer_id,
            event_type="APPROVAL_CONFIRMED",
            payload=_result["payload"],
            event_id=new_id("evt"),
            plan_id=_result["plan_id"],
            now_wall=now,
        )

    return response


# ── G7 POST /approvals/{approval_id}/reject ─────────────────────────────────


@router.post("/approvals/{approval_id}/reject")
async def reject_approval(
    approval_id: str,
    body: ConfirmRejectRequest,
    request: Request,
):
    """Reject an approval."""
    caller = getattr(request.state, "caller", None)
    if caller is None or caller.customer_id is None:
        raise GatewayError(Code.UNAUTHENTICATED, 401)

    customer_id = caller.customer_id
    db = request.app.state.db
    now = _now(request)
    route = f"/approvals/{approval_id}/reject"

    _result: dict[str, Any] = {}

    async def produce():
        # All DB work on the SAME connection as request_idempotent.
        approval = await _lock_approval(conn, approval_id, customer_id)
        plan, evaluation, _binding = await _validate_confirmable(
            conn, approval, view_hash=body.view_hash, now=now, request=request
        )

        # D1: Transition both approval and plan in the same short transaction.
        await state.transition(
            conn,
            t.approvals,
            id_=approval_id,
            from_statuses=[ApprovalStatus.PENDING.value],
            to=ApprovalStatus.REJECTED.value,
        )
        await state.transition(
            conn,
            t.plans,
            id_=plan["id"],
            from_statuses=[PlanStatus.PENDING_APPROVAL.value],
            to=PlanStatus.REJECTED.value,
        )

        _result["plan_id"] = plan["id"]
        _result["payload"] = {
            "approval_id": approval_id,
            "plan_id": plan["id"],
            "view_hash": body.view_hash,
        }
        return 200, {
            "approval_id": approval_id,
            "plan_id": plan["id"],
            "status": "REJECTED",
        }

    async with db.begin() as conn:
        status, response, replayed = await request_idempotent(
            conn,
            customer_id=customer_id,
            route=route,
            request_id=body.request_id,
            body=body.model_dump(mode="json"),
            produce=produce,
        )

    # Audit outside the main transaction, only for new (non-replayed) operations.
    if not replayed and "plan_id" in _result:
        await append_tx(
            db,
            customer_id=customer_id,
            event_type="APPROVAL_REJECTED",
            payload=_result["payload"],
            event_id=new_id("evt"),
            plan_id=_result["plan_id"],
            now_wall=now,
        )

    return response


# ── G17 GET /approvals ─────────────────────────────────────────────────────


@router.get("/approvals")
async def list_approvals(
    request: Request,
    status: str | None = Query(default=None, description="Filter by approval status"),
    cursor: str | None = Query(default=None, description="Pagination cursor (approval_id)"),
    limit: int = Query(default=50, ge=1, le=100),
):
    """List approvals for the caller's customer, with optional status filter."""
    caller = getattr(request.state, "caller", None)
    if caller is None or caller.customer_id is None:
        raise GatewayError(Code.UNAUTHENTICATED, 401)

    customer_id = caller.customer_id
    db = request.app.state.db

    async with db.connect() as conn:
        # Build base query with JOIN to evaluations for risk (D3).
        cols = [
            t.approvals.c.id.label("approval_id"),
            t.approvals.c.plan_id,
            t.approvals.c.sid.label("session_id"),
            t.approvals.c.status,
            t.evaluations.c.risk,
            t.approvals.c.expires_at_wall,
        ]
        base = (
            select(*cols)
            .select_from(
                t.approvals.join(
                    t.evaluations,
                    (t.approvals.c.evaluation_id == t.evaluations.c.id)
                    & (t.approvals.c.plan_id == t.evaluations.c.plan_id),
                )
            )
            .where(t.approvals.c.customer_id == customer_id)
        )

        # Optional status filter.
        valid_statuses = {s.value for s in ApprovalStatus}
        if status is not None and status.upper() != "ALL":
            requested = status.upper()
            if requested not in valid_statuses:
                raise GatewayError(Code.CONTRACT_INVALID, 400, f"invalid status filter: {status}")
            base = base.where(t.approvals.c.status == requested)

        # D5: cursor pagination by approval_id DESC (ULID, time-ordered).
        if cursor:
            base = base.where(t.approvals.c.id < cursor)

        base = base.order_by(t.approvals.c.id.desc()).limit(limit)

        rows = (await conn.execute(base)).mappings().all()

    items = [
        ApprovalListItem(
            approval_id=row["approval_id"],
            plan_id=row["plan_id"],
            session_id=row["session_id"],
            status=row["status"],
            risk=row["risk"],
            expires_at_wall=row["expires_at_wall"].isoformat(),
        )
        for row in rows
    ]

    next_cursor = items[-1].approval_id if len(items) == limit else None

    return ListResponse(items=items, next_cursor=next_cursor).model_dump(mode="json")


# ── G10 POST /approvals/{approval_id}/otp (W4b2c) ──────────────────────────


@router.post("/approvals/{approval_id}/otp")
async def otp_send(approval_id: str, request: Request):
    """G10 OTP send — lands in W4b2c."""
    raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503, "G10 otp lands in W4b2c")
