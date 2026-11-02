"""S1 system event tests (FUND_CONFIRM / FUND_SETTLE / FUND_EXPIRY / DEMO_EVENT).

System events have no grant; they bypass the grant-lookup and re-evaluation
phases and go directly to L3 event_execute with the appropriate evidence type.

Run with ``GATEWAY_DB_TESTS=1 pytest tests/gateway/s1 -q`` (tmpfs PG).
"""

from __future__ import annotations

import asyncio

from safebank_contracts.codes import Code
from sqlalchemy import text
from sqlalchemy.pool import NullPool

from services.gateway.app.db import engine
from tests.gateway.s1.conftest import build_app, make_event_def

URL = "/internal/scheduled/authorize"


# ── FUND_CONFIRM ──────────────────────────────────────────────────────────────


def test_fund_confirm_success(app_config):
    """FUND_CONFIRM: no grant, calls event_execute with OrderEvidence, returns SUCCESS."""

    async def run():
        app = await build_app(app_config)

        app.fake.set_event(
            make_event_def(
                event_id="evt_fund_confirm",
                source_id="ord_sub_001",
                kind="FUND_CONFIRM",
            )
        )

        resp = await app.client.post(
            URL,
            json={"event_id": "evt_fund_confirm", "attempt": 1},
            headers=app.scheduler_headers(),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["event_id"] == "evt_fund_confirm"
        assert body["status"] == "SUCCESS"

        # event_execute called exactly once
        exec_calls = [c for c in app.fake.calls if c["op"] == "event_execute"]
        assert len(exec_calls) == 1

        # Verify the execute body was received by FakeL3
        stored = app.fake.event_executions.get("evt_fund_confirm")
        assert stored is not None
        assert stored["replayed"] is False

        # No grant lookup happened (no backfill)
        assert not any(c["op"] == "execution" for c in app.fake.calls)

    asyncio.run(run())


def test_fund_confirm_replay_returns_stored_result(app_config):
    """FUND_CONFIRM replay: terminal row exists → returns stored result, no L3 calls."""

    async def run():
        app = await build_app(app_config)

        app.fake.set_event(
            make_event_def(
                event_id="evt_fund_replay",
                source_id="ord_sub_002",
                kind="FUND_CONFIRM",
            )
        )

        # First call: execute
        resp = await app.client.post(
            URL,
            json={"event_id": "evt_fund_replay", "attempt": 1},
            headers=app.scheduler_headers(),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "SUCCESS"

        # Second call: replay
        app.fake.calls.clear()
        resp2 = await app.client.post(
            URL,
            json={"event_id": "evt_fund_replay", "attempt": 2},
            headers=app.scheduler_headers(),
        )
        assert resp2.status_code == 200, resp2.text
        assert resp2.json()["status"] == "SUCCESS"

        # No new L3 calls on replay
        exec_calls2 = [c for c in app.fake.calls if c["op"] == "event_execute"]
        assert len(exec_calls2) == 0

    asyncio.run(run())


# ── FUND_SETTLE ───────────────────────────────────────────────────────────────


def test_fund_settle_success(app_config):
    """FUND_SETTLE: no grant, calls event_execute with OrderEvidence, returns SUCCESS."""

    async def run():
        app = await build_app(app_config)

        app.fake.set_event(
            make_event_def(
                event_id="evt_fund_settle",
                source_id="ord_sub_003",
                kind="FUND_SETTLE",
            )
        )

        resp = await app.client.post(
            URL,
            json={"event_id": "evt_fund_settle", "attempt": 1},
            headers=app.scheduler_headers(),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["event_id"] == "evt_fund_settle"
        assert body["status"] == "SUCCESS"

        exec_calls = [c for c in app.fake.calls if c["op"] == "event_execute"]
        assert len(exec_calls) == 1

    asyncio.run(run())


# ── FUND_EXPIRY ───────────────────────────────────────────────────────────────


def test_fund_expiry_success(app_config):
    """FUND_EXPIRY: no grant, calls event_execute with OrderEvidence, returns SUCCESS."""

    async def run():
        app = await build_app(app_config)

        app.fake.set_event(
            make_event_def(
                event_id="evt_fund_expiry",
                source_id="ord_sub_004",
                kind="FUND_EXPIRY",
            )
        )

        resp = await app.client.post(
            URL,
            json={"event_id": "evt_fund_expiry", "attempt": 1},
            headers=app.scheduler_headers(),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["event_id"] == "evt_fund_expiry"
        assert body["status"] == "SUCCESS"

        exec_calls = [c for c in app.fake.calls if c["op"] == "event_execute"]
        assert len(exec_calls) == 1

    asyncio.run(run())


# ── DEMO_EVENT ────────────────────────────────────────────────────────────────


def test_demo_event_blocked_when_demo_disabled(app_config):
    """DEMO_EVENT in non-demo mode → BLOCKED, L3 not executed."""

    async def run():
        app = await build_app(app_config)

        app.fake.set_event(
            make_event_def(
                event_id="evt_demo_blocked",
                source_id="demo_session_01",
                kind="DEMO_EVENT",
            )
        )

        resp = await app.client.post(
            URL,
            json={"event_id": "evt_demo_blocked", "attempt": 1},
            headers=app.scheduler_headers(),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["event_id"] == "evt_demo_blocked"
        assert body["status"] == "BLOCKED"
        assert body["code"] == Code.SCHEDULED_BYPASS.value

        # L3 event_execute was NOT called
        exec_calls = [c for c in app.fake.calls if c["op"] == "event_execute"]
        assert len(exec_calls) == 0

    asyncio.run(run())


def test_demo_event_success_when_demo_enabled(app_config):
    """DEMO_EVENT in demo mode → SUCCESS, L3 executed once."""

    async def run():
        app = await build_app(app_config, demo_mode=True)

        app.fake.set_event(
            make_event_def(
                event_id="evt_demo_ok",
                source_id="demo_session_02",
                kind="DEMO_EVENT",
            )
            | {"payload": {"kind": "EXTERNAL_CREDIT", "console_session_id": "scheduler"}}
        )

        resp = await app.client.post(
            URL,
            json={"event_id": "evt_demo_ok", "attempt": 1},
            headers=app.scheduler_headers(),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["event_id"] == "evt_demo_ok"
        assert body["status"] == "SUCCESS"

        exec_calls = [c for c in app.fake.calls if c["op"] == "event_execute"]
        assert len(exec_calls) == 1

    asyncio.run(run())


# ── L3 unavailable ────────────────────────────────────────────────────────────


def test_system_event_retrying_on_l3_unavailable(app_config):
    """FUND_CONFIRM with L3 event_execute unavailable → RETRYING, claim released."""

    async def run():
        app = await build_app(app_config)

        app.fake.set_event(
            make_event_def(
                event_id="evt_sys_retry",
                source_id="ord_sub_005",
                kind="FUND_CONFIRM",
            )
        )
        app.fake.behaviors["event_execute"] = "unavailable_503"

        resp = await app.client.post(
            URL,
            json={"event_id": "evt_sys_retry", "attempt": 1},
            headers=app.scheduler_headers(),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["event_id"] == "evt_sys_retry"
        assert body["status"] == "RETRYING"
        assert body["code"] == Code.DEPENDENCY_UNAVAILABLE.value

    asyncio.run(run())


def test_fund_confirm_failed_on_l3_records_failure_not_success(app_config):
    """L3 rejects the confirm (FAILED_INSUFFICIENT + code) → recorded as that failure.

    L3 has already committed its terminal row, so the gateway must not call
    event_block and must never report SUCCESS.
    """

    async def run():
        app = await build_app(app_config)

        app.fake.set_event(
            make_event_def(
                event_id="evt_fund_fail",
                source_id="ord_sub_009",
                kind="FUND_CONFIRM",
            )
        )
        app.fake.set_event_execute_result(
            "evt_fund_fail",
            {
                "status": "FAILED_INSUFFICIENT",
                "result": {"status": "FAILED", "code": Code.SUITABILITY_MISMATCH.value},
            },
        )

        resp = await app.client.post(
            URL,
            json={"event_id": "evt_fund_fail", "attempt": 1},
            headers=app.scheduler_headers(),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "FAILED_INSUFFICIENT"
        assert body["code"] == Code.SUITABILITY_MISMATCH.value
        assert not any(c["op"] == "event_block" for c in app.fake.calls)

        db = engine(app.db_url, poolclass=NullPool)
        async with db.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT status, code FROM event_decisions WHERE event_id = :e"),
                    {"e": "evt_fund_fail"},
                )
            ).one()
        await db.dispose()
        assert row.status == "FAILED_INSUFFICIENT"
        assert row.code == Code.SUITABILITY_MISMATCH.value

    asyncio.run(run())
