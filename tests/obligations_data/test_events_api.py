"""L3 event API real-PG acceptance tests (PRD §7.11, §8.5).

Tests GET /internal/events/{id}, POST /internal/events/{id}/execute,
and POST /internal/events/{id}/block against a tmpfs PostgreSQL database
via the EventService directly (same pattern as test_standing_exec.py).
"""

import asyncio
import hashlib
from datetime import UTC, datetime, time, timedelta, timezone

import pytest
from app import models as m
from app.db import engine
from app.events import EventService
from safebank_contracts.codes import Code
from sqlalchemy import select, text
from sqlalchemy.pool import NullPool

from tests.obligations_data.fixtures import NOW, install_core, install_obligations

pytestmark = pytest.mark.integration

EVT_ID = "evt_test_001"
PAYLOAD = "si_zs_mom|100000|acc_zs_chk"
PAYLOAD_HASH = hashlib.sha256(PAYLOAD.encode()).hexdigest()
DUE = NOW + timedelta(hours=1)
CST = timezone(timedelta(hours=8))
SI_DUE = datetime(2026, 11, 5, 6, 0, 0, tzinfo=CST)


@pytest.fixture(autouse=True)
def clean_database(obligations_db):
    """Truncate every application table before each test."""

    async def _truncate():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        async with admin.begin() as conn:
            names = ",".join(m.metadata.tables)
            await conn.execute(text(f"TRUNCATE {names} CASCADE"))
        await admin.dispose()

    asyncio.run(_truncate())
    yield


# ── helpers ──────────────────────────────────────────────────────────────


def _admin_conn(obligations_db):
    return engine(obligations_db.admin_url, poolclass=NullPool)


def _runtime_conn(obligations_db):
    return engine(obligations_db.runtime_url, poolclass=NullPool)


async def _seed_event(
    conn,
    *,
    event_id,
    kind,
    source_id,
    due_at,
    payload_hash,
    immutable_payload=None,
    period_key=None,
    status="PENDING",
    rank=3,
):
    period = period_key or due_at.isoformat()
    await conn.execute(
        m.event_jobs.insert().values(
            id=event_id,
            kind=kind,
            rank=rank,
            due_at=due_at,
            immutable_payload=immutable_payload or {},
            payload_hash=payload_hash,
            source_id=source_id,
            period_key=period,
            status=status,
        )
    )


async def _setup(obligations_db):
    admin = _admin_conn(obligations_db)
    async with admin.begin() as conn:
        await install_core(conn)
        await install_obligations(conn)
    await admin.dispose()


async def _clock_and_version(conn, customer_id: str):
    """Return (clock_version, customer_state_version)."""
    clock = (await conn.execute(select(m.clock.c.version))).scalar()
    ver = (
        await conn.execute(
            select(m.state_versions.c.version).where(m.state_versions.c.customer_id == customer_id)
        )
    ).scalar()
    return clock, ver


async def _event_execution(conn, event_id: str):
    row = await conn.execute(
        select(m.event_executions).where(m.event_executions.c.event_id == event_id)
    )
    return row.mappings().first()


def _si_payload(instruction_id: str, amount_cents: int, account_id: str) -> str:
    return f"{instruction_id}|{amount_cents}|{account_id}"


def _si_hash(instruction_id: str, amount_cents: int, account_id: str) -> str:
    raw = _si_payload(instruction_id, amount_cents, account_id)
    return hashlib.sha256(raw.encode()).hexdigest()


# ── test 1: GET shape ────────────────────────────────────────────────────


def test_get_event_shape(obligations_db):
    """GET /internal/events/{id} returns event definition with expected keys."""

    async def check():
        await _setup(obligations_db)
        admin = _admin_conn(obligations_db)
        async with admin.begin() as conn:
            await _seed_event(
                conn,
                event_id=EVT_ID,
                kind="SCHEDULED_TRANSFER",
                source_id="si_zs_mom",
                due_at=DUE,
                payload_hash=PAYLOAD_HASH,
                immutable_payload={"payload": PAYLOAD},
            )
        await admin.dispose()

        runtime = _runtime_conn(obligations_db)
        async with runtime.connect() as conn:
            svc = EventService()
            result = await svc.get_event(conn, EVT_ID, customer_id=None)
        await runtime.dispose()

        assert result["event_id"] == EVT_ID
        assert result["kind"] == "SCHEDULED_TRANSFER"
        assert result["customer_id"] == "cus_zs"
        assert result["due_at"] == DUE.astimezone(UTC).isoformat()
        assert result["payload_hash"] == PAYLOAD_HASH
        assert result["source_id"] == "si_zs_mom"
        assert result["job_status"] == "PENDING"
        assert result["execution"] is None
        assert result["instruction_id"] == "si_zs_mom"
        assert result["source_plan_id"] == "pln_seed"
        assert result["grant_scope_hash"] == "0" * 64
        assert result["account_id"] == "acc_zs_chk"
        assert result["amount_cents"] == 100000
        assert result["payee_id"] == "pye_zs_mom"

    asyncio.run(check())


def test_get_merchant_event_resolves_billing_to_mandate(obligations_db):
    async def check():
        await _setup(obligations_db)
        runtime = _runtime_conn(obligations_db)
        async with runtime.begin() as conn:
            await _seed_event(
                conn,
                event_id="evt_merchant_definition",
                kind="MERCHANT_CHARGE",
                source_id="bill_zs_video",
                due_at=DUE,
                payload_hash="m" * 64,
            )
            event = await EventService().get_event(conn, "evt_merchant_definition", None)
            assert event["source_id"] == "bill_zs_video"
            assert event["instruction_id"] == "si_zs_video"
            assert event["source_plan_id"] == "pln_seed"
            assert event["account_id"] == "acc_zs_chk"
            assert event["amount_cents"] == 3000
            assert event["merchant_id"] == "mer_video"
        await runtime.dispose()

    asyncio.run(check())


# ── test 2: execute SCHEDULED_TRANSFER + replay ──────────────────────────


def test_execute_scheduled_transfer_and_replay(obligations_db):
    """Execute a due SCHEDULED_TRANSFER → money posts exactly once.
    Replay returns the same terminal result without a second posting."""

    async def check():
        await _setup(obligations_db)
        payload_hash = _si_hash("si_zs_mom", 100000, "acc_zs_chk")
        evt_id = "evt_si_zs_mom_001"
        payload_str = f"{evt_id}|{_si_payload('si_zs_mom', 100000, 'acc_zs_chk')}"
        payload_hash = hashlib.sha256(payload_str.encode()).hexdigest()
        admin = _admin_conn(obligations_db)
        async with admin.begin() as conn:
            await _seed_event(
                conn,
                event_id=evt_id,
                kind="SCHEDULED_TRANSFER",
                source_id="si_zs_mom",
                due_at=SI_DUE,
                payload_hash=payload_hash,
                immutable_payload={"payload": payload_str},
                period_key=SI_DUE.isoformat(),
            )
        await admin.dispose()

        runtime = _runtime_conn(obligations_db)
        async with runtime.connect() as conn:
            clock_ver, cust_ver = await _clock_and_version(conn, "cus_zs")

        # ── first execution ──
        async with runtime.begin() as conn:
            svc = EventService()
            result = await svc.execute_event(
                conn,
                evt_id,
                expected_versions={"cus_zs": cust_ver},
                clock_version=clock_ver,
                expected_payload_hash=payload_hash,
                authorization_evidence={"by": "test"},
            )
        assert result["event_id"] == evt_id
        assert result["customer_id"] == "cus_zs"
        assert result["status"] == "SUCCESS"
        assert result.get("replayed") is None  # first execution, not replayed
        assert result["result"]["transfer_id"] is not None

        # Verify event_jobs marked DONE and execution row exists
        async with runtime.connect() as conn:
            job = (
                (await conn.execute(select(m.event_jobs).where(m.event_jobs.c.id == evt_id)))
                .mappings()
                .first()
            )
            assert job["status"] == "DONE"
            exec_row = await _event_execution(conn, evt_id)
            assert exec_row is not None
            assert exec_row["status"] == "SUCCESS"

            # Money moved: acc_zs_chk 600000 → 500000
            txn = (
                await conn.execute(
                    select(m.transactions).where(
                        m.transactions.c.kind == "SCHEDULED_TRANSFER",
                        m.transactions.c.account_id == "acc_zs_chk",
                        m.transactions.c.direction == "OUT",
                    )
                )
            ).first()
            assert txn is not None
            assert txn.amount_cents == 100000

            # Instruction run recorded
            run = (
                await conn.execute(
                    select(m.instruction_runs).where(m.instruction_runs.c.event_id == evt_id)
                )
            ).first()
            assert run is not None
            assert run.status == "SUCCESS"
            assert run.amount_cents == 100000

        # ── replay (same event, already executed) ──
        async with runtime.begin() as conn:
            svc = EventService()
            result2 = await svc.execute_event(
                conn,
                evt_id,
                expected_versions={"cus_zs": cust_ver},
                clock_version=clock_ver,
                expected_payload_hash=payload_hash,
                authorization_evidence={"by": "test"},
            )
        assert result2["event_id"] == evt_id
        assert result2["replayed"] is True
        assert result2["status"] == "SUCCESS"

        # Only one execution row and one run
        async with runtime.connect() as conn:
            exec_count = (
                await conn.execute(
                    select(text("count(*)"))
                    .select_from(m.event_executions)
                    .where(m.event_executions.c.event_id == evt_id)
                )
            ).scalar()
            assert exec_count == 1

            run_count = (
                await conn.execute(
                    select(text("count(*)"))
                    .select_from(m.instruction_runs)
                    .where(m.instruction_runs.c.event_id == evt_id)
                )
            ).scalar()
            assert run_count == 1

        await runtime.dispose()

    asyncio.run(check())


# ── test 3: payload hash mismatch rejected ───────────────────────────────


def test_execute_payload_hash_mismatch(obligations_db):
    """POST …/execute with wrong payload hash → 422 CONTRACT_INVALID."""

    async def check():
        await _setup(obligations_db)
        payload_hash = _si_hash("si_zs_mom", 100000, "acc_zs_chk")
        evt_id = "evt_hash_mismatch"
        payload_str = f"{evt_id}|{_si_payload('si_zs_mom', 100000, 'acc_zs_chk')}"
        payload_hash = hashlib.sha256(payload_str.encode()).hexdigest()
        admin = _admin_conn(obligations_db)
        async with admin.begin() as conn:
            await _seed_event(
                conn,
                event_id=evt_id,
                kind="SCHEDULED_TRANSFER",
                source_id="si_zs_mom",
                due_at=SI_DUE,
                payload_hash=payload_hash,
                immutable_payload={"payload": payload_str},
                period_key=SI_DUE.isoformat(),
            )
        await admin.dispose()

        runtime = _runtime_conn(obligations_db)
        async with runtime.connect() as conn:
            clock_ver, cust_ver = await _clock_and_version(conn, "cus_zs")

        async with runtime.begin() as conn:
            svc = EventService()
            with pytest.raises(Exception) as exc_info:
                await svc.execute_event(
                    conn,
                    evt_id,
                    expected_versions={"cus_zs": cust_ver},
                    clock_version=clock_ver,
                    expected_payload_hash="00" * 32,  # wrong hash
                    authorization_evidence={},
                )
        err = exc_info.value
        assert getattr(err, "status", None) == 422 or "CONTRACT_INVALID" in str(err)

        await runtime.dispose()

    asyncio.run(check())


# ── test 4: block then execute → replayed ────────────────────────────────


def test_block_scheduled_transfer_then_execute_replayed(obligations_db):
    """Block a due SCHEDULED_TRANSFER → BLOCKED, no money moves.
    Subsequent execute returns the same terminal row (replayed)."""

    async def check():
        await _setup(obligations_db)
        payload_hash = _si_hash("si_zs_mom", 100000, "acc_zs_chk")
        evt_id = "evt_block_replay"
        payload_str = f"{evt_id}|{_si_payload('si_zs_mom', 100000, 'acc_zs_chk')}"
        payload_hash = hashlib.sha256(payload_str.encode()).hexdigest()
        admin = _admin_conn(obligations_db)
        async with admin.begin() as conn:
            await _seed_event(
                conn,
                event_id=evt_id,
                kind="SCHEDULED_TRANSFER",
                source_id="si_zs_mom",
                due_at=SI_DUE,
                payload_hash=payload_hash,
                immutable_payload={"payload": payload_str},
                period_key=SI_DUE.isoformat(),
            )
        await admin.dispose()

        runtime = _runtime_conn(obligations_db)
        async with runtime.connect() as conn:
            clock_ver, cust_ver = await _clock_and_version(conn, "cus_zs")

        # ── block ──
        async with runtime.begin() as conn:
            svc = EventService()
            result = await svc.block_event(
                conn,
                evt_id,
                status="BLOCKED",
                code=Code.SCHEDULED_BYPASS.value,
                expected_versions={"cus_zs": cust_ver},
                clock_version=clock_ver,
                expected_payload_hash=payload_hash,
            )
        assert result["event_id"] == evt_id
        assert result["status"] == "BLOCKED"
        assert result["result"]["transfer_id"] is None  # no money moved

        # ── execute after block → replayed ──
        async with runtime.begin() as conn:
            svc = EventService()
            result2 = await svc.execute_event(
                conn,
                evt_id,
                expected_versions={"cus_zs": cust_ver},
                clock_version=clock_ver,
                expected_payload_hash=payload_hash,
                authorization_evidence={},
            )
        assert result2["event_id"] == evt_id
        assert result2["replayed"] is True
        assert result2["status"] == "BLOCKED"

        # Only one execution row
        async with runtime.connect() as conn:
            exec_count = (
                await conn.execute(
                    select(text("count(*)"))
                    .select_from(m.event_executions)
                    .where(m.event_executions.c.event_id == evt_id)
                )
            ).scalar()
            assert exec_count == 1

        await runtime.dispose()

    asyncio.run(check())


# ── test 5: insufficient funds → FAILED_INSUFFICIENT ─────────────────────


def test_execute_insufficient_funds(obligations_db):
    """Scheduled transfer exceeding account balance → FAILED_INSUFFICIENT,
    no money moved, period consumed, run recorded."""

    async def check():
        await _setup(obligations_db)

        # Raise card limits so the amount passes transfer_control_violation
        # and reaches post_transfer for the balance (INSUFFICIENT_FUNDS) check.
        # acc_zs_chk has 600_000 credits; use 700_000 → exceeds balance but
        # stays under all card/customer outflow limits.
        huge_amount = 700_000
        huge_si_id = "si_zs_huge"
        payload_hash = _si_hash(huge_si_id, huge_amount, "acc_zs_chk")
        evt_id = "evt_insufficient"

        admin = _admin_conn(obligations_db)
        async with admin.begin() as conn:
            await conn.execute(
                m.cards.update()
                .where(m.cards.c.id == "crd_zs_1")
                .values(single_limit_cents=5_000_000, daily_limit_cents=10_000_000)
            )
            await conn.execute(
                m.standing_instructions.insert().values(
                    id=huge_si_id,
                    customer_id="cus_zs",
                    kind="SCHEDULED_TRANSFER",
                    amount_type="FIXED",
                    status="ACTIVE",
                    from_account_id="acc_zs_chk",
                    payee_id="pye_zs_mom",
                    to_account_id=None,
                    amount_cents=huge_amount,
                    recurrence="PERIODIC",
                    period="ONCE",
                    anchor_day=None,
                    weekday=None,
                    time_of_day=time(6, 0),
                    next_run_at=SI_DUE,
                    end_at=None,
                    priority="HIGH",
                    grant_scope_hash="0" * 64,
                    source_plan_id="pln_seed",
                    created_at=NOW,
                )
            )
            await _seed_event(
                conn,
                event_id=evt_id,
                kind="SCHEDULED_TRANSFER",
                source_id=huge_si_id,
                due_at=SI_DUE,
                payload_hash=payload_hash,
                immutable_payload={"payload": _si_payload(huge_si_id, huge_amount, "acc_zs_chk")},
                period_key=SI_DUE.isoformat(),
            )
        await admin.dispose()

        runtime = _runtime_conn(obligations_db)
        async with runtime.connect() as conn:
            clock_ver, cust_ver = await _clock_and_version(conn, "cus_zs")

        async with runtime.begin() as conn:
            svc = EventService()
            result = await svc.execute_event(
                conn,
                evt_id,
                expected_versions={"cus_zs": cust_ver},
                clock_version=clock_ver,
                expected_payload_hash=payload_hash,
                authorization_evidence={"by": "test"},
            )
        assert result["event_id"] == evt_id
        assert result["status"] == "FAILED_INSUFFICIENT"
        assert result["result"]["transfer_id"] is None
        assert result["result"]["code"] == Code.INSUFFICIENT_FUNDS.value

        # Verify no money moved
        async with runtime.connect() as conn:
            txn = (
                await conn.execute(
                    select(m.transactions).where(m.transactions.c.kind == "SCHEDULED_TRANSFER")
                )
            ).first()
            assert txn is None  # no transfer posted

            # Run recorded with failure status
            run = (
                await conn.execute(
                    select(m.instruction_runs).where(m.instruction_runs.c.event_id == evt_id)
                )
            ).first()
            assert run is not None
            assert run.status == "FAILED_INSUFFICIENT"

            # Event job is DONE
            job = (
                (await conn.execute(select(m.event_jobs).where(m.event_jobs.c.id == evt_id)))
                .mappings()
                .first()
            )
            assert job["status"] == "DONE"

        await runtime.dispose()

    asyncio.run(check())


# ── test 6: MERCHANT_CHARGE with active mandate ──────────────────────────


def test_merchant_charge_with_active_mandate(obligations_db):
    """MERCHANT_CHARGE with an active mandate → money moves to clearing account."""

    async def check():
        await _setup(obligations_db)

        # bill_zs_video: 3000 cents, active mandate si_zs_video
        billing_id = "bill_zs_video"
        amount = 3000
        charge_payload = f"{billing_id}|{amount}|acc_zs_chk"
        payload_hash = hashlib.sha256(charge_payload.encode()).hexdigest()
        evt_id = "evt_merchant_charge"
        # Use the billing's next_due_at
        charge_due = datetime(2026, 10, 18, 6, 0, 0, tzinfo=CST)

        admin = _admin_conn(obligations_db)
        async with admin.begin() as conn:
            await _seed_event(
                conn,
                event_id=evt_id,
                kind="MERCHANT_CHARGE",
                source_id=billing_id,
                due_at=charge_due,
                payload_hash=payload_hash,
                immutable_payload={"payload": charge_payload},
                period_key=charge_due.isoformat(),
            )
        await admin.dispose()

        runtime = _runtime_conn(obligations_db)
        async with runtime.connect() as conn:
            clock_ver, cust_ver = await _clock_and_version(conn, "cus_zs")

        async with runtime.begin() as conn:
            svc = EventService()
            result = await svc.execute_event(
                conn,
                evt_id,
                expected_versions={"cus_zs": cust_ver},
                clock_version=clock_ver,
                expected_payload_hash=payload_hash,
                authorization_evidence={"by": "test"},
            )
        assert result["event_id"] == evt_id
        assert result["customer_id"] == "cus_zs"
        assert result["status"] == "SUCCESS"
        assert result["result"]["transfer_id"] is not None

        # Verify money moved (600000 → 597000)
        async with runtime.connect() as conn:
            txn = (
                await conn.execute(
                    select(m.transactions).where(
                        m.transactions.c.kind == "MERCHANT_CHARGE",
                        m.transactions.c.account_id == "acc_zs_chk",
                        m.transactions.c.direction == "OUT",
                    )
                )
            ).first()
            assert txn is not None
            assert txn.amount_cents == amount

            # Run recorded
            run = (
                await conn.execute(
                    select(m.instruction_runs).where(m.instruction_runs.c.event_id == evt_id)
                )
            ).first()
            assert run is not None
            assert run.status == "SUCCESS"
            assert run.amount_cents == amount
            assert run.instruction_id == "si_zs_video"  # mandate id

            # Event execution row
            exec_row = await _event_execution(conn, evt_id)
            assert exec_row is not None
            assert exec_row["status"] == "SUCCESS"

        await runtime.dispose()

    asyncio.run(check())


# ── test 7: DEMO_EVENT external credit ───────────────────────────────────


def test_demo_event_external_credit(obligations_db):
    """DEMO_EVENT with credit kind → money credited to target account."""

    async def check():
        await _setup(obligations_db)
        credit_amount = 50000
        demo_payload = {
            "kind": "credit",
            "customer_id": "cus_zs",
            "account_id": "acc_zs_chk",
            "amount_cents": credit_amount,
            "counterparty": "Test Employer",
            "memo": "Salary deposit",
            "tx_kind": "SALARY",
        }
        import json

        payload_hash = hashlib.sha256(json.dumps(demo_payload).encode()).hexdigest()
        evt_id = "evt_demo_credit"
        demo_due = datetime(2026, 10, 16, 9, 0, 0, tzinfo=CST)

        admin = _admin_conn(obligations_db)
        async with admin.begin() as conn:
            await conn.execute(m.clock.update().where(m.clock.c.id == 1).values(sim_now=demo_due))
            await _seed_event(
                conn,
                event_id=evt_id,
                kind="DEMO_EVENT",
                source_id="demo_salary_001",
                due_at=demo_due,
                payload_hash=payload_hash,
                immutable_payload={"payload": json.dumps(demo_payload)},
                period_key=demo_due.isoformat(),
            )
        await admin.dispose()

        runtime = _runtime_conn(obligations_db)
        async with runtime.connect() as conn:
            clock_ver, cust_ver = await _clock_and_version(conn, "cus_zs")

        async with runtime.begin() as conn:
            svc = EventService()
            result = await svc.execute_event(
                conn,
                evt_id,
                expected_versions={"cus_zs": cust_ver},
                clock_version=clock_ver,
                expected_payload_hash=payload_hash,
                authorization_evidence={
                    "type": "DEMO_EVENT",
                    "event_id": evt_id,
                    "console_session_id": "scheduler",
                    "kind": "EXTERNAL_CREDIT",
                },
            )
        assert result["event_id"] == evt_id
        assert result["customer_id"] == "cus_zs"
        assert result["status"] == "SUCCESS"

        # Verify money credited: 600000 + 50000 = 650000
        async with runtime.connect() as conn:
            txn = (
                await conn.execute(
                    select(m.transactions).where(
                        m.transactions.c.kind == "SALARY",
                        m.transactions.c.account_id == "acc_zs_chk",
                        m.transactions.c.direction == "IN",
                    )
                )
            ).first()
            assert txn is not None
            assert txn.amount_cents == credit_amount

            exec_row = await _event_execution(conn, evt_id)
            assert exec_row is not None
            assert exec_row["status"] == "SUCCESS"

        await runtime.dispose()

    asyncio.run(check())


# ── test 8: FUND_CONFIRM – order not found ───────────────────────────────


def test_fund_confirm_order_not_found(obligations_db):
    """FUND_CONFIRM for a non-existent order → 404 NOT_FOUND."""

    async def check():
        await _setup(obligations_db)
        order_id = "ord_nonexistent"
        fund_payload = {"order_id": order_id}
        import json

        payload_hash = hashlib.sha256(json.dumps(fund_payload).encode()).hexdigest()
        evt_id = "evt_fund_missing"
        fund_due = datetime(2026, 10, 20, 9, 0, 0, tzinfo=CST)

        admin = _admin_conn(obligations_db)
        async with admin.begin() as conn:
            await _seed_event(
                conn,
                event_id=evt_id,
                kind="FUND_CONFIRM",
                source_id=order_id,
                due_at=fund_due,
                payload_hash=payload_hash,
                immutable_payload={"payload": json.dumps(fund_payload)},
                period_key=fund_due.isoformat(),
            )
        await admin.dispose()

        runtime = _runtime_conn(obligations_db)
        async with runtime.connect() as conn:
            clock_ver, cust_ver = await _clock_and_version(conn, "cus_zs")

        async with runtime.begin() as conn:
            svc = EventService()
            with pytest.raises(Exception) as exc_info:
                await svc.execute_event(
                    conn,
                    evt_id,
                    expected_versions={"cus_zs": cust_ver},
                    clock_version=clock_ver,
                    expected_payload_hash=payload_hash,
                    authorization_evidence={},
                )
        err = exc_info.value
        assert getattr(err, "status", None) == 404 or "NOT_FOUND" in str(err)

        await runtime.dispose()

    asyncio.run(check())


# ── test 9: block non-scheduled-or-merchant kind rejected ────────────────


def test_block_demo_event_rejected(obligations_db):
    """Blocking a DEMO_EVENT → 422 TOOL_NOT_ALLOWED (only SCHEDULED_TRANSFER
    and MERCHANT_CHARGE may be blocked)."""

    async def check():
        await _setup(obligations_db)
        evt_id = "evt_demo_block_reject"
        demo_due = datetime(2026, 10, 16, 9, 0, 0, tzinfo=CST)
        import json

        demo_payload = {
            "kind": "credit",
            "customer_id": "cus_zs",
            "account_id": "acc_zs_chk",
            "amount_cents": 100,
        }
        payload_hash = hashlib.sha256(json.dumps(demo_payload).encode()).hexdigest()

        admin = _admin_conn(obligations_db)
        async with admin.begin() as conn:
            await _seed_event(
                conn,
                event_id=evt_id,
                kind="DEMO_EVENT",
                source_id="demo_001",
                due_at=demo_due,
                payload_hash=payload_hash,
                immutable_payload={"payload": json.dumps(demo_payload)},
                period_key=demo_due.isoformat(),
            )
        await admin.dispose()

        runtime = _runtime_conn(obligations_db)
        async with runtime.connect() as conn:
            clock_ver, cust_ver = await _clock_and_version(conn, "cus_zs")

        async with runtime.begin() as conn:
            svc = EventService()
            with pytest.raises(Exception) as exc_info:
                await svc.block_event(
                    conn,
                    evt_id,
                    status="BLOCKED",
                    code="TEST",
                    expected_versions={"cus_zs": cust_ver},
                    clock_version=clock_ver,
                    expected_payload_hash=payload_hash,
                )
        err = exc_info.value
        assert getattr(err, "status", None) == 422 or "TOOL_NOT_ALLOWED" in str(err)

        await runtime.dispose()

    asyncio.run(check())
