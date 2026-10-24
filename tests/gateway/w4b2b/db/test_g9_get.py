"""W4b2b Task 1: G9 GET /approvals/{approval_id} real-PG tests.

Uses the isolated tmpfs PostgreSQL fixture. Never touches long-lived volumes.
"""

from __future__ import annotations

import asyncio

import pytest
from safebank_contracts.codes import Code
from sqlalchemy import select
from sqlalchemy.pool import NullPool

from services.gateway.app import tables as t
from services.gateway.app.db import engine
from services.gateway.app.schemas.enums import ApprovalStatus
from tests.gateway.w4b2b.db.conftest import (
    _runtime_db,
    _seed_full,
)


def test_g9_returns_planview(test_app):
    """G9 returns the persisted view_json for a PENDING approval."""
    async def run():
        db = _runtime_db(test_app)
        view_json = {"plan_id": "pln_1", "risk": "YELLOW", "status": "PENDING_APPROVAL"}
        async with db.begin() as conn:
            await _seed_full(conn, view_json=view_json)

        resp = await test_app.client.get(
            "/approvals/apv_1", headers=test_app.auth_headers()
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["plan_id"] == "pln_1"
        # D2: status overridden from approvals row
        assert data["status"] == "PENDING"
        await db.dispose()

    asyncio.run(run())


def test_g9_requires_auth(test_app):
    """G9 without auth headers returns 401."""
    async def run():
        resp = await test_app.client.get("/approvals/apv_1")
        assert resp.status_code == 401
    asyncio.run(run())


def test_g9_nonexistent_approval_returns_404(test_app):
    """G9 on a nonexistent approval returns 404."""
    async def run():
        resp = await test_app.client.get(
            "/approvals/apv_nonexistent", headers=test_app.auth_headers()
        )
        assert resp.status_code == 404
    asyncio.run(run())


def test_g9_wrong_customer_returns_404(test_app):
    """G9 for an approval owned by another customer returns 404."""
    async def run():
        db = _runtime_db(test_app)
        async with db.begin() as conn:
            # Seed with customer cus_zs
            await _seed_full(conn, customer="cus_zs", approval_id="apv_zs")

        # Access as cus_other
        resp = await test_app.client.get(
            "/approvals/apv_zs",
            headers=test_app.auth_headers(customer_id="cus_other"),
        )
        assert resp.status_code == 404
        await db.dispose()

    asyncio.run(run())


def test_g9_locked_returns_409(test_app):
    """G9 on a LOCKED approval returns 409 OTP_LOCKED."""
    async def run():
        db = _runtime_db(test_app)
        async with db.begin() as conn:
            await _seed_full(conn, approval_status=ApprovalStatus.LOCKED.value)

        resp = await test_app.client.get(
            "/approvals/apv_1", headers=test_app.auth_headers()
        )
        assert resp.status_code == 409
        await db.dispose()

    asyncio.run(run())


def test_g9_confirmed_still_readable(test_app):
    """G9 can still read a CONFIRMED approval (historical panel)."""
    async def run():
        db = _runtime_db(test_app)
        async with db.begin() as conn:
            await _seed_full(conn, approval_status=ApprovalStatus.CONFIRMED.value)

        resp = await test_app.client.get(
            "/approvals/apv_1", headers=test_app.auth_headers()
        )
        assert resp.status_code == 200
        data = resp.json()
        # D2: status reflects current approval state
        assert data["status"] == "CONFIRMED"
        await db.dispose()

    asyncio.run(run())


def test_g9_rejected_still_readable(test_app):
    """G9 can still read a REJECTED approval (historical panel)."""
    async def run():
        db = _runtime_db(test_app)
        async with db.begin() as conn:
            await _seed_full(conn, approval_status=ApprovalStatus.REJECTED.value)

        resp = await test_app.client.get(
            "/approvals/apv_1", headers=test_app.auth_headers()
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "REJECTED"
        await db.dispose()

    asyncio.run(run())


def test_g9_expired_still_readable(test_app):
    """G9 can still read an EXPIRED approval."""
    async def run():
        db = _runtime_db(test_app)
        async with db.begin() as conn:
            await _seed_full(conn, approval_status=ApprovalStatus.EXPIRED.value)

        resp = await test_app.client.get(
            "/approvals/apv_1", headers=test_app.auth_headers()
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "EXPIRED"
        await db.dispose()

    asyncio.run(run())


def test_g9_requires_approve_scope(test_app):
    """G9 with only app scope (no approve) returns 403."""
    async def run():
        # Token with only "app" scope, not "approve"
        resp = await test_app.client.get(
            "/approvals/apv_1",
            headers=test_app.auth_headers(scope=["app"]),
        )
        assert resp.status_code == 403
    asyncio.run(run())