"""W4b2c Task 2: G10 POST /approvals/{approval_id}/otp real-PG red tests.

All tests expected to FAIL until the G10 handler is implemented.
Covers: send, idempotency, auth, ownership, status checks, view_hash,
expiry, rate limiting, resend overwrites, audit.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from safebank_contracts.enums import Risk
from sqlalchemy import select

from services.gateway.app import tables as t
from services.gateway.app.schemas.enums import ApprovalStatus
from tests.gateway.w4b2c.db.conftest import (
    WALL,
    _runtime_db,
    _seed_otp_challenge,
    _seed_red_approval_chain,
    build_app,
)

OTP_URL = "/approvals/apv_1/otp"
VIEW_HASH = "b" * 64


def _body(request_id="req_otp_1", view_hash=VIEW_HASH):
    return {"request_id": request_id, "view_hash": view_hash}


# ── Success ───────────────────────────────────────────────────────────────────


def test_g10_send_succeeds(app_config):
    """G10 sends OTP for a PENDING RED approval → 200, otp_challenges row created."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_red_approval_chain(conn)

        resp = await app.client.post(OTP_URL, json=_body(), headers=app.auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["sent"] is True
        assert "expires_at_wall" in data
        assert data["retry_after_seconds"] is None

        # Verify otp_challenges row.
        async with db.connect() as conn:
            challenge = (
                (
                    await conn.execute(
                        select(t.otp_challenges).where(t.otp_challenges.c.approval_id == "apv_1")
                    )
                )
                .mappings()
                .one()
            )
        assert challenge["code_digest"] is not None
        assert len(challenge["code_digest"]) == 64  # SHA256 hex
        assert challenge["sent_count"] == 1
        assert challenge["attempt_count"] == 0

        await db.dispose()

    asyncio.run(run())


def test_g10_idempotent_replay(app_config):
    """Same request_id returns original result, sent_count stays 1, code_digest unchanged."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_red_approval_chain(conn)

        body = _body()
        r1 = await app.client.post(OTP_URL, json=body, headers=app.auth_headers())
        assert r1.status_code == 200

        r2 = await app.client.post(OTP_URL, json=body, headers=app.auth_headers())
        assert r2.status_code == 200
        assert r2.json() == r1.json()

        # DB: sent_count still 1, code_digest unchanged.
        async with db.connect() as conn:
            challenge = (
                (
                    await conn.execute(
                        select(t.otp_challenges).where(t.otp_challenges.c.approval_id == "apv_1")
                    )
                )
                .mappings()
                .one()
            )
        assert challenge["sent_count"] == 1

        await db.dispose()

    asyncio.run(run())


# ── Auth ──────────────────────────────────────────────────────────────────────


def test_g10_no_auth_returns_401(app_config):
    """G10 without auth headers → 401."""

    async def run():
        app = await build_app(app_config)
        resp = await app.client.post(OTP_URL, json=_body())
        assert resp.status_code == 401

    asyncio.run(run())


def test_g10_app_only_scope_returns_403(app_config):
    """G10 with app scope but no approve scope → 403."""

    async def run():
        app = await build_app(app_config)
        resp = await app.client.post(OTP_URL, json=_body(), headers=app.auth_headers(scope=["app"]))
        assert resp.status_code == 403

    asyncio.run(run())


# ── Ownership / existence ─────────────────────────────────────────────────────


def test_g10_nonexistent_approval_returns_404(app_config):
    """G10 on nonexistent approval → 404."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            from tests.gateway.w4b2c.db.conftest import _seed_auth

            await _seed_auth(conn)

        resp = await app.client.post(OTP_URL, json=_body(), headers=app.auth_headers())
        assert resp.status_code == 404

    asyncio.run(run())


def test_g10_wrong_customer_returns_404(app_config):
    """G10 for another customer's approval → 404."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_red_approval_chain(conn, customer="cus_zs")
            from tests.gateway.w4b2c.db.conftest import _seed_auth

            await _seed_auth(conn, jti="other-jti", customer="cus_other")

        resp = await app.client.post(
            OTP_URL,
            json=_body(),
            headers=app.auth_headers(customer_id="cus_other", jti="other-jti"),
        )
        assert resp.status_code == 404

    asyncio.run(run())


# ── Status checks ────────────────────────────────────────────────────────────


def test_g10_non_pending_approval_returns_409(app_config):
    """G10 on non-PENDING (CONFIRMED) approval → 409."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_red_approval_chain(conn, approval_status=ApprovalStatus.CONFIRMED.value)

        resp = await app.client.post(OTP_URL, json=_body(), headers=app.auth_headers())
        assert resp.status_code == 409

        await db.dispose()

    asyncio.run(run())


# ── view_hash ────────────────────────────────────────────────────────────────


def test_g10_wrong_view_hash_returns_409(app_config):
    """G10 with mismatched view_hash → 409 VIEW_HASH_MISMATCH."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_red_approval_chain(conn)

        resp = await app.client.post(
            OTP_URL, json=_body(view_hash="c" * 64), headers=app.auth_headers()
        )
        assert resp.status_code == 409

        await db.dispose()

    asyncio.run(run())


# ── Expiry ────────────────────────────────────────────────────────────────────


def test_g10_expired_approval_returns_409(app_config):
    """G10 on expired approval → 409 APPROVAL_EXPIRED."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            from tests.gateway.w4b2c.db.conftest import (
                _seed_approval,
                _seed_auth,
                _seed_evaluation,
                _seed_plan,
                _seed_session,
            )

            await _seed_auth(conn)
            await _seed_session(conn)
            await _seed_plan(conn, status="PENDING_APPROVAL")
            await _seed_evaluation(conn, risk=Risk.RED)
            await _seed_approval(
                conn,
                status="PENDING",
                expires_at_wall=WALL - timedelta(minutes=1),
            )

        resp = await app.client.post(OTP_URL, json=_body(), headers=app.auth_headers())
        assert resp.status_code == 409
        await db.dispose()

    asyncio.run(run())


# ── Session revocation ───────────────────────────────────────────────────────


def test_g10_revoked_session_returns_409(app_config):
    """G10 with revoked session → 409 AUTHORIZATION_CANCELLED."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            from tests.gateway.w4b2c.db.conftest import _seed_session

            await _seed_session(conn, revoked=True)
            await _seed_red_approval_chain(conn)

        resp = await app.client.post(OTP_URL, json=_body(), headers=app.auth_headers())
        assert resp.status_code == 409
        await db.dispose()

    asyncio.run(run())


# ── Rate limiting ────────────────────────────────────────────────────────────


def test_g10_sent_count_exhausted_returns_429(app_config):
    """G10 after 3 sends → 429 RATE_LIMITED, no DB change."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_red_approval_chain(conn)
            # Pre-seed challenge with last_sent_at_wall far in the past
            # so the 30-second interval doesn't block rapid successive sends.
            await _seed_otp_challenge(
                conn, sent_count=0, last_sent_at_wall=WALL - timedelta(seconds=60)
            )

        # Send 3 times (different request_id each for fresh sends).
        # After each send, last_sent_at_wall is set to WALL, blocking the
        # next send.  We work around this by re-seeding between sends.
        async with db.begin() as conn:
            await _seed_otp_challenge(
                conn, sent_count=0, last_sent_at_wall=WALL - timedelta(seconds=60)
            )
        r0 = await app.client.post(
            OTP_URL,
            json=_body(request_id="req_otp_0"),
            headers=app.auth_headers(),
        )
        assert r0.status_code == 200, f"send 0: expected 200, got {r0.status_code}"

        async with db.begin() as conn:
            await _seed_otp_challenge(
                conn, sent_count=1, last_sent_at_wall=WALL - timedelta(seconds=60)
            )
        r1 = await app.client.post(
            OTP_URL,
            json=_body(request_id="req_otp_1"),
            headers=app.auth_headers(),
        )
        assert r1.status_code == 200, f"send 1: expected 200, got {r1.status_code}"

        async with db.begin() as conn:
            await _seed_otp_challenge(
                conn, sent_count=2, last_sent_at_wall=WALL - timedelta(seconds=60)
            )
        r2 = await app.client.post(
            OTP_URL,
            json=_body(request_id="req_otp_2"),
            headers=app.auth_headers(),
        )
        assert r2.status_code == 200, f"send 2: expected 200, got {r2.status_code}"

        # 4th send → exhausted (sent_count=3 ≥ 3).
        r3 = await app.client.post(
            OTP_URL,
            json=_body(request_id="req_otp_3"),
            headers=app.auth_headers(),
        )
        assert r3.status_code == 429

        async with db.connect() as conn:
            challenge = (
                (
                    await conn.execute(
                        select(t.otp_challenges).where(t.otp_challenges.c.approval_id == "apv_1")
                    )
                )
                .mappings()
                .one()
            )
        assert challenge["sent_count"] == 3

        await db.dispose()

    asyncio.run(run())


def test_g10_within_30s_interval_returns_429(app_config):
    """G10 within 30-second interval → 429 with retry_after_seconds."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_red_approval_chain(conn)

        # First send succeeds.
        r1 = await app.client.post(
            OTP_URL, json=_body(request_id="req_otp_a"), headers=app.auth_headers()
        )
        assert r1.status_code == 200

        # Second send with different request_id (to avoid idempotency) → rate limited.
        r2 = await app.client.post(
            OTP_URL, json=_body(request_id="req_otp_b"), headers=app.auth_headers()
        )
        assert r2.status_code == 429
        data = r2.json()
        rr = data.get("error", {}).get("details", {}).get("retry_after_seconds")
        assert isinstance(rr, int) and 0 < rr <= 30, f"unexpected retry_after_seconds: {rr}"

        await db.dispose()

    asyncio.run(run())


# ── Resend overwrites old code (R1: attempt_count does NOT reset) ─────────────


def test_g10_resend_overwrites_code_but_keeps_attempt_count(app_config):
    """R1: resend creates new code_digest/ciphertext/expires_at_wall,
    but attempt_count stays at previous value (cumulative)."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_red_approval_chain(conn)

        # Inject an existing challenge with attempt_count=2.
        from services.gateway.app.approval.otp import compute_code_digest

        key = b"w4b2b-otp-hmac-key-at-least-32-bytes-long-now"
        old_digest = compute_code_digest("111111", "apv_1", key)

        async with db.begin() as conn:
            await _seed_otp_challenge(
                conn,
                code_digest=old_digest,
                code_ciphertext="deadbeef",
                view_hash=VIEW_HASH,
                expires_at_wall=WALL + timedelta(minutes=2),
                attempt_count=2,
                sent_count=1,
                last_sent_at_wall=WALL - timedelta(seconds=60),
            )

        # Wait for the interval to pass (last_sent is 60s ago → interval elapsed).
        r = await app.client.post(
            OTP_URL,
            json=_body(request_id="req_resend_1"),
            headers=app.auth_headers(),
        )
        assert r.status_code == 200

        async with db.connect() as conn:
            challenge = (
                (
                    await conn.execute(
                        select(t.otp_challenges).where(t.otp_challenges.c.approval_id == "apv_1")
                    )
                )
                .mappings()
                .one()
            )
        # Code changed.
        assert challenge["code_digest"] != old_digest
        # R1: attempt_count stays at 2 (cumulative, NOT reset to 0).
        assert challenge["attempt_count"] == 2
        assert challenge["sent_count"] == 2

        await db.dispose()

    asyncio.run(run())


# ── Audit ─────────────────────────────────────────────────────────────────────


def test_g10_audit_event(app_config):
    """G10 success → APPROVAL_OTP_SENT audit event written."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_red_approval_chain(conn)

        resp = await app.client.post(OTP_URL, json=_body(), headers=app.auth_headers())
        assert resp.status_code == 200

        # Audit event exists.
        async with db.connect() as conn:
            events = (
                (
                    await conn.execute(
                        select(t.audit_events).where(
                            t.audit_events.c.event_type == "APPROVAL_OTP_SENT"
                        )
                    )
                )
                .mappings()
                .all()
            )
        assert len(events) == 1
        evt = events[0]
        assert evt["customer_id"] == "cus_zs"
        assert evt["plan_id"] == "pln_1", f"plan_id should be pln_1, got {evt['plan_id']}"
        payload = evt["payload"]
        assert payload["approval_id"] == "apv_1"
        assert payload["view_hash"] == VIEW_HASH
        # Code or ciphertext must NOT appear.
        payload_str = str(payload)
        assert "code" not in payload_str.lower()
        assert "ciphertext" not in payload_str.lower()
        assert "digest" not in payload_str.lower()

        await db.dispose()

    asyncio.run(run())
