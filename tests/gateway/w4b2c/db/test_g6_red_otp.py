"""W4b2c Task 3: G6 POST /approvals/{approval_id}/confirm RED OTP verification.

Real-PG tests for the red-level confirm path with OTP challenge verification.
All tests expected to FAIL or PASS red-to-green after implementation.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from sqlalchemy import select

from services.gateway.app import tables as t
from services.gateway.app.approval.otp import compute_code_digest
from services.gateway.app.schemas.enums import ApprovalStatus, PlanStatus
from tests.gateway.w4b2c.db.conftest import (
    OTP_KEY,
    WALL,
    _runtime_db,
    _seed_otp_challenge,
    _seed_red_approval_chain,
    build_app,
)

CONFIRM_URL = "/approvals/apv_1/confirm"
VIEW_HASH = "b" * 64

# Precomputed OTP code and its digest for predictable tests.
OTP_CODE = "123456"
# The OTP code_digest_key is derived from OTP_KEY.
from services.gateway.app.approval.otp import derive_otp_keys  # noqa: E402

_CODE_DIGEST_KEY, _ = derive_otp_keys(OTP_KEY)
OTP_DIGEST = compute_code_digest(OTP_CODE, "apv_1", _CODE_DIGEST_KEY)


def _confirm_body(request_id="req_c1", view_hash=VIEW_HASH, risk_ack=True, otp=None):
    d = {"request_id": request_id, "view_hash": view_hash, "risk_ack": risk_ack}
    if otp is not None:
        d["otp"] = otp
    return d


# ── Correct OTP → CONFIRMED ──────────────────────────────────────────────────


def test_g6_red_correct_otp_confirms(app_config):
    """RED approval + correct OTP → 200 CONFIRMED; challenge deleted; audit with otp_verified."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_red_approval_chain(conn)
            await _seed_otp_challenge(conn, code_digest=OTP_DIGEST, view_hash=VIEW_HASH)

        resp = await app.client.post(
            CONFIRM_URL,
            json=_confirm_body(otp=OTP_CODE),
            headers=app.auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "CONFIRMED"

        # Verify DB state.
        async with db.connect() as conn:
            approval = (
                (await conn.execute(select(t.approvals).where(t.approvals.c.id == "apv_1")))
                .mappings()
                .one()
            )
            plan = (
                (await conn.execute(select(t.plans).where(t.plans.c.id == "pln_1")))
                .mappings()
                .one()
            )
            # OTP challenge row deleted.
            challenge = (
                (
                    await conn.execute(
                        select(t.otp_challenges).where(t.otp_challenges.c.approval_id == "apv_1")
                    )
                )
                .mappings()
                .one_or_none()
            )
        assert approval["status"] == ApprovalStatus.CONFIRMED.value
        assert plan["status"] == PlanStatus.CONFIRMED.value
        assert challenge is None, "OTP challenge should be deleted after successful verification"

        # Audit with otp_verified:true.
        async with db.connect() as conn:
            events = (
                (
                    await conn.execute(
                        select(t.audit_events).where(
                            t.audit_events.c.event_type == "APPROVAL_CONFIRMED"
                        )
                    )
                )
                .mappings()
                .all()
            )
        assert len(events) == 1
        assert events[0]["payload"].get("otp_verified") is True

        await db.dispose()

    asyncio.run(run())


# ── Wrong OTP → OTP_INVALID ──────────────────────────────────────────────────


def test_g6_red_wrong_otp_returns_otp_invalid(app_config):
    """RED approval + wrong OTP → 409 OTP_INVALID; attempt_count incremented; DB unchanged."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_red_approval_chain(conn)
            await _seed_otp_challenge(conn, code_digest=OTP_DIGEST, view_hash=VIEW_HASH)

        resp = await app.client.post(
            CONFIRM_URL,
            json=_confirm_body(otp="999999"),
            headers=app.auth_headers(),
        )
        assert resp.status_code == 409

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
            approval = (
                (await conn.execute(select(t.approvals).where(t.approvals.c.id == "apv_1")))
                .mappings()
                .one()
            )
        assert challenge["attempt_count"] == 1
        assert approval["status"] == ApprovalStatus.PENDING.value

        await db.dispose()

    asyncio.run(run())


# ── 5th wrong OTP → LOCKED ───────────────────────────────────────────────────


def test_g6_red_5th_wrong_otp_locks_approval(app_config):
    """RED approval + 5th wrong OTP → 409 OTP_LOCKED; approval PENDING→LOCKED."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_red_approval_chain(conn)
            await _seed_otp_challenge(
                conn,
                code_digest=OTP_DIGEST,
                view_hash=VIEW_HASH,
                attempt_count=4,  # one away from lock
            )

        resp = await app.client.post(
            CONFIRM_URL,
            json=_confirm_body(otp="999999", request_id="req_bad_5"),
            headers=app.auth_headers(),
        )
        assert resp.status_code == 409

        async with db.connect() as conn:
            approval = (
                (await conn.execute(select(t.approvals).where(t.approvals.c.id == "apv_1")))
                .mappings()
                .one()
            )
            plan = (
                (await conn.execute(select(t.plans).where(t.plans.c.id == "pln_1")))
                .mappings()
                .one()
            )
            challenge = (
                (
                    await conn.execute(
                        select(t.otp_challenges).where(t.otp_challenges.c.approval_id == "apv_1")
                    )
                )
                .mappings()
                .one()
            )
        assert approval["status"] == ApprovalStatus.LOCKED.value
        assert challenge["locked_at_wall"] is not None
        # Plan stays PENDING_APPROVAL.
        assert plan["status"] == PlanStatus.PENDING_APPROVAL.value

        await db.dispose()

    asyncio.run(run())


# ── Expired OTP ──────────────────────────────────────────────────────────────


def test_g6_red_expired_otp_returns_otp_expired(app_config):
    """RED approval with expired OTP challenge → 409 OTP_EXPIRED."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_red_approval_chain(conn)
            await _seed_otp_challenge(
                conn,
                code_digest=OTP_DIGEST,
                view_hash=VIEW_HASH,
                expires_at_wall=WALL - timedelta(minutes=1),
            )

        resp = await app.client.post(
            CONFIRM_URL,
            json=_confirm_body(otp=OTP_CODE),
            headers=app.auth_headers(),
        )
        assert resp.status_code == 409

        # DB unchanged.
        async with db.connect() as conn:
            approval = (
                (await conn.execute(select(t.approvals).where(t.approvals.c.id == "apv_1")))
                .mappings()
                .one()
            )
        assert approval["status"] == ApprovalStatus.PENDING.value

        await db.dispose()

    asyncio.run(run())


# ── No challenge at all ──────────────────────────────────────────────────────


def test_g6_red_no_challenge_returns_otp_expired(app_config):
    """RED approval without any OTP sent → 409 OTP_EXPIRED."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_red_approval_chain(conn)
            # No _seed_otp_challenge call.

        resp = await app.client.post(
            CONFIRM_URL,
            json=_confirm_body(otp=OTP_CODE),
            headers=app.auth_headers(),
        )
        assert resp.status_code == 409

        # DB unchanged.
        async with db.connect() as conn:
            approval = (
                (await conn.execute(select(t.approvals).where(t.approvals.c.id == "apv_1")))
                .mappings()
                .one()
            )
        assert approval["status"] == ApprovalStatus.PENDING.value

        await db.dispose()

    asyncio.run(run())


# ── otp=null (R2: OTP_EXPIRED) ───────────────────────────────────────────────


def test_g6_red_otp_null_returns_otp_expired(app_config):
    """RED approval with otp=null → 409 OTP_EXPIRED (R2)."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_red_approval_chain(conn)
            await _seed_otp_challenge(conn, code_digest=OTP_DIGEST, view_hash=VIEW_HASH)

        resp = await app.client.post(
            CONFIRM_URL,
            json=_confirm_body(otp=None),
            headers=app.auth_headers(),
        )
        assert resp.status_code == 409

        # attempt_count did NOT change.
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
        assert challenge["attempt_count"] == 0

        await db.dispose()

    asyncio.run(run())


# ── risk_ack check still applies to red confirm ──────────────────────────────


def test_g6_red_risk_ack_required_without_ack_returns_409(app_config):
    """RED confirm with risk_ack_required but risk_ack=false → 409 even with correct OTP."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_red_approval_chain(conn)
            await _seed_otp_challenge(conn, code_digest=OTP_DIGEST, view_hash=VIEW_HASH)

        resp = await app.client.post(
            CONFIRM_URL,
            json=_confirm_body(otp=OTP_CODE, risk_ack=False),
            headers=app.auth_headers(),
        )
        assert resp.status_code == 409  # RISK_ACK_REQUIRED

        # OTP challenge NOT consumed (risk_ack check fires after OTP).
        async with db.connect() as conn:
            challenge = (
                (
                    await conn.execute(
                        select(t.otp_challenges).where(t.otp_challenges.c.approval_id == "apv_1")
                    )
                )
                .mappings()
                .one_or_none()
            )
        assert challenge is not None, "OTP should not be consumed when risk_ack fails"

        await db.dispose()

    asyncio.run(run())


# ── LOCKED approvals blocked ─────────────────────────────────────────────────


def test_g6_locked_approval_returns_state_changed(app_config):
    """LOCKED approval → 409 STATE_CHANGED (not OTP_LOCKED, from _validate_confirmable)."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_red_approval_chain(conn, approval_status=ApprovalStatus.LOCKED.value)
            await _seed_otp_challenge(conn, code_digest=OTP_DIGEST, view_hash=VIEW_HASH)

        resp = await app.client.post(
            CONFIRM_URL,
            json=_confirm_body(otp=OTP_CODE),
            headers=app.auth_headers(),
        )
        assert resp.status_code == 409
        await db.dispose()

    asyncio.run(run())


# ── Idempotency ──────────────────────────────────────────────────────────────


def test_g6_red_confirm_idempotent(app_config):
    """Same request_id replay of red confirm returns original CONFIRMED result."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_red_approval_chain(conn)
            await _seed_otp_challenge(conn, code_digest=OTP_DIGEST, view_hash=VIEW_HASH)

        body = _confirm_body(otp=OTP_CODE)
        r1 = await app.client.post(CONFIRM_URL, json=body, headers=app.auth_headers())
        assert r1.status_code == 200

        r2 = await app.client.post(CONFIRM_URL, json=body, headers=app.auth_headers())
        assert r2.status_code == 200
        assert r2.json()["status"] == "CONFIRMED"

        # Only one audit event.
        async with db.connect() as conn:
            events = (
                (
                    await conn.execute(
                        select(t.audit_events).where(
                            t.audit_events.c.event_type == "APPROVAL_CONFIRMED"
                        )
                    )
                )
                .mappings()
                .all()
            )
        assert len(events) == 1

        await db.dispose()

    asyncio.run(run())


def test_g6_red_confirm_different_request_id_after_confirm_fails(app_config):
    """Different request_id on already CONFIRMED approval → 409."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_red_approval_chain(conn)
            await _seed_otp_challenge(conn, code_digest=OTP_DIGEST, view_hash=VIEW_HASH)

        r1 = await app.client.post(
            CONFIRM_URL,
            json=_confirm_body(otp=OTP_CODE, request_id="req_a"),
            headers=app.auth_headers(),
        )
        assert r1.status_code == 200

        r2 = await app.client.post(
            CONFIRM_URL,
            json=_confirm_body(otp=OTP_CODE, request_id="req_b"),
            headers=app.auth_headers(),
        )
        assert r2.status_code == 409
        await db.dispose()

    asyncio.run(run())
