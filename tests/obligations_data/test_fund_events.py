"""Fund event execution (FUND_CONFIRM / FUND_SETTLE / FUND_EXPIRY) real PG tests.

Exercises EventService.execute_event for fund lifecycle events created by
subscribe/redeem against a tmpfs PostgreSQL database.
"""

import asyncio
import hashlib
import json
import socket
import threading
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
import uvicorn
from app import models as m
from app.db import engine
from app.events import EventService
from app.main import create_app
from safebank_contracts.plan import Execute, InteractiveEvidence, Prepare, SnapshotRequest
from safebank_contracts.state import BankState
from sqlalchemy import select, text
from sqlalchemy.pool import NullPool

from tests.contracts.samples import step
from tests.obligations_data.fixtures import KEY, NOW, install_core

SERVICE_TOKEN = "obligations-test-token"
DEADLINE = "2099-01-01T00:00:00Z"
HEADERS = {"X-Service-Token": SERVICE_TOKEN}
CST = timezone(timedelta(hours=8))

_PRODUCTS = [
    dict(
        id="prd_mmf_01",
        name="活钱宝",
        product_type="MMF",
        risk_level=1,
        risk_label="R1",
        base_nav="1.0000",
        base_date=datetime(2026, 1, 1, tzinfo=UTC),
        confirm_days=0,
        settle_days=0,
        min_amount_cents=100,
        risk_disclosure="货币基金，低风险",
        disclosure_version="v1.0",
    ),
    dict(
        id="prd_r3_gain",
        name="稳健增利",
        product_type="MIXED",
        risk_level=3,
        risk_label="R3",
        base_nav="1.0000",
        base_date=datetime(2026, 1, 1, tzinfo=UTC),
        confirm_days=1,
        settle_days=2,
        min_amount_cents=100,
        risk_disclosure="混合型基金，中风险",
        disclosure_version="v1.0",
    ),
]


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


async def _seed_products(conn):
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    for p in _PRODUCTS:
        await conn.execute(pg_insert(m.products).values(**p).on_conflict_do_nothing())
        settlement_id = f"acc_fund_{p['id']}"
        await conn.execute(
            pg_insert(m.accounts)
            .values(
                id=settlement_id,
                customer_id=None,
                type="INTERNAL",
                display_name=f"Fund Settlement ({p['id']})",
                account_no=f"internal-{p['id']}",
                flags=0,
                single_limit_cents=500_000,
                daily_limit_cents=2_000_000,
            )
            .on_conflict_do_nothing()
        )


async def _seed_risk_assessment(conn, customer_id: str, level: int):
    await conn.execute(
        m.risk_assessments.insert().values(
            id=f"ra_{customer_id}",
            customer_id=customer_id,
            level=f"R{level}",
            assessed_at=NOW,
            valid_until=datetime(2027, 1, 1, tzinfo=CST),
            score=min(level * 3 + 3, 24),
            answers={"q1": "a1"},
            questionnaire_id="rq_v1",
            plan_id=f"pln_seed_ra_{customer_id}",
        )
    )
    await conn.execute(
        m.customers.update()
        .where(m.customers.c.id == customer_id)
        .values(risk_level=level, risk_valid_until="2027-01-01")
    )


async def _setup(obligations_db):
    admin = _admin_conn(obligations_db)
    async with admin.begin() as conn:
        await install_core(conn)
        await _seed_products(conn)
        await _seed_risk_assessment(conn, "cus_ls", 3)
    await admin.dispose()


async def _clock_and_version(conn, customer_id: str):
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


def _green_evidence(plan_hash: str) -> InteractiveEvidence:
    return InteractiveEvidence(
        type="INTERACTIVE",
        origin="agent",
        scope_ids=("utt_1",),
        utterance_ids=("utt_1",),
        evaluation_id="evl_1",
        approved_evaluation_id=None,
        plan_hash=plan_hash,
        approval_id=None,
        confirmed_view_hash=None,
        final_view_hash=plan_hash,
        risk="GREEN",
        confirmed_at_wall=None,
        authorization_epoch=0,
    )


def _subscribe_step(
    account_id="acc_ls_chk", product_id="prd_r3_gain", amount_cents=1_195_500
):
    return [
        step(
            tool="subscribe_product",
            account_id=account_id,
            product_id=product_id,
            amount_cents=amount_cents,
        )
    ]


def _redeem_step(account_id="acc_ls_chk", product_id="prd_r3_gain", units="100.0000"):
    return [
        step(
            tool="redeem_product",
            account_id=account_id,
            product_id=product_id,
            units=units,
        )
    ]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _serve(app, port):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server


async def _prepare_execute(
    client,
    plan_id,
    plan_hash,
    steps,
    *,
    expected_versions,
    clock_version=1,
    customer_id="cus_ls",
):
    evidence = _green_evidence(plan_hash)
    prepare_body = Prepare(
        customer_id=customer_id,
        plan_id=plan_id,
        attempt=1,
        plan_hash=plan_hash,
        steps=steps,
        deadline_wall=DEADLINE,
        deadline_cap_wall=DEADLINE,
        authorization_evidence=evidence,
    )
    execute_body = Execute(
        customer_id=customer_id,
        plan_id=plan_id,
        attempt=1,
        plan_hash=plan_hash,
        steps=steps,
        expected_versions={customer_id: expected_versions},
        clock_version=clock_version,
        authorization_evidence=evidence,
    )
    for _ in range(40):
        try:
            prepared = await client.post(
                "/internal/plans/prepare",
                json=prepare_body.model_dump(mode="json"),
                headers=HEADERS,
                timeout=2.0,
            )
            break
        except httpx.TransportError:
            await asyncio.sleep(0.2)
    else:
        raise RuntimeError("uvicorn listener never became ready")
    assert prepared.status_code == 200, prepared.text
    executed = await client.post(
        "/internal/plans/execute",
        json=execute_body.model_dump(mode="json"),
        headers=HEADERS,
        timeout=10.0,
    )
    assert executed.status_code == 200, executed.text
    return executed.json()


async def _balances(conn) -> tuple[int, int]:
    """Return (total_debits, total_credits) across all account ledgers."""
    debits = (await conn.execute(text("SELECT coalesce(sum(debits_posted),0) FROM accounts"))).scalar()
    credits = (
        await conn.execute(text("SELECT coalesce(sum(credits_posted),0) FROM accounts"))
    ).scalar()
    return int(debits), int(credits)


# ── test 1: execute FUND_CONFIRM ────────────────────────────────────────


def test_execute_fund_confirm(obligations_db):
    """Subscribe prd_r3_gain (non-MMF) → execute FUND_CONFIRM.
    Order → SETTLED, holding created, pending released, account debited.
    Replay returns same terminal result without double posting."""

    async def check():
        await _setup(obligations_db)
        admin = _admin_conn(obligations_db)
        runtime = _runtime_conn(obligations_db)

        # Step 1 — subscribe via HTTP to create order + event_jobs
        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        steps = _subscribe_step()
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            body = await _prepare_execute(
                client, "pln_fund_sub_ev1", "hash_fund_sub_ev1", steps, expected_versions=0
            )
            assert body["status"] == "EXECUTED", body

            # Snapshot to find the order
            snapshot_req = SnapshotRequest(customer_id="cus_ls", steps=steps, event_id=None)
            snapshot_resp = await client.post(
                "/internal/snapshot",
                json=snapshot_req.model_dump(mode="json"),
                headers=HEADERS,
                timeout=10.0,
            )
        assert snapshot_resp.status_code == 200
        state = BankState.model_validate_json(snapshot_resp.text)

        # Verify account has pending freeze
        acc = state.accounts["acc_ls_chk"]
        before_balance = acc.balance_cents
        assert acc.available_cents == before_balance - 1_195_500  # frozen

        # Find the order and event_jobs
        async with admin.connect() as conn:
            orders = (
                await conn.execute(
                    select(m.fund_orders).where(m.fund_orders.c.plan_id == "pln_fund_sub_ev1")
                )
            ).fetchall()
            assert len(orders) == 1
            order = orders[0]
            assert order.status == "PENDING_CONFIRM"
            assert order.type == "SUB"
            order_id = order.id

            # Find FUND_CONFIRM job
            jobs = (
                await conn.execute(
                    select(m.event_jobs).where(
                        m.event_jobs.c.source_id == order_id,
                        m.event_jobs.c.kind == "FUND_CONFIRM",
                    )
                )
            ).fetchall()
            assert len(jobs) == 1
            confirm_job = jobs[0]
            event_id = confirm_job.id
            payload_hash = confirm_job.payload_hash
            assert confirm_job.status == "PENDING"

        # Step 2 — execute FUND_CONFIRM
        async with runtime.connect() as conn:
            clock_ver, cust_ver = await _clock_and_version(conn, "cus_ls")

        async with runtime.begin() as conn:
            svc = EventService()
            result = await svc.execute_event(
                conn,
                event_id,
                expected_versions={"cus_ls": cust_ver},
                clock_version=clock_ver,
                expected_payload_hash=payload_hash,
                authorization_evidence={"by": "test"},
            )
        assert result["event_id"] == event_id
        assert result["customer_id"] == "cus_ls"
        assert result["status"] == "SUCCESS"
        assert result.get("replayed") is None

        # Verify DB state
        async with runtime.connect() as conn:
            # Order is now SETTLED
            updated = (
                await conn.execute(
                    select(m.fund_orders).where(m.fund_orders.c.id == order_id)
                )
            ).first()
            assert updated.status == "SETTLED"
            assert updated.units is not None and updated.units > 0
            assert updated.settle_at is not None

            # Holding created
            holdings = (
                await conn.execute(
                    select(m.holdings).where(
                        m.holdings.c.product_id == "prd_r3_gain",
                        m.holdings.c.customer_id == "cus_ls",
                    )
                )
            ).fetchall()
            assert len(holdings) == 1
            h = holdings[0]
            assert h.units > 0
            assert h.reserved_units == 0

            # Event job → DONE
            job_after = (
                await conn.execute(
                    select(m.event_jobs).where(m.event_jobs.c.id == event_id)
                )
            ).first()
            assert job_after.status == "DONE"

            # Event execution row
            exec_row = await _event_execution(conn, event_id)
            assert exec_row is not None
            assert exec_row["status"] == "SUCCESS"

            # Account: pending resolved → balance down by subscription amount
            acc_after = (
                await conn.execute(
                    select(m.accounts).where(m.accounts.c.id == "acc_ls_chk")
                )
            ).first()
            net = acc_after.credits_posted_cents - acc_after.debits_posted_cents
            avail = net - acc_after.debits_pending_cents
            assert net == 1_200_000 - 1_195_500
            assert avail == net

        # Step 3 — replay FUND_CONFIRM (same event, already executed)
        async with runtime.begin() as conn:
            svc = EventService()
            result2 = await svc.execute_event(
                conn,
                event_id,
                expected_versions={"cus_ls": cust_ver},
                clock_version=clock_ver,
                expected_payload_hash=payload_hash,
                authorization_evidence={"by": "test"},
            )
        assert result2["event_id"] == event_id
        assert result2["replayed"] is True
        assert result2["status"] == "SUCCESS"

        # Only one execution row
        async with runtime.connect() as conn:
            exec_count = (
                await conn.execute(
                    text("SELECT count(*) FROM event_executions WHERE event_id = :eid"),
                    {"eid": event_id},
                )
            ).scalar()
            assert exec_count == 1

        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


# ── test 2: execute FUND_EXPIRY ────────────────────────────────────────


def test_execute_fund_expiry(obligations_db):
    """Subscribe prd_r3_gain → execute FUND_EXPIRY instead of confirm.
    Order → EXPIRED, freeze released, no holding created."""

    async def check():
        await _setup(obligations_db)
        admin = _admin_conn(obligations_db)
        runtime = _runtime_conn(obligations_db)

        # Step 1 — subscribe via HTTP
        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        steps = _subscribe_step()
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            body = await _prepare_execute(
                client, "pln_fund_sub_ev2", "hash_fund_sub_ev2", steps, expected_versions=0
            )
            assert body["status"] == "EXECUTED", body

            snapshot_req = SnapshotRequest(customer_id="cus_ls", steps=steps, event_id=None)
            snapshot_resp = await client.post(
                "/internal/snapshot",
                json=snapshot_req.model_dump(mode="json"),
                headers=HEADERS,
                timeout=10.0,
            )
        assert snapshot_resp.status_code == 200
        state = BankState.model_validate_json(snapshot_resp.text)
        acc = state.accounts["acc_ls_chk"]
        before_credit = acc.credits_posted_cents
        before_debit = acc.debits_posted_cents

        # Find order and FUND_EXPIRY job
        async with admin.connect() as conn:
            orders = (
                await conn.execute(
                    select(m.fund_orders).where(m.fund_orders.c.plan_id == "pln_fund_sub_ev2")
                )
            ).fetchall()
            assert len(orders) == 1
            order_id = orders[0].id
            assert orders[0].status == "PENDING_CONFIRM"

            expiry_jobs = (
                await conn.execute(
                    select(m.event_jobs).where(
                        m.event_jobs.c.source_id == order_id,
                        m.event_jobs.c.kind == "FUND_EXPIRY",
                    )
                )
            ).fetchall()
            assert len(expiry_jobs) == 1
            event_id = expiry_jobs[0].id
            payload_hash = expiry_jobs[0].payload_hash

        # Step 2 — execute FUND_EXPIRY
        async with runtime.connect() as conn:
            clock_ver, cust_ver = await _clock_and_version(conn, "cus_ls")

        async with runtime.begin() as conn:
            svc = EventService()
            result = await svc.execute_event(
                conn,
                event_id,
                expected_versions={"cus_ls": cust_ver},
                clock_version=clock_ver,
                expected_payload_hash=payload_hash,
                authorization_evidence={"by": "test"},
            )
        assert result["event_id"] == event_id
        assert result["status"] == "SUCCESS"

        # Verify: order EXPIRED, no holdings, freeze released
        async with runtime.connect() as conn:
            updated = (
                await conn.execute(
                    select(m.fund_orders).where(m.fund_orders.c.id == order_id)
                )
            ).first()
            assert updated.status == "EXPIRED"

            holdings = (
                await conn.execute(
                    select(m.holdings).where(m.holdings.c.customer_id == "cus_ls")
                )
            ).fetchall()
            assert len(holdings) == 0

            # Balance restored (pending freeze released)
            acc_after = (
                await conn.execute(
                    select(m.accounts).where(m.accounts.c.id == "acc_ls_chk")
                )
            ).first()
            # No posted debit (credit and debit unchanged from freeze release)
            assert acc_after.debits_posted_cents == before_debit
            assert acc_after.credits_posted_cents == before_credit
            # Pending is zero — freeze has been voided
            assert acc_after.debits_pending_cents == 0

            # Event job → DONE
            job_after = (
                await conn.execute(
                    select(m.event_jobs).where(m.event_jobs.c.id == event_id)
                )
            ).first()
            assert job_after.status == "DONE"

            exec_row = await _event_execution(conn, event_id)
            assert exec_row is not None
            assert exec_row["status"] == "SUCCESS"

        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())