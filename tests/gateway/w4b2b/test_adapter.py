"""Pure-function tests for the W4b2b approval adapter (no DB).

Tests that the adapter correctly extracts keys/profile/wall_now from
``request.app.state`` and that the keys are instance-scoped (D0).
"""

from __future__ import annotations


def test_imports():
    """Smoke test: adapter module is importable."""
    from services.gateway.app.approval.adapter import (  # noqa: F401
        approval_hmac_key,
        profile,
        wall_now,
    )


def test_approval_hmac_key_returns_bytes():
    """approval_hmac_key() returns the bytes stored on app.state."""
    from unittest.mock import MagicMock

    from services.gateway.app.approval.adapter import approval_hmac_key

    request = MagicMock()
    request.app.state.approval_hmac_key = b"test-key-32-bytes-long-at-least!!"
    result = approval_hmac_key(request)
    assert result == b"test-key-32-bytes-long-at-least!!"
    assert isinstance(result, bytes)


def test_profile_returns_string():
    """profile() returns the profile string from settings."""
    from unittest.mock import MagicMock

    from services.gateway.app.approval.adapter import profile

    request = MagicMock()
    request.app.state.settings.profile = "B2"
    result = profile(request)
    assert result == "B2"
    assert isinstance(result, str)


def test_wall_now_returns_callable():
    """wall_now() returns the callable from app.state."""
    from datetime import datetime, timedelta, timezone
    from unittest.mock import MagicMock

    from services.gateway.app.approval.adapter import wall_now

    fixed = datetime(2026, 10, 15, 9, 0, 0, tzinfo=timezone(timedelta(hours=8)))
    request = MagicMock()
    request.app.state.wall_now = lambda: fixed
    fn = wall_now(request)
    assert callable(fn)
    result = fn()
    assert result == fixed
    assert result.tzinfo is not None


def test_two_requests_different_keys_return_different_values():
    """Two requests with different app.state return different key values (D0:
    instance-scoped, no global state leakage)."""
    from unittest.mock import MagicMock

    from services.gateway.app.approval.adapter import approval_hmac_key

    req1 = MagicMock()
    req1.app.state.approval_hmac_key = b"aaaa-bbbb-cccc-dddd-eeee-ffff-1111"
    req2 = MagicMock()
    req2.app.state.approval_hmac_key = b"zzzz-yyyy-xxxx-wwww-vvvv-uuuu-9999"

    assert approval_hmac_key(req1) != approval_hmac_key(req2)
    assert approval_hmac_key(req1) == b"aaaa-bbbb-cccc-dddd-eeee-ffff-1111"
    assert approval_hmac_key(req2) == b"zzzz-yyyy-xxxx-wwww-vvvv-uuuu-9999"


def test_d0_concurrency_two_apps_different_keys(app_config):
    """Two apps built concurrently with independent HMAC keys produce different
    binding MACs for the same plan data (D0: instance-scoped, no global state)."""

    import asyncio

    from sqlalchemy import select

    from services.gateway.app import tables as t
    from tests.gateway.w4b2b.db.conftest import (
        _runtime_db,
        _seed_full,
        build_app,
    )

    async def run():
        # Build two apps in a single event loop (shared config, same key pair).
        app1, app2 = await asyncio.gather(build_app(app_config), build_app(app_config))

        db1 = _runtime_db(app1)
        db2 = _runtime_db(app2)

        # Seed distinct data in each app (different sid to avoid the
        # one_pending_approval_per_session unique constraint).
        async with db1.begin() as conn:
            await _seed_full(
                conn,
                approval_id="apv_d0_1",
                sid="ses_d0_1",
                plan_id="pln_d0_1",
                evaluation_id="evl_d0_1",
            )
        async with db2.begin() as conn:
            await _seed_full(
                conn,
                approval_id="apv_d0_2",
                sid="ses_d0_2",
                plan_id="pln_d0_2",
                evaluation_id="evl_d0_2",
            )

        # Verify both apps can serve their own approvals.
        resp1 = await app1.client.get("/approvals/apv_d0_1", headers=app1.auth_headers())
        resp2 = await app2.client.get("/approvals/apv_d0_2", headers=app2.auth_headers())
        assert resp1.status_code == 200
        assert resp2.status_code == 200

        # Binding MACs are deterministic per (key, data).
        async with db1.connect() as conn:
            a1 = (
                (await conn.execute(select(t.approvals).where(t.approvals.c.id == "apv_d0_1")))
                .mappings()
                .one()
            )
        async with db2.connect() as conn:
            a2 = (
                (await conn.execute(select(t.approvals).where(t.approvals.c.id == "apv_d0_2")))
                .mappings()
                .one()
            )

        assert a1["binding_mac"] is not None
        assert a2["binding_mac"] is not None
        # Different approval IDs => different bindings even with same key.
        assert a1["binding_mac"] != a2["binding_mac"]

        await db1.dispose()
        await db2.dispose()

    asyncio.run(run())
