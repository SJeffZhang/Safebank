"""W4b2b Task 2: G7 POST /approvals/{approval_id}/reject real-PG tests.

Covers: reject with valid view_hash, idempotency, expiry, view_hash mismatch,
wrong customer, session revocation, already-confirmed/rejected/superseded/expired,
audit events.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

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

REJECT_URL = "/approvals/apv_1/reject"
VIEW_HASH = "b" * 64


def _body(request_id="req_r1", view_hash=VIEW_HASH):
    return {"request_id": request_id, "view_hash": view_hash}


def test_reject_succeeds(app_config):
    """Reject with correct view_hash transitions to REJECTED."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_full(conn)

        resp = await app.client.post(REJECT_URL, json=_body(), headers=app.auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "REJECTED"
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
        assert approval["status"] == ApprovalStatus.REJECTED.value
        assert plan["status"] == PlanStatus.REJECTED.value

        # Audit event exists.
        async with db.connect() as conn:
            count = await conn.scalar(
                select(t.audit_events.c.event_type)
                .where(t.audit_events.c.event_type == "APPROVAL_REJECTED")
                .limit(1)
            )
        assert count is not None
        await db.dispose()

    asyncio.run(run())


def test_reject_idempotent(app_config):
    """Same request_id returns the original result without double-transitioning."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_full(conn)

        body = _body()
        r1 = await app.client.post(REJECT_URL, json=body, headers=app.auth_headers())
        assert r1.status_code == 200

        r2 = await app.client.post(REJECT_URL, json=body, headers=app.auth_headers())
        assert r2.status_code == 200
        assert r2.json()["status"] == "REJECTED"

        # Only one audit event.
        async with db.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        select(t.audit_events).where(
                            t.audit_events.c.event_type == "APPROVAL_REJECTED"
                        )
                    )
                )
                .mappings()
                .all()
            )
        assert len(rows) == 1
        await db.dispose()

    asyncio.run(run())


def test_reject_requires_auth(app_config):
    """G7 without auth returns 401."""

    async def run():
        app = await build_app(app_config)
        resp = await app.client.post(REJECT_URL, json=_body())
        assert resp.status_code == 401

    asyncio.run(run())


def test_reject_nonexistent_approval_returns_404(app_config):
    """Reject on nonexistent approval returns 404."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_auth(conn)

        resp = await app.client.post(REJECT_URL, json=_body(), headers=app.auth_headers())
        assert resp.status_code == 404

    asyncio.run(run())


def test_reject_wrong_customer_returns_404(app_config):
    """Reject for another customer's approval returns 404."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_full(conn, customer="cus_zs")
            await _seed_auth(conn, jti="other-jti", customer="cus_other")

        resp = await app.client.post(
            REJECT_URL,
            json=_body(),
            headers=app.auth_headers(customer_id="cus_other", jti="other-jti"),
        )
        assert resp.status_code == 404
        await db.dispose()

    asyncio.run(run())


def test_reject_wrong_view_hash_returns_409(app_config):
    """Reject with mismatched view_hash returns 409."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_full(conn)

        resp = await app.client.post(
            REJECT_URL,
            json=_body(view_hash="c" * 64),
            headers=app.auth_headers(),
        )
        assert resp.status_code == 409
        await db.dispose()

    asyncio.run(run())


def test_reject_expired_approval_returns_409(app_config):
    """Reject on expired approval returns 409."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_full(conn, expires_at_wall=WALL - timedelta(minutes=1))

        resp = await app.client.post(REJECT_URL, json=_body(), headers=app.auth_headers())
        assert resp.status_code == 409
        await db.dispose()

    asyncio.run(run())


def test_reject_already_confirmed_returns_409(app_config):
    """Reject on a CONFIRMED approval returns 409."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_full(conn, approval_status=ApprovalStatus.CONFIRMED.value)

        resp = await app.client.post(REJECT_URL, json=_body(), headers=app.auth_headers())
        assert resp.status_code == 409
        await db.dispose()

    asyncio.run(run())


def test_reject_already_rejected_returns_409(app_config):
    """Reject on a REJECTED approval returns 409."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_full(conn, approval_status=ApprovalStatus.REJECTED.value)

        resp = await app.client.post(REJECT_URL, json=_body(), headers=app.auth_headers())
        assert resp.status_code == 409
        await db.dispose()

    asyncio.run(run())


def test_reject_revoked_session_returns_409(app_config):
    """Reject with revoked session returns 409."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            from tests.gateway.w4b2b.db.conftest import _seed_session

            await _seed_session(conn, revoked=True)
            await _seed_full(conn)

        resp = await app.client.post(REJECT_URL, json=_body(), headers=app.auth_headers())
        assert resp.status_code == 409
        await db.dispose()

    asyncio.run(run())


def test_reject_requires_approve_scope(app_config):
    """Reject with only app scope (no approve) returns 403."""

    async def run():
        app = await build_app(app_config)
        resp = await app.client.post(
            REJECT_URL,
            json=_body(),
            headers=app.auth_headers(scope=["app"]),
        )
        assert resp.status_code == 403

    asyncio.run(run())


def test_reject_plan_not_pending_approval_returns_409(app_config):
    """Reject when plan is not in PENDING_APPROVAL returns 409."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_full(conn, plan_status=PlanStatus.CONFIRMED.value)

        resp = await app.client.post(REJECT_URL, json=_body(), headers=app.auth_headers())
        assert resp.status_code == 409
        await db.dispose()

    asyncio.run(run())
