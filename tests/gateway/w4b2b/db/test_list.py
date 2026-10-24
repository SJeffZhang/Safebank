"""W4b2b Task 2: G17 GET /approvals real-PG tests.

Covers: empty list, status filter (PENDING/CONFIRMED/REJECTED/ALL), invalid
status, cursor pagination, correct field mapping including risk via JOIN (D3).
"""

from __future__ import annotations

import asyncio

from safebank_contracts.enums import Risk

from tests.gateway.w4b2b.db.conftest import _runtime_db, _seed_auth, _seed_full, build_app


def test_list_empty(app_config):
    """G17 returns empty list when no approvals exist."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_auth(conn)

        resp = await app.client.get("/approvals", headers=app.auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["next_cursor"] is None

    asyncio.run(run())


def test_list_single_pending(app_config):
    """G17 returns a single PENDING approval with risk from JOIN."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_full(conn, approval_id="apv_a")

        resp = await app.client.get("/approvals", headers=app.auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert item["approval_id"] == "apv_a"
        assert item["plan_id"] == "pln_1"
        assert item["session_id"] == "ses_1"
        assert item["status"] == "PENDING"
        assert item["risk"] == "YELLOW"
        assert item["expires_at_wall"] is not None
        assert data["next_cursor"] is None
        await db.dispose()

    asyncio.run(run())


def test_list_filter_by_status(app_config):
    """G17 filters by status parameter."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_full(conn, approval_id="apv_a", approval_status="PENDING")
            await _seed_full(
                conn,
                sid="ses_2",
                plan_id="pln_2",
                evaluation_id="evl_2",
                approval_id="apv_b",
                approval_status="CONFIRMED",
            )
            await _seed_full(
                conn,
                sid="ses_3",
                plan_id="pln_3",
                evaluation_id="evl_3",
                approval_id="apv_c",
                approval_status="REJECTED",
            )

        # Filter PENDING only.
        resp = await app.client.get("/approvals?status=PENDING", headers=app.auth_headers())
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["status"] == "PENDING"

        # Filter CONFIRMED only.
        resp = await app.client.get("/approvals?status=CONFIRMED", headers=app.auth_headers())
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["status"] == "CONFIRMED"
        await db.dispose()

    asyncio.run(run())


def test_list_invalid_status_returns_400(app_config):
    """G17 with invalid status filter returns 400."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_auth(conn)

        resp = await app.client.get("/approvals?status=INVALID", headers=app.auth_headers())
        assert resp.status_code == 400

    asyncio.run(run())


def test_list_cursor_pagination(app_config):
    """G17 paginates correctly with cursor (D5: ULID DESC)."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            # Seed 3 approvals with different ULIDs.
            await _seed_full(
                conn, approval_id="apv_aaa", sid="ses_a", plan_id="pln_a", evaluation_id="evl_a"
            )
            await _seed_full(
                conn, approval_id="apv_bbb", sid="ses_b", plan_id="pln_b", evaluation_id="evl_b"
            )
            await _seed_full(
                conn, approval_id="apv_ccc", sid="ses_c", plan_id="pln_c", evaluation_id="evl_c"
            )

        # First page, limit 2.
        resp = await app.client.get("/approvals?limit=2", headers=app.auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 2
        # Newest first (descending by ULID): ccc, bbb
        assert data["items"][0]["approval_id"] == "apv_ccc"
        assert data["items"][1]["approval_id"] == "apv_bbb"
        cursor = data["next_cursor"]
        assert cursor is not None

        # Second page.
        resp = await app.client.get(
            f"/approvals?limit=2&cursor={cursor}", headers=app.auth_headers()
        )
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["approval_id"] == "apv_aaa"
        assert data["next_cursor"] is None
        await db.dispose()

    asyncio.run(run())


def test_list_respects_customer_boundary(app_config):
    """G17 only returns approvals for the caller's customer."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_full(conn, approval_id="apv_zs", customer="cus_zs")
            await _seed_full(
                conn,
                sid="ses_o",
                plan_id="pln_o",
                evaluation_id="evl_o",
                approval_id="apv_other",
                customer="cus_other",
            )

        resp = await app.client.get("/approvals", headers=app.auth_headers(customer_id="cus_zs"))
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["approval_id"] == "apv_zs"
        await db.dispose()

    asyncio.run(run())


def test_list_requires_app_scope(app_config):
    """G17 with no app scope returns 403."""

    async def run():
        app = await build_app(app_config)
        resp = await app.client.get(
            "/approvals",
            headers=app.auth_headers(scope=[]),
        )
        # Empty scope won't match F_APP requirement.
        assert resp.status_code == 403

    asyncio.run(run())


def test_list_shows_risk_via_join(app_config):
    """G17 shows risk from evaluations table (D3: JOIN, not approvals column)."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_full(conn, approval_id="apv_r", risk=Risk.RED)

        resp = await app.client.get("/approvals", headers=app.auth_headers())
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["risk"] == "RED"
        await db.dispose()

    asyncio.run(run())
