"""Fund execution (subscribe_product / redeem_product) real PG/HTTP tests.

Uses the real obligations fixtures (install_core) against a tmpfs Postgres
database served via HTTP.  Run with make test-obligations (OBLIGATIONS_DB_TESTS=1).
"""

import asyncio
import socket
import threading
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest
import uvicorn
from app.db import engine
from app.main import create_app
from safebank_contracts.enums import Risk
from safebank_contracts.plan import Execute, InteractiveEvidence, Prepare, SnapshotRequest
from safebank_contracts.state import BankState
from sqlalchemy import text as sa_text
from sqlalchemy.pool import NullPool

from tests.contracts.samples import step
from tests.obligations_data.fixtures import KEY, NOW, install_core

SERVICE_TOKEN = "obligations-test-token"
DEADLINE = "2099-01-01T00:00:00Z"
HEADERS = {"X-Service-Token": SERVICE_TOKEN}

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
    dict(
        id="prd_r4_growth",
        name="进取成长",
        product_type="MIXED",
        risk_level=4,
        risk_label="R4",
        base_nav="1.0000",
        base_date=datetime(2026, 1, 1, tzinfo=UTC),
        confirm_days=2,
        settle_days=3,
        min_amount_cents=100,
        risk_disclosure="股票型基金，中高风险",
        disclosure_version="v1.0",
    ),
]


@pytest.fixture(autouse=True)
def clean_database(obligations_db):
    """Truncate every application table before each test for an isolated dataset."""

    async def _truncate():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        async with admin.begin() as conn:
            from app import models as m

            names = ",".join(m.metadata.tables)
            await conn.execute(sa_text(f"TRUNCATE {names} CASCADE"))
        await admin.dispose()

    asyncio.run(_truncate())
    yield


async def _seed_products(conn):
    from app import models as m
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
                # Contract Account defaults (same as the P1 seed's internal accounts).
                single_limit_cents=500_000,
                daily_limit_cents=2_000_000,
            )
            .on_conflict_do_nothing()
        )


async def _seed_risk_assessment(conn, customer_id: str, level: int):
    from app import models as m

    # Assessment record (score must be 6–24 per CHECK constraint)
    await conn.execute(
        m.risk_assessments.insert().values(
            id=f"ra_{customer_id}",
            customer_id=customer_id,
            level=f"R{level}",
            assessed_at=NOW,
            valid_until=datetime(2027, 1, 1, tzinfo=timezone(timedelta(hours=8))),
            score=min(level * 3 + 3, 24),
            answers={"q1": "a1"},
            questionnaire_id="rq_v1",
            plan_id=f"pln_seed_ra_{customer_id}",
        )
    )
    # Customer summary
    await conn.execute(
        m.customers.update()
        .where(m.customers.c.id == customer_id)
        .values(
            risk_level=level,
            risk_valid_until="2027-01-01",
        )
    )
    # Internal equity and clearing accounts are already created by install_core


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
        risk=Risk.GREEN,
        confirmed_at_wall=None,
        authorization_epoch=0,
    )


def _subscribe_step(account_id="acc_ls_chk", product_id="prd_r3_gain", amount_cents=1_195_500):
    return [
        step(
            tool="subscribe_product",
            account_id=account_id,
            product_id=product_id,
            amount_cents=amount_cents,
        )
    ]


def _redeem_step(account_id="acc_ls_chk", product_id="prd_mmf_01", units="100.0000"):
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
    client, plan_id, plan_hash, steps, *, expected_versions, clock_version=1, customer_id="cus_ls"
):
    """Prepare then execute a plan; return the execute JSON body."""
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


async def _snapshot(client, steps, customer_id="cus_ls"):
    snapshot = SnapshotRequest(customer_id=customer_id, steps=steps, event_id=None)
    return await client.post(
        "/internal/snapshot", json=snapshot.model_dump(mode="json"), headers=HEADERS, timeout=10.0
    )


async def _read(client, tool, args, customer_id="cus_ls"):
    return await client.post(
        f"/internal/read/{tool}",
        json={"args": args, "customer_id": customer_id},
        headers=HEADERS,
        timeout=10.0,
    )


def test_non_mmf_subscribe_creates_pending_order_and_fund_confirm_job(obligations_db):
    """李四 (R3) subscribes 稳健增利 1,195,500 → PENDING_CONFIRM order + FUND_CONFIRM event_jobs"""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await _seed_products(conn)
            await _seed_risk_assessment(conn, "cus_ls", 3)

        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        steps = _subscribe_step()
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            body = await _prepare_execute(
                client, "pln_fund_sub_1", "hash_fund_sub_1", steps, expected_versions=0
            )
            assert body["status"] == "EXECUTED", body
            snapshot = await _snapshot(client, steps)
        assert snapshot.status_code == 200, snapshot.text
        state = BankState.model_validate_json(snapshot.text)
        acc = state.accounts["acc_ls_chk"]
        # Funds frozen as pending (PENDING_CONFIRM): balance unchanged, available down
        assert acc.balance_cents == 1_200_000
        assert acc.available_cents == 1_200_000 - 1_195_500

        async with admin.connect() as conn:
            # Fund order is PENDING_CONFIRM
            orders = (
                await conn.execute(
                    sa_text("SELECT id, type, status, amount_cents FROM fund_orders")
                )
            ).fetchall()
            assert len(orders) == 1
            assert orders[0].type == "SUB"
            assert orders[0].status == "PENDING_CONFIRM"
            assert orders[0].amount_cents == 1_195_500

            # FUND_CONFIRM event_job exists
            jobs = (
                await conn.execute(
                    sa_text("SELECT id, kind, status FROM event_jobs WHERE kind='FUND_CONFIRM'")
                )
            ).fetchall()
            assert len(jobs) == 1
            assert jobs[0].status == "PENDING"

            # FUND_EXPIRY event_job exists
            expiry = (
                await conn.execute(
                    sa_text("SELECT id, kind, status FROM event_jobs WHERE kind='FUND_EXPIRY'")
                )
            ).fetchall()
            assert len(expiry) == 1

            # Plan execution recorded once
            exec_count = await conn.scalar(
                sa_text("SELECT count(*) FROM plan_executions WHERE plan_id='pln_fund_sub_1'")
            )
            assert exec_count == 1

        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_mmf_subscribe_creates_holding_immediately(obligations_db):
    """MMF subscribe → SETTLED order + holding created immediately (no event_jobs needed)"""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await _seed_products(conn)
            await _seed_risk_assessment(conn, "cus_ls", 3)

        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        steps = _subscribe_step(product_id="prd_mmf_01", amount_cents=500_000)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            body = await _prepare_execute(
                client, "pln_fund_mmf_1", "hash_fund_mmf_1", steps, expected_versions=0
            )
            assert body["status"] == "EXECUTED", body
            snapshot = await _snapshot(client, steps)
        assert snapshot.status_code == 200, snapshot.text
        state = BankState.model_validate_json(snapshot.text)
        acc = state.accounts["acc_ls_chk"]
        assert acc.balance_cents == 1_200_000 - 500_000

        async with admin.connect() as conn:
            orders = (
                await conn.execute(sa_text("SELECT status, type FROM fund_orders"))
            ).fetchall()
            assert len(orders) == 1
            assert orders[0].status == "SETTLED"
            assert orders[0].type == "SUB"

            holdings = (
                await conn.execute(sa_text("SELECT product_id, units FROM holdings"))
            ).fetchall()
            assert len(holdings) == 1
            assert holdings[0].product_id == "prd_mmf_01"

        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_redeem_reserves_units_and_creates_fund_settle_job(obligations_db):
    """Redeem MMF → PENDING_SETTLEMENT order, units reserved, FUND_SETTLE event_jobs"""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await _seed_products(conn)
            await _seed_risk_assessment(conn, "cus_ls", 3)
            # First subscribe MMF to get some units
            from app import models as m

            _today = datetime(2026, 10, 15, 9, 0, 0, tzinfo=timezone(timedelta(hours=8)))
            await conn.execute(
                m.holdings.insert().values(
                    customer_id="cus_ls",
                    account_id="acc_ls_chk",
                    product_id="prd_r3_gain",
                    units="500.0000",
                    reserved_units="0.0000",
                    cost_cents=500_000,
                    reserved_cost_cents=0,
                )
            )
            # Also need a fund order for the initial subscription
            await conn.execute(
                m.fund_orders.insert().values(
                    id="ord_ls_sub_r3",
                    customer_id="cus_ls",
                    account_id="acc_ls_chk",
                    product_id="prd_r3_gain",
                    type="SUB",
                    status="SETTLED",
                    amount_cents=500_000,
                    units="500.0000",
                    nav="1.0000",
                    reserved_cost_cents=0,
                    pending_transfer_id=None,
                    plan_id="pln_seed_r3",
                    step_id="stp_seed_r3",
                    trade_date=_today,
                    confirm_at=_today,
                    settle_at=_today,
                    expires_at=None,
                    failure_code=None,
                )
            )

        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        steps = _redeem_step(product_id="prd_r3_gain", units="100.0000")
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            body = await _prepare_execute(
                client, "pln_fund_red_1", "hash_fund_red_1", steps, expected_versions=0
            )
            assert body["status"] == "EXECUTED", body
            snapshot = await _snapshot(client, steps)
        assert snapshot.status_code == 200, snapshot.text
        _ = BankState.model_validate_json(snapshot.text)

        # Available reduced by redeem amount + units reserved
        async with admin.connect() as conn:
            orders = (
                await conn.execute(
                    sa_text(
                        "SELECT id, type, status, amount_cents, units "
                        "FROM fund_orders WHERE type='RED'"
                    )
                )
            ).fetchall()
            assert len(orders) == 1
            assert orders[0].status == "PENDING_SETTLEMENT"
            assert orders[0].units == 100.0

            holdings = (
                await conn.execute(
                    sa_text(
                        "SELECT units, reserved_units FROM holdings WHERE product_id='prd_r3_gain'"
                    )
                )
            ).fetchall()
            assert len(holdings) == 1
            assert holdings[0].units == Decimal("500.0000")  # total unchanged
            assert holdings[0].reserved_units == Decimal("100.0000")  # reserved

            # FUND_SETTLE event_job exists
            jobs = (
                await conn.execute(
                    sa_text("SELECT id, kind, status FROM event_jobs WHERE kind='FUND_SETTLE'")
                )
            ).fetchall()
            assert len(jobs) == 1
            assert jobs[0].status == "PENDING"

            # Plan execution recorded once
            exec_count = await conn.scalar(
                sa_text("SELECT count(*) FROM plan_executions WHERE plan_id='pln_fund_red_1'")
            )
            assert exec_count == 1

        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_get_holdings_returns_holdings_and_orders(obligations_db):
    """get_holdings read returns holding data and orders for the customer"""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await _seed_products(conn)
            await _seed_risk_assessment(conn, "cus_ls", 3)
            from app import models as m

            _today = datetime(2026, 10, 15, 9, 0, 0, tzinfo=timezone(timedelta(hours=8)))
            await conn.execute(
                m.holdings.insert().values(
                    customer_id="cus_ls",
                    account_id="acc_ls_chk",
                    product_id="prd_mmf_01",
                    units="500.0000",
                    reserved_units="0.0000",
                    cost_cents=500_000,
                    reserved_cost_cents=0,
                )
            )

        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            resp = await _read(client, "get_holdings", {"account_id": None, "product_id": None})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert len(data["holdings"]) == 1
        h = data["holdings"][0]
        assert h["product_id"] == "prd_mmf_01"
        assert h["units"] == "500.0000"
        assert h["available_units"] == "500.0000"
        assert h["cost_cents"] == 500_000
        # orders field present
        assert isinstance(data["orders"], list)

        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_r2_customer_rejected_when_buying_r4_product(obligations_db):
    """R2 customer buying R4 product → rejected with no rows written"""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await _seed_products(conn)
            await _seed_risk_assessment(conn, "cus_zs", 2)

        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        steps = _subscribe_step(
            account_id="acc_zs_chk", product_id="prd_r4_growth", amount_cents=100_000
        )
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            body = await _prepare_execute(
                client,
                "pln_fund_r2_fail",
                "hash_fund_r2_fail",
                steps,
                expected_versions=0,
                customer_id="cus_zs",
            )
            assert body["status"] == "FAILED", body
            assert body["error_code"] == "SUITABILITY_MISMATCH", body

        async with admin.connect() as conn:
            assert (await conn.scalar(sa_text("SELECT count(*) FROM fund_orders"))) == 0
            assert (await conn.scalar(sa_text("SELECT count(*) FROM holdings"))) == 0
            assert (await conn.scalar(sa_text("SELECT count(*) FROM event_jobs"))) == 0

        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_subscribe_retry_20_times_is_idempotent(obligations_db):
    """Re-executing the same subscribe plan 20 times creates only one order/event_job"""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await _seed_products(conn)
            await _seed_risk_assessment(conn, "cus_ls", 3)

        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        steps = _subscribe_step(amount_cents=800_000)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            for _ in range(20):
                body = await _prepare_execute(
                    client,
                    "pln_fund_retry",
                    "hash_fund_retry",
                    steps,
                    expected_versions=0,
                )
                assert body["status"] == "EXECUTED", body

        async with admin.connect() as conn:
            assert (await conn.scalar(sa_text("SELECT count(*) FROM fund_orders"))) == 1
            assert (await conn.scalar(sa_text("SELECT count(*) FROM event_jobs"))) == 2
            assert (
                await conn.scalar(
                    sa_text("SELECT count(*) FROM plan_executions WHERE plan_id='pln_fund_retry'")
                )
                == 1
            )
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())
