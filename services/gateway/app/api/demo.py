"""D0–D12 (W5): demo / evaluation console, mounted only in DEMO/EVAL mode."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from safebank_contracts.codes import Code
from sqlalchemy import select

from .. import tables as t
from ..approval.adapter import otp_hmac_key as _adapter_otp_key
from ..approval.adapter import wall_now as _adapter_wall
from ..approval.otp import decrypt_otp_code, derive_otp_keys
from ..audit.chain import append_tx
from ..auth.matrix import CONSOLE_AUD
from ..auth.tokens import sign_jwt
from ..errors import GatewayError
from ..utterance.service import new_id
from . import stub

router = APIRouter()

CONSOLE_SCOPE = ["console"]
CONSOLE_TTL_SECONDS = 3600


def _now(request: Request) -> datetime:
    clock = _adapter_wall(request)
    now = clock()
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503, "wall clock unavailable")
    return now


# ── D0 POST /demo/auth/login ──────────────────────────────────────────────────


class ConsoleLoginRequest(BaseModel):
    secret: str


class ConsoleLoginResponse(BaseModel):
    token: str
    expires_at_wall: str


@router.post("/demo/auth/login")
async def console_login(body: ConsoleLoginRequest, request: Request):
    """D0 — exchange the demo console secret for a 1-hour console JWT.

    The global auth matrix already enforced the ``X-Service-Token`` (D) header;
    here we additionally compare the body secret against the configured
    ``DEMO_CONSOLE_TOKEN`` in constant time.  A wrong secret returns 401 with
    no reason leaked.
    """
    expected = request.app.state.settings.demo_console_token
    if not expected:
        raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503, "demo console not configured")
    if not secrets.compare_digest(body.secret.encode("utf-8"), expected.encode("utf-8")):
        raise GatewayError(Code.UNAUTHENTICATED, 401)

    now = _now(request)
    expires_at = now + timedelta(seconds=CONSOLE_TTL_SECONDS)
    token = sign_jwt(
        request.app.state.jwt_private_key,
        sub="demo-console",
        aud=CONSOLE_AUD,
        scope=CONSOLE_SCOPE,
        now=int(now.timestamp()),
        ttl=CONSOLE_TTL_SECONDS,
    )
    return ConsoleLoginResponse(
        token=token,
        expires_at_wall=expires_at.isoformat(),
    ).model_dump(mode="json")


router.post("/demo/reset")(stub("D1 reset lands in W5"))
router.post("/demo/scene/prepare")(stub("D2 scene prepare lands in W5"))
router.post("/demo/clock/advance")(stub("D3 clock advance lands in W5"))
router.get("/demo/clock/advances/{job_id}")(stub("D3 clock advance status lands in W5"))
router.post("/demo/events")(stub("D4 inject event lands in W5"))


# ── D5 GET /demo/sms ──────────────────────────────────────────────────────────


class SmsCodeResponse(BaseModel):
    approval_id: str
    code: str
    expires_at_wall: str


@router.get("/demo/sms")
async def demo_sms(request: Request, approval_id: str = Query(...)):
    """D5 — read the currently-active OTP for a red approval (demo/eval only).

    The auth matrix requires the D service token plus a ``demo-console`` JWT.
    Returns the decrypted plaintext code and its expiry; no active challenge
    (missing / expired / locked / AEAD mismatch) → 404.  The plaintext code is
    never written to audit or logs — the audit event records only the
    ``approval_id``.
    """
    db = request.app.state.db
    now = _now(request)

    async with db.connect() as conn:
        row = (
            (
                await conn.execute(
                    select(
                        t.otp_challenges.c.code_ciphertext,
                        t.otp_challenges.c.view_hash,
                        t.otp_challenges.c.expires_at_wall,
                        t.otp_challenges.c.last_sent_at_wall,
                        t.otp_challenges.c.locked_at_wall,
                        t.approvals.c.plan_id,
                        t.approvals.c.customer_id,
                    )
                    .join(t.approvals, t.approvals.c.id == t.otp_challenges.c.approval_id)
                    .where(t.otp_challenges.c.approval_id == approval_id)
                )
            )
            .mappings()
            .one_or_none()
        )

    if row is None or row["locked_at_wall"] is not None or row["expires_at_wall"] <= now:
        raise GatewayError(Code.NOT_FOUND, 404, "no active OTP challenge")

    ciphertext = row["code_ciphertext"]
    last_sent = row["last_sent_at_wall"]
    if not ciphertext or last_sent is None:
        raise GatewayError(Code.NOT_FOUND, 404, "no active OTP challenge")
    if last_sent.tzinfo is None or last_sent.utcoffset() is None:
        raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503, "challenge timestamp missing tz")

    # Reconstruct the exact AAD wall_now used at encryption time.  G10 stored
    # ``last_sent_at_wall = now`` and encrypted with ``wall_now=now``, so the
    # two must match byte-for-byte after isoformat.  (UTC wall clock round-trips
    # losslessly through the timestamptz column.)
    otp_master = _adapter_otp_key(request)
    _code_digest_key, _aead_key = derive_otp_keys(otp_master)
    code = decrypt_otp_code(
        ciphertext,
        approval_id=approval_id,
        plan_id=row["plan_id"],
        view_hash=row["view_hash"],
        wall_now=last_sent,
        key=_aead_key,
    )
    if code is None:
        raise GatewayError(Code.NOT_FOUND, 404, "no active OTP challenge")

    await append_tx(
        db,
        customer_id=row["customer_id"],
        event_type="DEMO_OTP_READ",
        payload={"approval_id": approval_id},
        event_id=new_id("evt"),
        plan_id=row["plan_id"],
        now_wall=now,
    )

    return SmsCodeResponse(
        approval_id=approval_id,
        code=code,
        expires_at_wall=row["expires_at_wall"].isoformat(),
    ).model_dump(mode="json")


router.post("/demo/attack/cases/{case_id}/start")(stub("D6a attack start lands in W5"))
router.post("/demo/attack/submit_plan")(stub("D6 attack submit lands in W5"))
router.post("/demo/attack/plans/{plan_id}/execute")(stub("D6 attack execute lands in W5"))
router.get("/demo/attack/plans/{plan_id}")(stub("D6 attack plan lands in W5"))
router.post("/demo/eval/assert")(stub("D7 eval assert lands in W5"))
router.get("/demo/plans/{plan_id}/comparison")(stub("D8 comparison lands in W5"))
router.get("/demo/reports/latest")(stub("D9 reports lands in W5"))
router.post("/demo/auth/logout")(stub("D10 console logout lands in W5"))
router.get("/demo/plans")(stub("D11 plans list lands in W5"))
router.get("/demo/audit")(stub("D12 demo audit lands in W5"))
