"""Transactional approval binding, OTP and confirmation.

Callers own an HTTP-free transaction. Error outcomes are returned rather than
raised when a security counter/status must commit before the HTTP error.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timedelta
from typing import Any

from safebank_contracts.codes import Code
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from .. import tables as t
from ..errors import GatewayError
from ..middleware import canonical_bytes
from ..schemas.decision import DecisionValue
from ..schemas.planview import PlanView
from ..state import transition
from . import sms
from .planview import content_hash

_approval_key: bytes | None = None
_otp_key: bytes | None = None
# The writer must enqueue to the controlled simulated SMS store in THIS
# transaction. It must not perform HTTP or log plaintext. W5 owns delivery/read.
OtpWriter = Callable[[AsyncConnection, str, str, str, datetime], Awaitable[None]]
_otp_writer: OtpWriter | None = None
_simulated_sms = False
Row = Mapping[str, Any]


def configure(
    approval_hmac_key: str,
    otp_hmac_key: str,
    *,
    otp_writer: OtpWriter | None = None,
    simulated_sms: bool = False,
) -> None:
    if len(approval_hmac_key.encode()) < 32 or len(otp_hmac_key.encode()) < 32:
        raise ValueError("approval and OTP keys must be at least 32 bytes")
    global _approval_key, _otp_key, _otp_writer, _simulated_sms
    _approval_key, _otp_key, _otp_writer = (
        approval_hmac_key.encode(),
        otp_hmac_key.encode(),
        otp_writer,
    )
    _simulated_sms = simulated_sms


def _mac(key: bytes | None, value: Any) -> str:
    if key is None:
        raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503)
    return hmac.new(key, canonical_bytes(value), hashlib.sha256).hexdigest()


def binding(plan: Row, evaluation: Row, approval: Row) -> str:
    return _mac(
        _approval_key,
        dict(
            customer_id=plan["customer_id"],
            sid=plan["sid"],
            plan_id=plan["id"],
            plan_hash=plan["plan_hash"],
            evaluation_id=evaluation["id"],
            versions=evaluation["versions"],
            clock_version=evaluation["clock_version"],
            policy_version=evaluation["policy_version"],
            profile=evaluation["decision"].get("profile", evaluation["policy_version"]),
            view_hash=approval["view_hash"],
            expires_at_wall=approval["expires_at_wall"].isoformat(),
        ),
    )


def verify_binding(plan: Row, evaluation: Row, approval: Row) -> None:
    if (
        approval["plan_id"] != plan["id"]
        or approval["evaluation_id"] != evaluation["id"]
        or evaluation["plan_id"] != plan["id"]
        or approval["customer_id"] != plan["customer_id"]
        or approval["sid"] != plan["sid"]
        or approval["plan_hash"] != plan["plan_hash"]
        or approval["view_hash"] != evaluation["view_hash"]
        or not hmac.compare_digest(approval["binding_mac"], binding(plan, evaluation, approval))
    ):
        raise GatewayError(Code.APPROVAL_BINDING_MISMATCH, 409)


async def create_for_evaluation(
    conn: AsyncConnection, *, plan: Any, evaluation: Any, view: Any
) -> str | None:
    """Create one customer confirmation. Green policy approval stays in evaluation."""
    if conn is None or plan is None or evaluation is None:
        raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503)
    decision = evaluation["decision"]
    value = decision["decision"]
    if value == DecisionValue.ALLOW:
        return None
    if value != DecisionValue.REQUIRE_APPROVAL:
        raise ValueError("only approving evaluations can create approvals")
    view = PlanView.model_validate(view)
    if (
        view.origin != plan["origin"]
        or view.risk != evaluation["risk"]
        or view.plan_id != plan["id"]
        or view.session_id != plan["sid"]
        or view.evaluation_id != evaluation["id"]
        or evaluation["plan_id"] != plan["id"]
        or view.view_hash != evaluation["view_hash"]
        or content_hash(view) != view.view_hash
    ):
        raise GatewayError(Code.APPROVAL_BINDING_MISMATCH, 409)
    # Session serialization protects the partial unique indexes across plans.
    session_id = await conn.scalar(
        select(t.sessions.c.id)
        .where(
            t.sessions.c.id == plan["sid"],
            t.sessions.c.customer_id == plan["customer_id"],
            t.sessions.c.revoked_at_wall.is_(None),
        )
        .with_for_update()
    )
    if session_id is None:
        raise GatewayError(Code.NOT_FOUND, 404)
    old = (
        (
            await conn.execute(
                select(t.approvals)
                .where(
                    t.approvals.c.sid == plan["sid"],
                    t.approvals.c.status.in_(["PENDING", "CONFIRMED"]),
                )
                .with_for_update()
            )
        )
        .mappings()
        .all()
    )
    for row in old:
        owner = (
            (await conn.execute(select(t.plans).where(t.plans.c.id == row["plan_id"])))
            .mappings()
            .one()
        )
        if owner["status"] in {"EXECUTING", "UNKNOWN"}:
            raise GatewayError(Code.EXECUTION_IN_PROGRESS, 409)
        await transition(
            conn, t.approvals, id_=row["id"], from_statuses=[row["status"]], to="SUPERSEDED"
        )
    expiry = min(
        datetime.fromisoformat(view.expires_at_wall),
        evaluation["created_at_wall"] + timedelta(minutes=5),
    )
    row = dict(
        id="apv_" + secrets.token_hex(16),
        plan_id=plan["id"],
        evaluation_id=evaluation["id"],
        customer_id=plan["customer_id"],
        sid=plan["sid"],
        plan_hash=plan["plan_hash"],
        view_hash=view.view_hash,
        expires_at_wall=expiry,
        status="PENDING",
        risk_ack=False,
    )
    row["binding_mac"] = binding(plan, evaluation, row)
    await conn.execute(t.approvals.insert().values(**row))
    return row["id"]


async def load(
    conn: AsyncConnection, approval_id: str, customer_id: str, *, locked: bool = False
) -> tuple[Row, Row, Row]:
    query = select(t.approvals).where(
        t.approvals.c.id == approval_id, t.approvals.c.customer_id == customer_id
    )
    if locked:
        query = query.with_for_update()
    row = (await conn.execute(query)).mappings().one_or_none()
    if row is None:
        raise GatewayError(Code.NOT_FOUND, 404)
    plan = (
        (await conn.execute(select(t.plans).where(t.plans.c.id == row["plan_id"]))).mappings().one()
    )
    evaluation = (
        (
            await conn.execute(
                select(t.evaluations).where(t.evaluations.c.id == row["evaluation_id"])
            )
        )
        .mappings()
        .one()
    )
    verify_binding(plan, evaluation, row)
    return row, plan, evaluation


def stored_view(evaluation: Row) -> PlanView:
    data = evaluation.get("view_json")
    if data is None:
        raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503, "persisted approval view unavailable")
    view = PlanView.model_validate(data)
    if view.view_hash != evaluation["view_hash"] or content_hash(view) != view.view_hash:
        raise GatewayError(Code.APPROVAL_BINDING_MISMATCH, 409)
    return view


async def pending(conn: AsyncConnection, row: Row, now: datetime) -> GatewayError | None:
    if row["status"] != "PENDING":
        code = {
            "REJECTED": Code.APPROVAL_REJECTED,
            "USED": Code.APPROVAL_USED,
            "EXPIRED": Code.APPROVAL_EXPIRED,
            "SUPERSEDED": Code.APPROVAL_SUPERSEDED,
            "LOCKED": Code.OTP_LOCKED,
        }.get(row["status"], Code.APPROVAL_REQUIRED)
        return GatewayError(code, 409)
    if row["expires_at_wall"] <= now:
        await transition(conn, t.approvals, id_=row["id"], from_statuses=["PENDING"], to="EXPIRED")
        return GatewayError(Code.APPROVAL_EXPIRED, 409)
    return None


def otp_digest(approval_id: str, view_hash: str, code: str) -> str:
    return _mac(_otp_key, dict(approval_id=approval_id, view_hash=view_hash, code=code))


def disclosure_token(row: Row, view: PlanView) -> str | None:
    if not view.disclosures:
        return None
    return _mac(
        _approval_key,
        dict(
            approval_id=row["id"],
            view_hash=row["view_hash"],
            disclosures=[d.model_dump(mode="json") for d in view.disclosures],
        ),
    )


async def send_otp(
    conn: AsyncConnection, row: Row, view: PlanView, now: datetime
) -> dict[str, Any] | GatewayError:
    error = await pending(conn, row, now)
    if error:
        return error
    if not view.otp_required:
        return GatewayError(Code.CONTRACT_INVALID, 422)
    if _otp_writer is None or not _simulated_sms:
        return GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503)
    old = (
        (
            await conn.execute(
                select(t.otp_challenges)
                .where(t.otp_challenges.c.approval_id == row["id"])
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if old:
        if old["locked_at_wall"] or old["attempt_count"] >= 5:
            return GatewayError(Code.OTP_LOCKED, 409)
        if old["sent_count"] >= 3 or (now - old["last_sent_at_wall"]).total_seconds() < 30:
            return GatewayError(Code.RATE_LIMITED, 429)
    code = f"{secrets.randbelow(1_000_000):06d}"
    expiry = min(now + timedelta(minutes=2), row["expires_at_wall"])
    values = dict(
        approval_id=row["id"],
        code_digest=otp_digest(row["id"], row["view_hash"], code),
        view_hash=row["view_hash"],
        expires_at_wall=expiry,
        attempt_count=old["attempt_count"] if old else 0,
        sent_count=old["sent_count"] + 1 if old else 1,
        last_sent_at_wall=now,
    )
    await conn.execute(
        pg_insert(t.otp_challenges)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[t.otp_challenges.c.approval_id],
            set_={k: v for k, v in values.items() if k != "approval_id"},
        )
    )
    encrypted = sms.encrypt(_otp_key, row["id"], row["view_hash"], code)
    await _otp_writer(conn, row["customer_id"], row["id"], encrypted, expiry)
    return dict(sent=True, expires_at_wall=expiry.isoformat(), retry_after_seconds=30)


async def confirm(
    conn: AsyncConnection,
    row: Row,
    plan: Row,
    view: PlanView,
    now: datetime,
    *,
    otp: str | None,
    risk_ack: bool,
    token: str | None,
) -> dict[str, Any] | GatewayError:
    error = await pending(conn, row, now)
    if error:
        return error
    if view.risk_ack_required and (
        not risk_ack or not token or not hmac.compare_digest(token, disclosure_token(row, view))
    ):
        return GatewayError(Code.RISK_ACK_REQUIRED, 409)
    if view.otp_required:
        challenge = (
            (
                await conn.execute(
                    select(t.otp_challenges)
                    .where(t.otp_challenges.c.approval_id == row["id"])
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if challenge is None:
            return GatewayError(Code.OTP_INVALID, 409)
        if challenge["locked_at_wall"] or challenge["attempt_count"] >= 5:
            return GatewayError(Code.OTP_LOCKED, 409)
        if challenge["expires_at_wall"] <= now:
            return GatewayError(Code.OTP_EXPIRED, 409)
        if (
            challenge["view_hash"] != row["view_hash"]
            or not otp
            or not hmac.compare_digest(
                challenge["code_digest"], otp_digest(row["id"], row["view_hash"], otp)
            )
        ):
            count = challenge["attempt_count"] + 1
            await conn.execute(
                update(t.otp_challenges)
                .where(t.otp_challenges.c.approval_id == row["id"])
                .values(attempt_count=count, locked_at_wall=now if count >= 5 else None)
            )
            if count >= 5:
                await transition(
                    conn, t.approvals, id_=row["id"], from_statuses=["PENDING"], to="LOCKED"
                )
            return GatewayError(Code.OTP_LOCKED if count >= 5 else Code.OTP_INVALID, 409)
    await transition(conn, t.approvals, id_=row["id"], from_statuses=["PENDING"], to="CONFIRMED")
    await conn.execute(
        update(t.approvals)
        .where(t.approvals.c.id == row["id"])
        .values(risk_ack=risk_ack, confirmed_at_wall=now)
    )
    await transition(conn, t.plans, id_=plan["id"], from_statuses=[plan["status"]], to="CONFIRMED")
    return dict(approval_id=row["id"], plan_id=plan["id"], status="CONFIRMED")
