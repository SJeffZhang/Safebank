"""G6 / G7 / G9 / G10 / G17 (W4): approvals, OTP, panel.

G9, G6, G7, G17 implemented in W4b2b. G10 reserved for W4b2c.
All write paths go through ``state.transition`` and ``audit.append_tx``.
Instance keys come from ``request.app.state`` — no global ``configure()``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from safebank_contracts.codes import Code
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from .. import state
from .. import tables as t
from ..approval.adapter import (
    approval_hmac_key as _adapter_key,
)
from ..approval.adapter import (
    otp_hmac_key as _adapter_otp_key,
)
from ..approval.adapter import (
    profile as _adapter_profile,
)
from ..approval.adapter import (
    wall_now as _adapter_wall,
)
from ..approval.otp import (
    compute_code_digest,
    derive_otp_keys,
    encrypt_otp_code,
    generate_otp_code,
    otp_retry_after,
)
from ..approval.otp import (
    verify_otp as _verify_otp,
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
    otp: str | None = None  # W4b2c: required for red-level confirm


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

    Lock order: session → plan → evaluation (matches W4b1 create_for_evaluation,
    avoiding ABBA deadlock with W1 cancel_session).  The caller already holds
    the approval row FOR UPDATE via :func:`_lock_approval`.
    """
    plan_id = approval["plan_id"]
    customer_id = approval["customer_id"]

    # 1. Status must be PENDING.
    if approval["status"] != ApprovalStatus.PENDING.value:
        raise GatewayError(Code.STATE_CHANGED, 409, "approval is not pending")

    # 2. Not expired.
    if approval["expires_at_wall"] <= now:
        raise GatewayError(Code.APPROVAL_EXPIRED, 409)

    # 3. view_hash must match byte-for-byte.
    if approval["view_hash"] != view_hash:
        raise GatewayError(Code.VIEW_HASH_MISMATCH, 409)

    # 4. Session not revoked. Locked BEFORE the plan row to match W4b1
    #    lock order (session → plan → evaluation); locking plan first
    #    would form an ABBA deadlock with W1 cancel_session which holds
    #    the session row lock first.
    session = (
        (
            await conn.execute(
                select(t.sessions.c.revoked_at_wall)
                .where(t.sessions.c.id == approval["sid"])
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if session is None or session["revoked_at_wall"] is not None:
        raise GatewayError(Code.AUTHORIZATION_CANCELLED, 409, "session has been revoked")

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

    # Cross-check: view_json identity must match the approval being fetched.
    # A view that claims a different approval_id than the path parameter is a
    # tampered or misrouted panel.
    view_approval_id = view.get("approval_id")
    if view_approval_id is not None and view_approval_id != approval_id:
        raise GatewayError(Code.VIEW_HASH_MISMATCH, 409, "view does not match approval")

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

        # ── RED: OTP verification (W4b2c) ──────────────────────────────
        if risk == "RED":
            from sqlalchemy import delete as sqla_delete

            # Read challenge (no FOR UPDATE — the approval lock provides
            # serialisation against concurrent confirm requests).
            challenge = (
                (
                    await conn.execute(
                        select(t.otp_challenges).where(
                            t.otp_challenges.c.approval_id == approval_id
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )

            # R2: no challenge or expired → OTP_EXPIRED.
            if challenge is None or challenge["expires_at_wall"] <= now:
                raise GatewayError(Code.OTP_EXPIRED, 409, "OTP challenge not found or expired")

            # R1: attempt_count >= 5 (should be LOCKED, but double-check).
            if challenge["attempt_count"] >= 5:
                raise GatewayError(Code.OTP_LOCKED, 409, "OTP attempts exhausted")

            # R2: otp field empty → OTP_EXPIRED (no valid code submitted).
            if not body.otp or not body.otp.strip():
                raise GatewayError(Code.OTP_EXPIRED, 409, "OTP code required")

            # Derive OTP keys for verification.
            otp_master = _adapter_otp_key(request)

            _code_digest_key, _aead_key = derive_otp_keys(otp_master)

            if not _verify_otp(
                {"code_digest": challenge["code_digest"]},
                body.otp.strip(),
                approval_id,
                _code_digest_key,
            ):
                new_attempt = challenge["attempt_count"] + 1
                values = {"attempt_count": new_attempt}
                if new_attempt >= 5:
                    # Persist lock within the SAME transaction.  The
                    # transaction commits normally so these survive.
                    await state.transition(
                        conn,
                        t.approvals,
                        id_=approval_id,
                        from_statuses=[ApprovalStatus.PENDING.value],
                        to=ApprovalStatus.LOCKED.value,
                    )
                    values["locked_at_wall"] = now
                    await conn.execute(
                        t.otp_challenges.update()
                        .where(t.otp_challenges.c.approval_id == approval_id)
                        .values(**values)
                    )
                    _result["otp_error"] = {
                        "code": "OTP_LOCKED",
                        "message": "OTP attempts exhausted",
                        "status": 409,
                    }
                    return 409, {"code": "OTP_LOCKED", "message": "OTP attempts exhausted"}
                else:
                    await conn.execute(
                        t.otp_challenges.update()
                        .where(t.otp_challenges.c.approval_id == approval_id)
                        .values(**values)
                    )
                    remaining = 5 - new_attempt
                    _result["otp_error"] = {
                        "code": "OTP_INVALID",
                        "message": f"invalid OTP code ({remaining} attempts remaining)",
                        "status": 409,
                        "details": {"remaining_attempts": remaining},
                    }
                    return 409, {
                        "code": "OTP_INVALID",
                        "message": f"invalid OTP code ({remaining} attempts remaining)",
                        "details": {"remaining_attempts": remaining},
                    }

            # OTP verified — delete the consumed challenge (same connection,
            # this path commits normally).
            await conn.execute(
                sqla_delete(t.otp_challenges).where(t.otp_challenges.c.approval_id == approval_id)
            )
            # NOTE: fall through to the shared confirm path below.
            _otp_verified = True
        else:
            _otp_verified = False

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
            "otp_verified": _otp_verified,
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

    # If produce() stored an OTP error, the transaction has already
    # committed the attempt_count / lock state — raise GatewayError
    # so the caller gets the proper error response.
    if not replayed and "otp_error" in _result:
        err = _result["otp_error"]
        raise GatewayError(
            getattr(Code, err["code"], Code.CONTRACT_INVALID),
            err["status"],
            message=err.get("message", ""),
            details=err.get("details"),
        )

    # Replayed error response: convert cached dict to proper error.
    if status >= 400:
        raise GatewayError(
            Code(response.get("code", "UNKNOWN")),
            status,
            message=response.get("message", ""),
            details=response.get("details"),
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


class OtpSendRequest(BaseModel):
    request_id: str
    view_hash: str


class OtpSendResponse(BaseModel):
    sent: bool
    expires_at_wall: str
    retry_after_seconds: int | None


@router.post("/approvals/{approval_id}/otp")
async def otp_send(
    approval_id: str,
    body: OtpSendRequest,
    request: Request,
):
    """G10 — send a simulated OTP verification code for a RED approval.

    Returns ``{sent: true, expires_at_wall, retry_after_seconds: null}`` on
    success.  The plaintext code is never returned — it is only decryptable
    via the D5 demo console (W5).
    """
    caller = getattr(request.state, "caller", None)
    if caller is None or caller.customer_id is None:
        raise GatewayError(Code.UNAUTHENTICATED, 401)

    customer_id = caller.customer_id
    db = request.app.state.db
    now = _now(request)
    route = f"/approvals/{approval_id}/otp"

    _result: dict[str, Any] = {}

    async def produce():
        # Read approval with lock.
        approval = await _lock_approval(conn, approval_id, customer_id)

        # Validate: status must be PENDING.
        if approval["status"] != ApprovalStatus.PENDING.value:
            raise GatewayError(Code.STATE_CHANGED, 409, "approval is not pending")

        # Validate: not expired.
        if approval["expires_at_wall"] <= now:
            raise GatewayError(Code.APPROVAL_EXPIRED, 409)

        # Validate: view_hash match.
        if approval["view_hash"] != body.view_hash:
            raise GatewayError(Code.VIEW_HASH_MISMATCH, 409)

        # Validate: session not revoked (same lock order as W4b2b: session first).
        session = (
            (
                await conn.execute(
                    select(t.sessions.c.revoked_at_wall)
                    .where(t.sessions.c.id == approval["sid"])
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if session is None or session["revoked_at_wall"] is not None:
            raise GatewayError(Code.AUTHORIZATION_CANCELLED, 409, "session has been revoked")

        # Read existing challenge (FOR UPDATE).
        existing = (
            (
                await conn.execute(
                    select(t.otp_challenges)
                    .where(t.otp_challenges.c.approval_id == approval_id)
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )

        # Rate-limit checks (R1).
        sent_before = existing["sent_count"] if existing is not None else 0
        last_sent = existing["last_sent_at_wall"] if existing is not None else None

        try:
            retry = otp_retry_after(last_sent, sent_before, now)
        except ValueError:
            raise GatewayError(
                Code.RATE_LIMITED,
                429,
                message="OTP send limit exhausted (max 3 sends)",
            ) from None
        if retry is not None:
            raise GatewayError(
                Code.RATE_LIMITED,
                429,
                message=f"OTP send too frequent, retry after {retry}s",
                details={"retry_after_seconds": retry},
            )

        # Generate OTP code and derived keys.
        otp_master = _adapter_otp_key(request)

        _code_digest_key, _aead_key = derive_otp_keys(otp_master)
        code = generate_otp_code()
        new_digest = compute_code_digest(code, approval_id, _code_digest_key)
        new_ciphertext = encrypt_otp_code(
            code,
            approval_id=approval_id,
            plan_id=approval["plan_id"],
            view_hash=body.view_hash,
            wall_now=now,
            key=_aead_key,
        )
        expiry = now + timedelta(minutes=2)

        # R1: attempt_count is cumulative across resends — do NOT reset to 0.
        prev_attempt = existing["attempt_count"] if existing is not None else 0

        # Upsert the challenge row.
        await conn.execute(
            pg_insert(t.otp_challenges)
            .values(
                approval_id=approval_id,
                code_digest=new_digest,
                code_ciphertext=new_ciphertext,
                view_hash=body.view_hash,
                expires_at_wall=expiry,
                attempt_count=prev_attempt,
                sent_count=sent_before + 1,
                last_sent_at_wall=now,
            )
            .on_conflict_do_update(
                index_elements=["approval_id"],
                set_={
                    "code_digest": new_digest,
                    "code_ciphertext": new_ciphertext,
                    "view_hash": body.view_hash,
                    "expires_at_wall": expiry,
                    "attempt_count": prev_attempt,
                    "sent_count": sent_before + 1,
                    "last_sent_at_wall": now,
                },
            )
        )

        response = OtpSendResponse(
            sent=True,
            expires_at_wall=expiry.isoformat(),
            retry_after_seconds=None,
        )
        _result["payload"] = {
            "approval_id": approval_id,
            "view_hash": body.view_hash,
            "expires_at_wall": expiry.isoformat(),
        }
        return 200, response.model_dump(mode="json")

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
    if not replayed and "payload" in _result:
        await append_tx(
            db,
            customer_id=customer_id,
            event_type="APPROVAL_OTP_SENT",
            payload=_result["payload"],
            event_id=new_id("evt"),
            plan_id=approval_id,
            now_wall=now,
        )

    return response
