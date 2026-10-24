"""W4b2c DB fixtures: isolated temporary PostgreSQL with test FastAPI app.

Reuses the W4b2b ``build_app`` pattern and extends it with OTP-specific seed
helpers.  Each test builds its own app inside ``asyncio.run()`` so the DB
engine and ASGI processing share the same event loop.
"""

from __future__ import annotations

from datetime import timedelta

from safebank_contracts.enums import Risk
from sqlalchemy.dialects.postgresql import insert as pg_insert

from services.gateway.app import tables as t

# Re-export W4b2b fixtures unchanged.
from tests.gateway.w4b2b.db.conftest import (  # noqa: F401
    _PREFLIGHT_JSON,
    _UTTERANCE_IDS,
    _VERSIONS,
    HMAC_KEY,
    OTP_KEY,
    WALL,
    AppConfig,
    TestApp,
    _runtime_db,
    _seed_approval,
    _seed_auth,
    _seed_evaluation,
    _seed_full,
    _seed_plan,
    _seed_session,
    app_config,
    build_app,
)

# ── OTP-specific seed helpers ─────────────────────────────────────────────────


async def _seed_red_approval_chain(
    conn,
    *,
    approval_id="apv_1",
    plan_id="pln_1",
    evaluation_id="evl_1",
    sid="ses_1",
    customer="cus_zs",
    approval_status="PENDING",
    view_hash="b" * 64,
    view_json=None,
    hmac_key=HMAC_KEY,
):
    """Seed a complete chain with a RED-risk evaluation.

    This is the minimal setup for testing OTP-accompanied confirm: the
    approval is PENDING with RED risk and ``otp_required: true`` in the
    panel view_json.
    """
    _view = (
        view_json
        if view_json is not None
        else {
            "plan_id": plan_id,
            "risk_ack_required": True,
            "otp_required": True,
        }
    )
    await _seed_auth(conn, customer=customer)
    await _seed_session(conn, sid=sid, customer=customer)
    await _seed_plan(
        conn,
        plan_id=plan_id,
        sid=sid,
        customer=customer,
        status="PENDING_APPROVAL",
    )
    await _seed_evaluation(
        conn,
        plan_id=plan_id,
        evaluation_id=evaluation_id,
        risk=Risk.RED,
        view_json=_view,
    )
    await _seed_approval(
        conn,
        approval_id=approval_id,
        plan_id=plan_id,
        evaluation_id=evaluation_id,
        sid=sid,
        customer=customer,
        status=approval_status,
        view_hash=view_hash,
        hmac_key=hmac_key,
    )


async def _seed_otp_challenge(
    conn,
    *,
    approval_id="apv_1",
    code_digest="d" * 64,
    code_ciphertext=None,
    view_hash="b" * 64,
    expires_at_wall=None,
    attempt_count=0,
    sent_count=1,
    last_sent_at_wall=None,
):
    """Insert an existing otp_challenges row (for testing G6 red confirm paths)."""
    expiry = expires_at_wall if expires_at_wall is not None else WALL + timedelta(minutes=2)
    last = last_sent_at_wall if last_sent_at_wall is not None else WALL
    await conn.execute(
        pg_insert(t.otp_challenges)
        .values(
            approval_id=approval_id,
            code_digest=code_digest,
            code_ciphertext=code_ciphertext,
            view_hash=view_hash,
            expires_at_wall=expiry,
            attempt_count=attempt_count,
            sent_count=sent_count,
            last_sent_at_wall=last,
        )
        .on_conflict_do_update(
            index_elements=["approval_id"],
            set_={
                "code_digest": code_digest,
                "code_ciphertext": code_ciphertext,
                "view_hash": view_hash,
                "expires_at_wall": expiry,
                "attempt_count": attempt_count,
                "sent_count": sent_count,
                "last_sent_at_wall": last,
            },
        )
    )
