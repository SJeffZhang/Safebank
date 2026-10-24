"""W4b2b Task 1: G9 GET /approvals/{approval_id} real-PG tests.

Uses the isolated tmpfs PostgreSQL fixture. Never touches long-lived volumes.
"""

from __future__ import annotations

import asyncio

from services.gateway.app.schemas.enums import ApprovalStatus
from tests.gateway.w4b2b.db.conftest import (
    _runtime_db,
    _seed_auth,
    _seed_full,
    build_app,
)


def test_g9_returns_planview(app_config):
    """G9 returns the persisted view_json for a PENDING approval."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        view_json = {"plan_id": "pln_1", "risk": "YELLOW", "status": "PENDING_APPROVAL"}
        async with db.begin() as conn:
            await _seed_full(conn, view_json=view_json)

        resp = await app.client.get("/approvals/apv_1", headers=app.auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["plan_id"] == "pln_1"
        # D2: status overridden from approvals row
        assert data["status"] == "PENDING"
        await db.dispose()

    asyncio.run(run())


def test_g9_requires_auth(app_config):
    """G9 without auth headers returns 401."""

    async def run():
        app = await build_app(app_config)
        resp = await app.client.get("/approvals/apv_1")
        assert resp.status_code == 401

    asyncio.run(run())


def test_g9_nonexistent_approval_returns_404(app_config):
    """G9 on a nonexistent approval returns 404."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_auth(conn)

        resp = await app.client.get("/approvals/apv_nonexistent", headers=app.auth_headers())
        assert resp.status_code == 404

    asyncio.run(run())


def test_g9_wrong_customer_returns_404(app_config):
    """G9 for an approval owned by another customer returns 404."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            # Seed with customer cus_zs
            await _seed_full(conn, customer="cus_zs", approval_id="apv_zs")
            await _seed_auth(conn, jti="other-jti", customer="cus_other")

        # Access as cus_other
        resp = await app.client.get(
            "/approvals/apv_zs",
            headers=app.auth_headers(customer_id="cus_other", jti="other-jti"),
        )
        assert resp.status_code == 404
        await db.dispose()

    asyncio.run(run())


def test_g9_locked_returns_409(app_config):
    """G9 on a LOCKED approval returns 409 OTP_LOCKED."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_full(conn, approval_status=ApprovalStatus.LOCKED.value)

        resp = await app.client.get("/approvals/apv_1", headers=app.auth_headers())
        assert resp.status_code == 409
        await db.dispose()

    asyncio.run(run())


def test_g9_confirmed_still_readable(app_config):
    """G9 can still read a CONFIRMED approval (historical panel)."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_full(conn, approval_status=ApprovalStatus.CONFIRMED.value)

        resp = await app.client.get("/approvals/apv_1", headers=app.auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        # D2: status reflects current approval state
        assert data["status"] == "CONFIRMED"
        await db.dispose()

    asyncio.run(run())


def test_g9_rejected_still_readable(app_config):
    """G9 can still read a REJECTED approval (historical panel)."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_full(conn, approval_status=ApprovalStatus.REJECTED.value)

        resp = await app.client.get("/approvals/apv_1", headers=app.auth_headers())
        assert resp.status_code == 200
        assert resp.json()["status"] == "REJECTED"
        await db.dispose()

    asyncio.run(run())


def test_g9_expired_still_readable(app_config):
    """G9 can still read an EXPIRED approval."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_full(conn, approval_status=ApprovalStatus.EXPIRED.value)

        resp = await app.client.get("/approvals/apv_1", headers=app.auth_headers())
        assert resp.status_code == 200
        assert resp.json()["status"] == "EXPIRED"
        await db.dispose()

    asyncio.run(run())


def test_g9_requires_approve_scope(app_config):
    """G9 with only app scope (no approve) returns 403."""

    async def run():
        app = await build_app(app_config)
        # Token with only "app" scope, not "approve"
        resp = await app.client.get(
            "/approvals/apv_1",
            headers=app.auth_headers(scope=["app"]),
        )
        assert resp.status_code == 403

    asyncio.run(run())


def test_g9_view_json_approval_id_mismatch_returns_409(app_config):
    """G9 returns 409 when view_json.approval_id does not match the path
    approval_id. A misrouted or tampered panel must be rejected fail-closed."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        # Seed with view_json whose approval_id differs from the *actual*
        # approval row id.
        view_json = {"plan_id": "pln_1", "risk": "YELLOW", "approval_id": "apv_other"}
        async with db.begin() as conn:
            await _seed_full(conn, view_json=view_json)

        resp = await app.client.get("/approvals/apv_1", headers=app.auth_headers())
        assert resp.status_code == 409
        await db.dispose()

    asyncio.run(run())
