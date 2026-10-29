"""S1 `POST /internal/scheduled/authorize` endpoint tests (PRD §9.5a).

Run with ``GATEWAY_DB_TESTS=1 pytest tests/gateway/s1 -q`` (tmpfs PG).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from safebank_bankcore.ledger import replace_state
from safebank_contracts.codes import Code
from safebank_contracts.obligations import ObligationSnapshot
from safebank_contracts.state import Account, BankState, Counters, Customer
from sqlalchemy import text
from sqlalchemy.pool import NullPool

from services.gateway.app.db import engine
from tests.bankcore.fixtures import base_state
from tests.gateway.s1.conftest import (
    WALL,
    _seed_event_decision,
    _seed_grant,
    build_app,
    make_event_def,
)

URL = "/internal/scheduled/authorize"


def _runtime_db(app):
    return engine(app.db_url, poolclass=NullPool)


# ── S1-specific minimal banking state ─────────────────────────────────────────


def _s1_state(*, balance_cents: int = 600_000) -> BankState:
    """Minimal state with cus_a owning two checking accounts so a self-transfer
    ``acc_a → acc_b`` passes preflight.  ``balance_cents`` controls acc_a's
    posted credits (default 600,000)."""
    st = base_state(balance=balance_cents)
    # Give cus_a a second checking account for self-transfers.
    accounts = dict(st.accounts)
    accounts["acc_b"] = Account(
        id="acc_b",
        customer_id="cus_a",
        type="CHECKING",
        display_name="储蓄",
        account_no="6222020000000003",
        credits_posted_cents=200_000,
    )
    # Remove cus_b's acc_b (was in base_state) and the internal accounts.
    accounts.pop("acc_b_cus_b", None)
    return replace_state(
        BankState(
            sim_now=WALL,
            clock_version=1,
            versions={"cus_a": 0},
            accounts=accounts,
            customers={
                "cus_a": Customer(id="cus_a", display_name="张三", phone="13800000000"),
            },
            cards={},
            payees={},
            counters=Counters(),
            obligations=ObligationSnapshot(
                covered_customer_ids=("cus_a",),
                merchants={},
                billings={},
                standing_instructions={},
                runs=(),
                instruction_targets={},
            ),
        ),
        obligations=ObligationSnapshot(
            covered_customer_ids=("cus_a",),
            merchants={},
            billings={},
            standing_instructions={},
            runs=(),
            instruction_targets={},
        ),
    )


# ── Existing tests ────────────────────────────────────────────────────────────


def test_terminal_replay_returns_stored_result(app_config):
    """A stored terminal event_decisions row is returned without re-evaluation."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_event_decision(conn, event_id="evt_terminal_1", status="SUCCESS", code=None)

        resp = await app.client.post(
            URL, json={"event_id": "evt_terminal_1", "attempt": 1}, headers=app.scheduler_headers()
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["event_id"] == "evt_terminal_1"
        assert body["status"] == "SUCCESS"
        # no re-evaluation: FakeL3 must not have been asked for the event
        assert not any(c["op"] == "event" for c in app.fake.calls)
        await db.dispose()

    asyncio.run(run())


def test_retrying_on_l3_unavailable_writes_nothing(app_config):
    """L3 unreachable during event fetch → 200 RETRYING, nothing persisted."""

    async def run():
        app = await build_app(app_config)
        app.fake.behaviors["event"] = "unavailable_503"
        db = _runtime_db(app)

        resp = await app.client.post(
            URL, json={"event_id": "evt_retry_1", "attempt": 1}, headers=app.scheduler_headers()
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["event_id"] == "evt_retry_1"
        assert body["status"] == "RETRYING"
        assert body["code"] == Code.DEPENDENCY_UNAVAILABLE.value

        async with db.connect() as conn:
            rows = (
                await conn.execute(
                    text("SELECT 1 FROM event_decisions WHERE event_id = :e"),
                    {"e": "evt_retry_1"},
                )
            ).all()
        assert rows == []
        await db.dispose()

    asyncio.run(run())


# ── New tests (r4) ────────────────────────────────────────────────────────────


def test_wrong_token_returns_401(app_config):
    """Call without scheduler token → 401 UNAUTHENTICATED."""

    async def run():
        app = await build_app(app_config)
        # No auth header
        resp = await app.client.post(URL, json={"event_id": "evt_x", "attempt": 1})
        assert resp.status_code == 401, resp.text

        # Wrong service token
        resp = await app.client.post(
            URL,
            json={"event_id": "evt_x", "attempt": 1},
            headers={"X-Service-Token": "wrong-token-32bytes-long-xxxx"},
        )
        assert resp.status_code == 401, resp.text

    asyncio.run(run())


def test_same_event_replay_never_executes_twice(app_config):
    """Terminal event_decisions row exists → 200 with stored result, no L3 calls."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_event_decision(conn, event_id="evt_replay", status="SUCCESS", code=None)
        await db.dispose()

        app.fake.calls.clear()
        resp = await app.client.post(
            URL, json={"event_id": "evt_replay", "attempt": 2}, headers=app.scheduler_headers()
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["event_id"] == "evt_replay"
        assert body["status"] == "SUCCESS"
        # No event/execute/block calls on L3
        l3_ops = {c["op"] for c in app.fake.calls}
        assert not (l3_ops & {"event", "event_execute", "event_block"}), (
            f"unexpected L3 calls: {l3_ops}"
        )

    asyncio.run(run())


def test_revoked_grant_returns_blocked(app_config):
    """REVOKED grant → BLOCKED via /block, event_decision persisted."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_grant(
                conn,
                instruction_id="ins_revoked",
                status="REVOKED",
                revoked_at=WALL - timedelta(hours=1),
            )

        app.fake.set_event(
            make_event_def(
                event_id="evt_revoked",
                source_id="ins_revoked",
            )
        )

        resp = await app.client.post(
            URL, json={"event_id": "evt_revoked", "attempt": 1}, headers=app.scheduler_headers()
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["event_id"] == "evt_revoked"
        assert body["status"] == "BLOCKED"
        assert body["code"] == Code.SCHEDULED_BYPASS.value

        # event_block was called on L3
        block_calls = [c for c in app.fake.calls if c["op"] == "event_block"]
        assert len(block_calls) == 1

        # event_decisions row persisted
        async with db.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT status, code FROM event_decisions WHERE event_id = 'evt_revoked'")
                )
            ).first()
        assert row is not None
        assert row.status == "BLOCKED"
        await db.dispose()

    asyncio.run(run())


def test_missing_grant_backfill_and_success(app_config):
    """No grant seeded → backfill from L3 COMMITTED execution → full flow → SUCCESS."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)

        # L3 execution returns COMMITTED with approved_risk=YELLOW
        app.fake.intents[("cus_a", "plan_sched_1")] = {
            "status": "COMMITTED",
            "result": {
                "plan_id": "plan_sched_1",
                "status": "EXECUTED",
                "steps": [],
                "versions": {},
                "committed_at": WALL.isoformat(),
                "error_code": None,
                "replayed": False,
                "approved_risk": "YELLOW",
            },
            "plan_hash": "unused",
            "attempt": 1,
        }

        app.fake.set_snapshot(_s1_state())
        app.fake.set_event(
            make_event_def(
                event_id="evt_backfill",
                source_id="ins_backfill",
                to_account_id="acc_b",
                payee_id=None,
                amount_cents=80000,
            )
        )

        resp = await app.client.post(
            URL, json={"event_id": "evt_backfill", "attempt": 1}, headers=app.scheduler_headers()
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["event_id"] == "evt_backfill"
        assert body["status"] == "SUCCESS"

        # event_execute called exactly once
        exec_calls = [c for c in app.fake.calls if c["op"] == "event_execute"]
        assert len(exec_calls) == 1

        # event_decisions row persisted
        async with db.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT status FROM event_decisions WHERE event_id = 'evt_backfill'")
                )
            ).first()
        assert row is not None
        assert row.status == "SUCCESS"
        await db.dispose()

    asyncio.run(run())


def test_risk_increase_returns_skipped_reapproval(app_config):
    """Grant approved_risk=GREEN but transfer base_risk=YELLOW → SKIPPED_REAPPROVAL."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_grant(
                conn,
                instruction_id="ins_risk_up",
                approved_risk="GREEN",
            )

        app.fake.set_snapshot(_s1_state())
        app.fake.set_event(
            make_event_def(
                event_id="evt_risk_up",
                source_id="ins_risk_up",
                to_account_id="acc_b",
                payee_id=None,
                amount_cents=80000,
            )
        )

        resp = await app.client.post(
            URL, json={"event_id": "evt_risk_up", "attempt": 1}, headers=app.scheduler_headers()
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["event_id"] == "evt_risk_up"
        assert body["status"] == "SKIPPED_REAPPROVAL"
        assert body["code"] == Code.SCHEDULED_REAPPROVAL_REQUIRED.value

        # event_block was called (not execute)
        block_calls = [c for c in app.fake.calls if c["op"] == "event_block"]
        assert len(block_calls) == 1
        exec_calls = [c for c in app.fake.calls if c["op"] == "event_execute"]
        assert len(exec_calls) == 0
        await db.dispose()

    asyncio.run(run())


def test_insufficient_funds_returns_failed_insufficient(app_config):
    """Transfer amount exceeds balance → DENY/INSUFFICIENT_FUNDS → FAILED_INSUFFICIENT."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_grant(conn, instruction_id="ins_no_funds", approved_risk="YELLOW")

        # Balance is 600000, try to transfer 900000 → insufficient
        app.fake.set_snapshot(_s1_state(balance_cents=600_000))
        app.fake.set_event(
            make_event_def(
                event_id="evt_no_funds",
                source_id="ins_no_funds",
                to_account_id="acc_b",
                payee_id=None,
                amount_cents=900_000,
            )
        )

        resp = await app.client.post(
            URL, json={"event_id": "evt_no_funds", "attempt": 1}, headers=app.scheduler_headers()
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["event_id"] == "evt_no_funds"
        assert body["status"] == "FAILED_INSUFFICIENT"
        assert body["code"] == Code.INSUFFICIENT_FUNDS.value
        await db.dispose()

    asyncio.run(run())


def test_scope_hash_mismatch_returns_blocked(app_config):
    """Grant exists but scope_hash != event.grant_scope_hash → BLOCKED."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_grant(
                conn,
                instruction_id="ins_scope_mismatch",
                scope_hash="different_hash_not_matching_the_event_hash",
                approved_risk="YELLOW",
            )

        app.fake.set_snapshot(_s1_state())
        app.fake.set_event(
            make_event_def(
                event_id="evt_scope_mismatch",
                source_id="ins_scope_mismatch",
                grant_scope_hash="s" * 64,  # different from the seed
                to_account_id="acc_b",
                payee_id=None,
                amount_cents=80000,
            )
        )

        resp = await app.client.post(
            URL,
            json={"event_id": "evt_scope_mismatch", "attempt": 1},
            headers=app.scheduler_headers(),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["event_id"] == "evt_scope_mismatch"
        assert body["status"] == "BLOCKED"
        assert body["code"] == Code.SCHEDULED_BYPASS.value

        # event_block was called on L3
        block_calls = [c for c in app.fake.calls if c["op"] == "event_block"]
        assert len(block_calls) == 1

        # No execute calls
        exec_calls = [c for c in app.fake.calls if c["op"] == "event_execute"]
        assert len(exec_calls) == 0

        await db.dispose()

    asyncio.run(run())


def test_non_committed_backfill_returns_blocked(app_config):
    """No grant seeded; L3 execution returns non-COMMITTED → BLOCKED (no grant
    can exist)."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)

        # L3 execution returns CANCELLED (not COMMITTED)
        app.fake.intents[("cus_a", "plan_sched_1")] = {
            "status": "CANCELLED",
            "result": None,
            "plan_hash": "unused",
            "attempt": 1,
        }

        app.fake.set_snapshot(_s1_state())
        app.fake.set_event(
            make_event_def(
                event_id="evt_non_committed",
                source_id="ins_non_committed",
            )
        )

        resp = await app.client.post(
            URL,
            json={"event_id": "evt_non_committed", "attempt": 1},
            headers=app.scheduler_headers(),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["event_id"] == "evt_non_committed"
        assert body["status"] == "BLOCKED"
        assert body["code"] == Code.SCHEDULED_BYPASS.value

        # event_block called, not event_execute
        block_calls = [c for c in app.fake.calls if c["op"] == "event_block"]
        assert len(block_calls) == 1
        exec_calls = [c for c in app.fake.calls if c["op"] == "event_execute"]
        assert len(exec_calls) == 0

        await db.dispose()

    asyncio.run(run())


def test_happy_path_success_executes_once(app_config):
    """Full flow: ACTIVE grant, sufficient balance, approved_risk=YELLOW → SUCCESS."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_grant(conn, instruction_id="ins_happy", approved_risk="YELLOW")

        app.fake.set_snapshot(_s1_state())
        app.fake.set_event(
            make_event_def(
                event_id="evt_happy",
                source_id="ins_happy",
                to_account_id="acc_b",
                payee_id=None,
                amount_cents=80000,
            )
        )

        resp = await app.client.post(
            URL, json={"event_id": "evt_happy", "attempt": 1}, headers=app.scheduler_headers()
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["event_id"] == "evt_happy"
        assert body["status"] == "SUCCESS"
        assert body.get("instruction_run_id") is not None

        # event_execute called exactly once
        exec_calls = [c for c in app.fake.calls if c["op"] == "event_execute"]
        assert len(exec_calls) == 1

        # event_decisions persisted
        async with db.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT status FROM event_decisions WHERE event_id = 'evt_happy'")
                )
            ).first()
        assert row is not None
        assert row.status == "SUCCESS"

        # Replay: same event returns stored terminal result without new L3 calls
        app.fake.calls.clear()
        resp2 = await app.client.post(
            URL, json={"event_id": "evt_happy", "attempt": 2}, headers=app.scheduler_headers()
        )
        assert resp2.status_code == 200, resp2.text
        body2 = resp2.json()
        assert body2["event_id"] == "evt_happy"
        assert body2["status"] == "SUCCESS"
        exec_calls2 = [c for c in app.fake.calls if c["op"] == "event_execute"]
        assert len(exec_calls2) == 0
        await db.dispose()

    asyncio.run(run())


# ── P1 security review fixes ─────────────────────────────────────────────────


def test_concurrent_callbacks_exactly_one_execute(app_config):
    """P1-1: two concurrent scheduler callbacks for the same event → exactly one
    L3 event_execute call, the second gets RETRYING/EXECUTION_IN_PROGRESS."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_grant(conn, instruction_id="ins_concurrent", approved_risk="YELLOW")

        app.fake.set_snapshot(_s1_state())
        app.fake.set_event(
            make_event_def(
                event_id="evt_concurrent",
                source_id="ins_concurrent",
                to_account_id="acc_b",
                payee_id=None,
                amount_cents=80000,
            )
        )
        # Add a 0.3s delay inside event_execute so the second callback races
        # past the Step-1 terminal-decision check before the first persists.
        app.fake.set_event_execute_delay(0.3)
        app.fake.calls.clear()

        async def call():
            resp = await app.client.post(
                URL,
                json={"event_id": "evt_concurrent", "attempt": 1},
                headers=app.scheduler_headers(),
            )
            return resp

        r1, r2 = await asyncio.gather(call(), call())

        assert r1.status_code == 200, r1.text
        assert r2.status_code == 200, r2.text
        b1, b2 = r1.json(), r2.json()
        statuses = {b1["status"], b2["status"]}
        assert "SUCCESS" in statuses, f"expected SUCCESS in {statuses}"
        assert "RETRYING" in statuses, f"expected RETRYING in {statuses}"

        exec_calls = [c for c in app.fake.calls if c["op"] == "event_execute"]
        assert len(exec_calls) == 1, f"expected 1 execute, got {len(exec_calls)}"

        await db.dispose()

    asyncio.run(run())


def test_persist_failure_retry_records_original_result(app_config):
    """P1-2: if _persist_decision fails after L3 committed, the retry calls L3
    again, receives the stored idempotent result, and persists it."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_grant(conn, instruction_id="ins_retry", approved_risk="YELLOW")

        app.fake.set_snapshot(_s1_state())
        app.fake.set_event(
            make_event_def(
                event_id="evt_retry",
                source_id="ins_retry",
                to_account_id="acc_b",
                payee_id=None,
                amount_cents=80000,
            )
        )

        # First call: succeeds
        resp = await app.client.post(
            URL,
            json={"event_id": "evt_retry", "attempt": 1},
            headers=app.scheduler_headers(),
        )
        assert resp.status_code == 200, resp.text
        b1 = resp.json()
        assert b1["status"] == "SUCCESS"
        original_run_id = b1.get("instruction_run_id")
        assert original_run_id is not None

        # Simulate persist failure: delete the event_decisions row
        async with db.begin() as conn:
            await conn.execute(text("DELETE FROM event_decisions WHERE event_id = 'evt_retry'"))

        app.fake.calls.clear()

        # Retry with same event_id
        resp2 = await app.client.post(
            URL,
            json={"event_id": "evt_retry", "attempt": 2},
            headers=app.scheduler_headers(),
        )
        assert resp2.status_code == 200, resp2.text
        b2 = resp2.json()
        assert b2["status"] == "SUCCESS"
        assert b2.get("instruction_run_id") == original_run_id

        # L3 was called again but returned the stored idempotent result
        exec_calls = [c for c in app.fake.calls if c["op"] == "event_execute"]
        assert len(exec_calls) == 1

        # event_decisions row is back
        async with db.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT status FROM event_decisions WHERE event_id = 'evt_retry'")
                )
            ).first()
        assert row is not None
        assert row.status == "SUCCESS"

        await db.dispose()

    asyncio.run(run())
