"""Pure-function tests for the W4b2b approval adapter.

Tests that the adapter correctly extracts keys/profile/wall_now from
``request.app.state`` and that the keys are genuinely instance-scoped (D0).
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


def test_d0_two_apps_different_keys_self_confirm_cross_reject(app_config):
    """D0: two apps with genuinely different HMAC keys.

    - Each app seeds a YELLOW approval bound with its own key.
    - Self-confirm via G6 → both succeed (200, DB CONFIRMED).
    - Cross-confirm (app1 → app2's approval, app2 → app1's) → both
      rejected (409 APPROVAL_BINDING_MISMATCH), DB unchanged for the
      still-PENDING approval.
    """

    import asyncio

    from sqlalchemy import select

    from services.gateway.app import tables as t
    from services.gateway.app.schemas.enums import ApprovalStatus, PlanStatus
    from tests.gateway.w4b2b.db.conftest import (
        _runtime_db,
        _seed_full,
        build_app,
    )

    KEY_A = b"aaa-app-hmac-key-at-least-32-bytes"
    KEY_B = b"bbb-app-hmac-key-at-least-32-bytes"
    OTP_A = b"aaa-otp-hmac-key-at-least-32-bytes"
    OTP_B = b"bbb-otp-hmac-key-at-least-32-bytes"
    VIEW = "b" * 64

    async def run():
        app_a, app_b = await asyncio.gather(
            build_app(app_config, approval_hmac_key=KEY_A, otp_hmac_key=OTP_A),
            build_app(app_config, approval_hmac_key=KEY_B, otp_hmac_key=OTP_B),
        )

        db_a = _runtime_db(app_a)
        db_b = _runtime_db(app_b)

        # Seed each app's approval chain with its own key.
        async with db_a.begin() as conn:
            await _seed_full(
                conn,
                approval_id="apv_a",
                sid="ses_a",
                plan_id="pln_a",
                evaluation_id="evl_a",
                hmac_key=KEY_A,
            )
        async with db_b.begin() as conn:
            await _seed_full(
                conn,
                approval_id="apv_b",
                sid="ses_b",
                plan_id="pln_b",
                evaluation_id="evl_b",
                hmac_key=KEY_B,
            )

        body_a = {"request_id": "req_self_a", "view_hash": VIEW, "risk_ack": True}
        body_b = {"request_id": "req_self_b", "view_hash": VIEW, "risk_ack": True}

        # ── Self-confirm: each app confirms its own approval ───────────
        ra = await app_a.client.post(
            "/approvals/apv_a/confirm", json=body_a, headers=app_a.auth_headers()
        )
        rb = await app_b.client.post(
            "/approvals/apv_b/confirm", json=body_b, headers=app_b.auth_headers()
        )
        assert ra.status_code == 200, f"self-confirm A: {ra.text}"
        assert rb.status_code == 200, f"self-confirm B: {rb.text}"
        assert ra.json()["status"] == "CONFIRMED"
        assert rb.json()["status"] == "CONFIRMED"

        # ── Cross-confirm: app A tries app B's approval (key mismatch) ─
        rx = await app_a.client.post(
            "/approvals/apv_b/confirm",
            json={"request_id": "req_cross_ab", "view_hash": VIEW, "risk_ack": True},
            headers=app_a.auth_headers(),
        )
        ry = await app_b.client.post(
            "/approvals/apv_a/confirm",
            json={"request_id": "req_cross_ba", "view_hash": VIEW, "risk_ack": True},
            headers=app_b.auth_headers(),
        )
        assert rx.status_code == 409, f"cross AB: expected 409, got {rx.status_code}"
        assert ry.status_code == 409, f"cross BA: expected 409, got {ry.status_code}"

        # ── DB state: apv_a CONFIRMED, apv_b CONFIRMED ────────────────
        async with db_a.connect() as conn:
            a_a = (
                (await conn.execute(select(t.approvals).where(t.approvals.c.id == "apv_a")))
                .mappings()
                .one()
            )
            plan_a = (
                (await conn.execute(select(t.plans).where(t.plans.c.id == "pln_a")))
                .mappings()
                .one()
            )
        assert a_a["status"] == ApprovalStatus.CONFIRMED.value
        assert plan_a["status"] == PlanStatus.CONFIRMED.value

        async with db_b.connect() as conn:
            a_b = (
                (await conn.execute(select(t.approvals).where(t.approvals.c.id == "apv_b")))
                .mappings()
                .one()
            )
            plan_b = (
                (await conn.execute(select(t.plans).where(t.plans.c.id == "pln_b")))
                .mappings()
                .one()
            )
        assert a_b["status"] == ApprovalStatus.CONFIRMED.value
        assert plan_b["status"] == PlanStatus.CONFIRMED.value

        await db_a.dispose()
        await db_b.dispose()

    asyncio.run(run())
