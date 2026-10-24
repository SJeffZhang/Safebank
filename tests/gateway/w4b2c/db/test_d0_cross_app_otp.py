"""W4b2c D0: cross-app OTP key isolation.

Two app instances with different OTP HMAC keys MUST NOT be able to verify
each other's OTP codes, even when the challenge row is shared in the same
DB.  The digest computation is key-dependent so the wrong key yields a
different digest and the verification fails.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from safebank_contracts.enums import Risk
from sqlalchemy import select

from services.gateway.app import tables as t
from tests.gateway.w4b2b.db.conftest import (
    WALL,
    _runtime_db,
    _seed_full,
    build_app,
)

CONFIRM_URL = "/approvals/apv_1/confirm"
VIEW_HASH = "b" * 64

KEY_X = b"app-x-otp-hmac-key--at-least-32-bytes"
KEY_Y = b"app-y-otp-hmac-key--at-least-32-bytes"


def _confirm_body(
    request_id="req_d0_1",
    view_hash=VIEW_HASH,
    risk_ack=True,
    otp=None,
):
    d = {"request_id": request_id, "view_hash": view_hash, "risk_ack": risk_ack}
    if otp is not None:
        d["otp"] = otp
    return d


def test_d0_otp_sent_by_app_a_verified_by_app_a(app_config):
    """App A generates and verifies its own OTP code → 200 CONFIRMED."""

    async def run():
        from services.gateway.app.approval.otp import (
            compute_code_digest,
            derive_otp_keys,
            encrypt_otp_code,
        )

        # App A with key X.
        app_a = await build_app(app_config, truncate=True, otp_hmac_key=KEY_X)
        db_a = _runtime_db(app_a)

        # Derive OTP subkeys for key X.
        digest_key_x, aead_key_x = derive_otp_keys(KEY_X)

        # Generate a code and compute its digest / ciphertext with key X.
        from services.gateway.app.approval.otp import generate_otp_code

        code = generate_otp_code()
        digest = compute_code_digest(code, "apv_1", digest_key_x)
        expiry = WALL + timedelta(minutes=2)
        ciphertext = encrypt_otp_code(
            code,
            approval_id="apv_1",
            plan_id="pln_1",
            view_hash=VIEW_HASH,
            wall_now=WALL,
            key=aead_key_x,
        )

        # Seed approval chain + OTP challenge as if G10 sent it via app A.
        async with db_a.begin() as conn:
            # RED approval chain (otp_required=True).
            view_json = {"risk_ack_required": True, "otp_required": True}
            await _seed_full(conn, view_json=view_json, risk=Risk.RED)

            # Patch the OTP challenge row with key-X derived values.
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            await conn.execute(
                pg_insert(t.otp_challenges)
                .values(
                    approval_id="apv_1",
                    code_digest=digest,
                    code_ciphertext=ciphertext,
                    view_hash=VIEW_HASH,
                    expires_at_wall=expiry,
                    sent_count=1,
                    attempt_count=0,
                    last_sent_at_wall=WALL,
                )
                .on_conflict_do_update(
                    index_elements=["approval_id"],
                    set_={
                        "code_digest": digest,
                        "code_ciphertext": ciphertext,
                    },
                )
            )

        # App A confirms with the correct code → 200.
        resp = await app_a.client.post(
            CONFIRM_URL,
            json=_confirm_body(otp=code),
            headers=app_a.auth_headers(),
        )
        assert resp.status_code == 200, f"app A: expected 200, got {resp.status_code} {resp.text}"
        assert resp.json()["status"] == "CONFIRMED"

        await db_a.dispose()

    asyncio.run(run())


def test_d0_otp_sent_by_app_a_rejected_by_app_b(app_config):
    """App B with a different OTP key cannot verify app A's code → 409 OTP_INVALID."""

    async def run():
        from services.gateway.app.approval.otp import (
            compute_code_digest,
            derive_otp_keys,
            encrypt_otp_code,
        )

        # Build app A and seed the data with key X.
        app_a = await build_app(app_config, truncate=True, otp_hmac_key=KEY_X)
        db_a = _runtime_db(app_a)

        digest_key_x, aead_key_x = derive_otp_keys(KEY_X)

        from services.gateway.app.approval.otp import generate_otp_code

        code = generate_otp_code()
        digest = compute_code_digest(code, "apv_1", digest_key_x)
        expiry = WALL + timedelta(minutes=2)
        ciphertext = encrypt_otp_code(
            code,
            approval_id="apv_1",
            plan_id="pln_1",
            view_hash=VIEW_HASH,
            wall_now=WALL,
            key=aead_key_x,
        )

        async with db_a.begin() as conn:
            view_json = {"risk_ack_required": True, "otp_required": True}
            await _seed_full(conn, view_json=view_json, risk=Risk.RED)

            from sqlalchemy.dialects.postgresql import insert as pg_insert

            await conn.execute(
                pg_insert(t.otp_challenges)
                .values(
                    approval_id="apv_1",
                    code_digest=digest,
                    code_ciphertext=ciphertext,
                    view_hash=VIEW_HASH,
                    expires_at_wall=expiry,
                    sent_count=1,
                    attempt_count=0,
                    last_sent_at_wall=WALL,
                )
                .on_conflict_do_update(
                    index_elements=["approval_id"],
                    set_={
                        "code_digest": digest,
                        "code_ciphertext": ciphertext,
                    },
                )
            )

        await db_a.dispose()

        # Build a SECOND app on the same DB with key Y.
        # Do NOT truncate — reuse the data seeded above.
        app_b = await build_app(app_config, truncate=False, otp_hmac_key=KEY_Y)
        db_b = _runtime_db(app_b)

        # App B tries to confirm with the same code → 409 OTP_INVALID.
        resp = await app_b.client.post(
            CONFIRM_URL,
            json=_confirm_body(otp=code),
            headers=app_b.auth_headers(),
        )
        assert resp.status_code == 409, f"app B: expected 409, got {resp.status_code} {resp.text}"
        data = resp.json()
        err_code = data.get("error", {}).get("code")
        assert err_code in ("OTP_INVALID",), f"unexpected code: {err_code}"

        # Verify attempt_count incremented.
        async with db_b.connect() as conn:
            challenge = (
                (
                    await conn.execute(
                        select(t.otp_challenges).where(t.otp_challenges.c.approval_id == "apv_1")
                    )
                )
                .mappings()
                .one()
            )
        assert challenge["attempt_count"] == 1

        await db_b.dispose()

    asyncio.run(run())
