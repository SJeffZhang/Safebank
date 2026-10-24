"""W4b2b Task 2: G6 POST /approvals/{approval_id}/confirm real-PG tests.

Covers: yellow confirm, risk_ack logic, red reject (D6), idempotency,
expiry, view_hash mismatch, wrong customer, session revocation, audit.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from safebank_contracts.enums import Risk
from sqlalchemy import select

from services.gateway.app import tables as t
from services.gateway.app.schemas.enums import ApprovalStatus, PlanStatus
from tests.gateway.w4b2b.db.conftest import (
    WALL,
    _runtime_db,
    _seed_auth,
    _seed_full,
    build_app,
)

CONFIRM_URL = "/approvals/apv_1/confirm"
VIEW_HASH = "b" * 64


def _body(request_id="req_c1", view_hash=VIEW_HASH, risk_ack=True, otp=None):
    d = {"request_id": request_id, "view_hash": view_hash, "risk_ack": risk_ack}
    if otp is not None:
        d["otp"] = otp
    return d


def test_confirm_yellow_succeeds(app_config):
    """Yellow confirm with correct view_hash and risk_ack transitions to CONFIRMED."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        view_json = {"risk_ack_required": True}
        async with db.begin() as conn:
            await _seed_full(conn, view_json=view_json)

        resp = await app.client.post(CONFIRM_URL, json=_body(), headers=app.auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "CONFIRMED"
        assert data["approval_id"] == "apv_1"
        assert data["plan_id"] == "pln_1"

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
        assert approval["status"] == ApprovalStatus.CONFIRMED.value
        assert approval["risk_ack"] is True
        assert approval["confirmed_at_wall"] is not None
        assert plan["status"] == PlanStatus.CONFIRMED.value

        # Audit event exists.
        async with db.connect() as conn:
            count = await conn.scalar(
                select(t.audit_events.c.event_type)
                .where(t.audit_events.c.event_type == "APPROVAL_CONFIRMED")
                .limit(1)
            )
        assert count is not None
        await db.dispose()

    asyncio.run(run())


def test_confirm_idempotent(app_config):
    """Same request_id returns the original result without double-transitioning."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        view_json = {"risk_ack_required": True}
        async with db.begin() as conn:
            await _seed_full(conn, view_json=view_json)

        body = _body()
        # First confirm.
        r1 = await app.client.post(CONFIRM_URL, json=body, headers=app.auth_headers())
        assert r1.status_code == 200

        # Second confirm with same request_id — idempotent.
        r2 = await app.client.post(CONFIRM_URL, json=body, headers=app.auth_headers())
        assert r2.status_code == 200
        assert r2.json()["status"] == "CONFIRMED"

        # Only one audit event (first call only, replay skipped).
        async with db.connect() as conn:
            rows = (
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
        assert len(rows) == 1
        await db.dispose()

    asyncio.run(run())


def test_confirm_different_request_id_after_confirm_fails(app_config):
    """A different request_id on an already CONFIRMED approval returns 409."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        view_json = {"risk_ack_required": True}
        async with db.begin() as conn:
            await _seed_full(conn, view_json=view_json)

        r1 = await app.client.post(
            CONFIRM_URL, json=_body(request_id="req_a"), headers=app.auth_headers()
        )
        assert r1.status_code == 200

        # Different request_id — approval is no longer PENDING.
        r2 = await app.client.post(
            CONFIRM_URL, json=_body(request_id="req_b"), headers=app.auth_headers()
        )
        assert r2.status_code == 409
        await db.dispose()

    asyncio.run(run())


def test_confirm_requires_auth(app_config):
    """G6 without auth returns 401."""

    async def run():
        app = await build_app(app_config)
        resp = await app.client.post(CONFIRM_URL, json=_body())
        assert resp.status_code == 401

    asyncio.run(run())


def test_confirm_nonexistent_approval_returns_404(app_config):
    """Confirm on nonexistent approval returns 404."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_auth(conn)

        resp = await app.client.post(CONFIRM_URL, json=_body(), headers=app.auth_headers())
        assert resp.status_code == 404

    asyncio.run(run())


def test_confirm_wrong_customer_returns_404(app_config):
    """Confirm for another customer's approval returns 404."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_full(conn, customer="cus_zs")
            # Seed a separate auth row for cus_other with a distinct jti.
            await _seed_auth(conn, jti="other-jti", customer="cus_other")

        resp = await app.client.post(
            CONFIRM_URL,
            json=_body(),
            headers=app.auth_headers(customer_id="cus_other", jti="other-jti"),
        )
        assert resp.status_code == 404
        await db.dispose()

    asyncio.run(run())


def test_confirm_wrong_view_hash_returns_409(app_config):
    """Confirm with mismatched view_hash returns 409 VIEW_HASH_MISMATCH."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_full(conn)

        resp = await app.client.post(
            CONFIRM_URL,
            json=_body(view_hash="c" * 64),
            headers=app.auth_headers(),
        )
        assert resp.status_code == 409
        await db.dispose()

    asyncio.run(run())


def test_confirm_expired_approval_returns_409(app_config):
    """Confirm on an expired approval returns 409 APPROVAL_EXPIRED."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_full(conn, expires_at_wall=WALL - timedelta(minutes=1))

        resp = await app.client.post(CONFIRM_URL, json=_body(), headers=app.auth_headers())
        assert resp.status_code == 409
        await db.dispose()

    asyncio.run(run())


def test_confirm_already_confirmed_returns_409(app_config):
    """Confirm on a CONFIRMED approval returns 409 (not PENDING)."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_full(conn, approval_status=ApprovalStatus.CONFIRMED.value)

        resp = await app.client.post(CONFIRM_URL, json=_body(), headers=app.auth_headers())
        assert resp.status_code == 409
        await db.dispose()

    asyncio.run(run())


def test_confirm_revoked_session_returns_409(app_config):
    """Confirm with a revoked session returns 409."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            from tests.gateway.w4b2b.db.conftest import _seed_session

            await _seed_session(conn, revoked=True)
            await _seed_full(conn)

        resp = await app.client.post(CONFIRM_URL, json=_body(), headers=app.auth_headers())
        assert resp.status_code == 409
        await db.dispose()

    asyncio.run(run())


def test_confirm_risk_ack_false_when_required_returns_409(app_config):
    """Confirm with risk_ack=false when panel requires it returns 409."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        view_json = {"risk_ack_required": True}
        async with db.begin() as conn:
            await _seed_full(conn, view_json=view_json)

        resp = await app.client.post(
            CONFIRM_URL,
            json=_body(risk_ack=False),
            headers=app.auth_headers(),
        )
        assert resp.status_code == 409
        await db.dispose()

    asyncio.run(run())


def test_confirm_risk_ack_false_when_not_required_succeeds(app_config):
    """Confirm with risk_ack=false when panel does NOT require it succeeds (D4)."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        view_json = {"risk_ack_required": False}
        async with db.begin() as conn:
            await _seed_full(conn, view_json=view_json)

        resp = await app.client.post(
            CONFIRM_URL,
            json=_body(risk_ack=False),
            headers=app.auth_headers(),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "CONFIRMED"
        await db.dispose()

    asyncio.run(run())


# ── RED OTP tests (R3: W4b2c implements real OTP, no more 503) ─────────


def test_confirm_red_no_challenge_returns_otp_expired(app_config):
    """RED approval + no OTP sent → 409 OTP_EXPIRED (R3-a). DB unchanged."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        view_json = {"risk_ack_required": True, "otp_required": True}
        async with db.begin() as conn:
            await _seed_full(conn, risk=Risk.RED, view_json=view_json)

        resp = await app.client.post(
            CONFIRM_URL, json=_body(otp="123456"), headers=app.auth_headers()
        )
        assert resp.status_code == 409

        # DB unchanged — approval still PENDING.
        async with db.connect() as conn:
            approval = (
                (await conn.execute(select(t.approvals).where(t.approvals.c.id == "apv_1")))
                .mappings()
                .one()
            )
        assert approval["status"] == ApprovalStatus.PENDING.value
        await db.dispose()

    asyncio.run(run())


def test_confirm_red_with_otp_succeeds(app_config):
    """RED approval + pre-seeded OTP challenge + correct code → 200 CONFIRMED (R3-b)."""

    from services.gateway.app.approval.otp import compute_code_digest, derive_otp_keys
    from tests.gateway.w4b2b.db.conftest import OTP_KEY

    _code_key, _aead_key = derive_otp_keys(OTP_KEY)
    OTP_CODE = "123456"
    OTP_DIGEST = compute_code_digest(OTP_CODE, "apv_1", _code_key)

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        view_json = {"risk_ack_required": True, "otp_required": True}
        async with db.begin() as conn:
            from sqlalchemy.dialects.postgresql import insert as pg_insert

            await _seed_full(conn, risk=Risk.RED, view_json=view_json)
            # Pre-seed an OTP challenge matching our known code.
            await conn.execute(
                pg_insert(t.otp_challenges)
                .values(
                    approval_id="apv_1",
                    code_digest=OTP_DIGEST,
                    code_ciphertext="deadbeef",
                    view_hash=VIEW_HASH,
                    expires_at_wall=WALL + timedelta(minutes=2),
                    attempt_count=0,
                    sent_count=1,
                    last_sent_at_wall=WALL,
                )
                .on_conflict_do_update(
                    index_elements=["approval_id"],
                    set_={
                        "code_digest": OTP_DIGEST,
                        "code_ciphertext": "deadbeef",
                        "view_hash": VIEW_HASH,
                        "expires_at_wall": WALL + timedelta(minutes=2),
                        "attempt_count": 0,
                        "sent_count": 1,
                        "last_sent_at_wall": WALL,
                    },
                )
            )

        resp = await app.client.post(
            CONFIRM_URL,
            json=_body(otp=OTP_CODE),
            headers=app.auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "CONFIRMED"

        # DB: approval CONFIRMED, OTP challenge deleted.
        async with db.connect() as conn:
            approval = (
                (await conn.execute(select(t.approvals).where(t.approvals.c.id == "apv_1")))
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
                .one_or_none()
            )
        assert approval["status"] == ApprovalStatus.CONFIRMED.value
        assert challenge is None, "OTP challenge should be deleted after successful verification"

        await db.dispose()

    asyncio.run(run())


def test_confirm_requires_approve_scope(app_config):
    """Confirm with only app scope (no approve) returns 403."""

    async def run():
        app = await build_app(app_config)
        resp = await app.client.post(
            CONFIRM_URL,
            json=_body(),
            headers=app.auth_headers(scope=["app"]),
        )
        assert resp.status_code == 403

    asyncio.run(run())


def test_confirm_plan_not_pending_approval_returns_409(app_config):
    """Confirm when plan is not in PENDING_APPROVAL returns 409."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_full(conn, plan_status=PlanStatus.CONFIRMED.value)

        resp = await app.client.post(CONFIRM_URL, json=_body(), headers=app.auth_headers())
        assert resp.status_code == 409
        await db.dispose()

    asyncio.run(run())


def test_confirm_binding_mac_tampered_returns_409(app_config):
    """Confirm with a tampered binding_mac returns 409 APPROVAL_BINDING_MISMATCH."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_full(conn)
            # Tamper the binding MAC directly.
            await conn.execute(
                t.approvals.update()
                .where(t.approvals.c.id == "apv_1")
                .values(binding_mac="deadbeef" * 8)
            )

        resp = await app.client.post(CONFIRM_URL, json=_body(), headers=app.auth_headers())
        assert resp.status_code == 409
        await db.dispose()

    asyncio.run(run())


def test_confirm_superseded_returns_409(app_config):
    """Confirm on a SUPERSEDED approval returns 409, DB unchanged."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_full(conn, approval_status=ApprovalStatus.SUPERSEDED.value)

        resp = await app.client.post(CONFIRM_URL, json=_body(), headers=app.auth_headers())
        assert resp.status_code == 409

        # DB unchanged — approval still SUPERSEDED.
        async with db.connect() as conn:
            approval = (
                (await conn.execute(select(t.approvals).where(t.approvals.c.id == "apv_1")))
                .mappings()
                .one()
            )
        assert approval["status"] == ApprovalStatus.SUPERSEDED.value
        await db.dispose()

    asyncio.run(run())


def test_confirm_used_returns_409(app_config):
    """Confirm on a USED approval returns 409, DB unchanged."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_full(conn, approval_status=ApprovalStatus.USED.value)

        resp = await app.client.post(CONFIRM_URL, json=_body(), headers=app.auth_headers())
        assert resp.status_code == 409

        # DB unchanged — approval still USED.
        async with db.connect() as conn:
            approval = (
                (await conn.execute(select(t.approvals).where(t.approvals.c.id == "apv_1")))
                .mappings()
                .one()
            )
        assert approval["status"] == ApprovalStatus.USED.value
        await db.dispose()

    asyncio.run(run())
