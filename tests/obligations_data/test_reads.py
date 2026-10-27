"""R7 list_scheduled and R10 list_subscriptions read tool acceptance tests.

Uses the real obligations fixtures (install_core + install_obligations) against
a tmpfs Postgres database served via HTTP, matching the pattern in
test_obligations_data.py. Tests are executed with make test-obligations.
"""

import asyncio
import socket
import threading

import httpx
import pytest
import uvicorn
from app.db import engine
from app.main import create_app
from sqlalchemy import text as sa_text
from sqlalchemy.pool import NullPool

from tests.obligations_data.fixtures import KEY, NOW, install_core, install_obligations

SERVICE_TOKEN = "obligations-test-token"


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


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _serve(app, port):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server


def _read(client, tool, args, *, customer_id="cus_zs", auth=SERVICE_TOKEN, timeout=10.0):
    return client.post(
        f"/internal/read/{tool}",
        json={"args": args, "customer_id": customer_id},
        headers={"X-Service-Token": auth},
        timeout=timeout,
    )


# ─── fixture helpers ───


async def _prepare_app(obligations_db) -> tuple:
    """Install core + obligations and return (app, port, admin_engine)."""
    admin = engine(obligations_db.admin_url, poolclass=NullPool)
    runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
    async with admin.begin() as conn:
        await install_core(conn)
        await install_obligations(conn)
    app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
    port = free_port()
    _serve(app, port)
    return app, port, admin


async def _await_app(client, port):
    """Wait for uvicorn to become ready."""
    for _ in range(40):
        try:
            r = await client.get(
                f"http://127.0.0.1:{port}/health",
                headers={"X-Service-Token": SERVICE_TOKEN},
                timeout=2.0,
            )
            if r.status_code == 200:
                return
        except httpx.TransportError:
            await asyncio.sleep(0.2)
    raise RuntimeError("uvicorn listener never became ready")


# ─── R7 list_scheduled ───


def test_r7_returns_scheduled_transfers(obligations_db):
    """cus_zs has one scheduled instruction (si_zs_mom MONTHLY 100000)."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)
        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await _read(client, "list_scheduled", {"status": "ALL", "limit": 50})
            assert r.status_code == 200, r.text
            body = r.json()
            assert len(body["items"]) == 1
            item = body["items"][0]
            assert item["id"] == "si_zs_mom"
            assert item["amount_cents"] == 100000
            assert item["period"] == "MONTHLY"
            assert item["status"] == "ACTIVE"
            assert item["last_run"] is None
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_r7_status_filter_deleted_returns_empty(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)
        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await _read(client, "list_scheduled", {"status": "DELETED", "limit": 50})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["items"] == []
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_r7_cross_customer_invisible(obligations_db):
    """cus_ls has no scheduled transfers; requesting as cus_zs must not return cus_ls data."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)
        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            # Request as cus_ls — they have zero scheduled instructions in fixtures.
            r = await client.post(
                "/internal/read/list_scheduled",
                json={"args": {"status": "ALL", "limit": 50}, "customer_id": "cus_ls"},
                headers={"X-Service-Token": SERVICE_TOKEN},
                timeout=10.0,
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["items"] == []
            # cus_zs's si_zs_mom must not appear.
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_r7_invalid_cursor_returns_422(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)
        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await _read(
                client, "list_scheduled", {"status": "ALL", "limit": 50, "cursor": "garbage"}
            )
            assert r.status_code == 422, r.text
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_r7_pagination_respects_limit(obligations_db):
    """With limit=1 the result has next_cursor; reusing it returns the remaining items."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        from datetime import datetime
        from datetime import time as dt_time

        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)
            # Add a second scheduled instruction so there's >1 item for pagination.
            from app import models as m

            await conn.execute(
                m.standing_instructions.insert().values(
                    id="si_zs_rent",
                    kind="SCHEDULED_TRANSFER",
                    customer_id="cus_zs",
                    status="ACTIVE",
                    amount_type="FIXED",
                    recurrence="PERIODIC",
                    from_account_id="acc_zs_chk",
                    payee_id="pye_zs_wwu",
                    to_account_id=None,
                    amount_cents=50000,
                    period="MONTHLY",
                    anchor_day=10,
                    weekday=None,
                    time_of_day=dt_time(9, 0),
                    next_run_at=datetime.fromisoformat("2026-11-10T09:00:00+08:00"),
                    end_at=None,
                    priority="MEDIUM",
                    grant_scope_hash="0" * 64,
                    source_plan_id="pln_seed",
                    created_at=NOW,
                )
            )
        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r1 = await _read(client, "list_scheduled", {"status": "ALL", "limit": 1})
            assert r1.status_code == 200, r1.text
            b1 = r1.json()
            assert len(b1["items"]) == 1
            assert b1["next_cursor"] is not None
            r2 = await _read(
                client,
                "list_scheduled",
                {"status": "ALL", "limit": 50, "cursor": b1["next_cursor"]},
            )
            assert r2.status_code == 200, r2.text
            b2 = r2.json()
            assert len(b2["items"]) == 1
            assert b2["next_cursor"] is None
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_r7_last_run_non_none_when_runs_exist(obligations_db):
    """When an instruction_run record exists for si_zs_mom, last_run must be non-None."""

    async def check():
        from datetime import datetime as dtm

        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)
            from app import models as m

            await conn.execute(
                m.instruction_runs.insert().values(
                    id="run_zs_mom_1",
                    customer_id="cus_zs",
                    account_id="acc_zs_chk",
                    kind="SCHEDULED_TRANSFER",
                    instruction_id="si_zs_mom",
                    due_at=dtm.fromisoformat("2026-10-05T06:00:00+08:00"),
                    event_id="evt_run_zs_mom_1",
                    status="FAILED_INSUFFICIENT",
                    code="INSUFFICIENT_FUNDS",
                    amount_cents=100000,
                    billing_id=None,
                )
            )
        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await _read(client, "list_scheduled", {"status": "ALL", "limit": 50})
            assert r.status_code == 200, r.text
            body = r.json()
            assert len(body["items"]) == 1
            item = body["items"][0]
            assert item["id"] == "si_zs_mom"
            last = item["last_run"]
            assert last is not None
            assert last["status"] == "FAILED_INSUFFICIENT"
            assert last["code"] == "INSUFFICIENT_FUNDS"
            assert last["due_at"] == "2026-10-04T22:00:00Z"
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_r7_cross_tool_cursor_rejected(obligations_db):
    """A cursor from list_subscriptions must be rejected when used with list_scheduled."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)
        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            # Get a list_subscriptions cursor.
            r1 = await _read(client, "list_subscriptions", {"status": "ALL", "limit": 1})
            assert r1.status_code == 200, r1.text
            b1 = r1.json()
            assert b1["next_cursor"] is not None
            # Reuse it with list_scheduled — must be rejected.
            r2 = await _read(
                client,
                "list_scheduled",
                {"status": "ALL", "limit": 50, "cursor": b1["next_cursor"]},
            )
            assert r2.status_code == 422, r2.text
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


# ─── R10 list_subscriptions ───


def test_r10_returns_subscriptions_with_totals(obligations_db):
    """cus_zs has 4 ACTIVE subscriptions (si_zs_video, si_zs_music, si_zs_cloud, si_zs_gym)."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)
        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await _read(client, "list_subscriptions", {"status": "ALL", "limit": 50})
            assert r.status_code == 200, r.text
            body = r.json()
            assert len(body["items"]) == 4

            ids = {i["instruction_id"] for i in body["items"]}
            assert ids == {"si_zs_video", "si_zs_music", "si_zs_cloud", "si_zs_gym"}

            nb = body["normalization_basis"]
            assert nb["weekly_to_annual_factor"] == 52
            assert nb["monthly_to_annual_factor"] == 12
            assert nb["annual_to_monthly"] == "floor_div_12"

            # All 4 ACTIVE: video=3000, music=1500, cloud=2100, gym=19900 (all MONTHLY)
            # total_monthly = 3000+1500+2100+19900 = 26500
            # total_annual = 26500×12 = 318000
            assert body["total_monthly_cents"] == 26500
            assert body["total_annual_cents"] == 318000
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_r10_totals_only_active(obligations_db):
    """Set one subscription to DELETED and verify totals exclude it."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)
            # Mark si_zs_video as DELETED
            from app import models as m

            await conn.execute(
                m.standing_instructions.update()
                .where(m.standing_instructions.c.id == "si_zs_video")
                .values(status="DELETED")
            )
        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await _read(client, "list_subscriptions", {"status": "ALL", "limit": 50})
            assert r.status_code == 200, r.text
            body = r.json()
            # 4 total items (1 DELETED + 3 ACTIVE)
            assert len(body["items"]) == 4
            statuses = {i["status"] for i in body["items"]}
            assert statuses == {"ACTIVE", "DELETED"}

            # Totals only for ACTIVE: music=1500 + cloud=2100 + gym=19900 = 23500
            # Monthly: all MONTHLY, so monthly = 1500+2100+19900 = 23500
            # Annual: 1500*12 + 2100*12 + 19900*12 = 18000+25200+238800 = 282000
            assert body["total_monthly_cents"] == 23500
            assert body["total_annual_cents"] == 282000
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_r10_status_filter_deleted(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)
            from app import models as m

            await conn.execute(
                m.standing_instructions.update()
                .where(m.standing_instructions.c.id == "si_zs_video")
                .values(status="DELETED")
            )
        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await _read(client, "list_subscriptions", {"status": "DELETED", "limit": 50})
            assert r.status_code == 200, r.text
            body = r.json()
            assert len(body["items"]) == 1
            assert body["items"][0]["instruction_id"] == "si_zs_video"
            assert body["items"][0]["status"] == "DELETED"
            # Totals only ACTIVE
            assert body["total_monthly_cents"] == 1500 + 2100 + 19900
            assert body["total_annual_cents"] == 1500 * 12 + 2100 * 12 + 19900 * 12
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_r10_cross_customer_invisible(obligations_db):
    """cus_ls subscriptions must not appear when querying as cus_zs."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)
        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await _read(client, "list_subscriptions", {"status": "ALL", "limit": 50})
            assert r.status_code == 200, r.text
            body = r.json()
            ids = {i["instruction_id"] for i in body["items"]}
            assert "si_ls_video" not in ids
            assert "si_ls_music" not in ids
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_r10_invalid_cursor_returns_422(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)
        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await _read(
                client, "list_subscriptions", {"status": "ALL", "limit": 50, "cursor": "bad"}
            )
            assert r.status_code == 422, r.text
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_r10_annualization_weekly_monthly(obligations_db):
    """Insert a WEEKLY billing and verify annual/monthly totals."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        from datetime import datetime
        from datetime import time as dt_time

        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)
            from app import models as m

            # Insert a WEEKLY merchant and billing + mandate
            await conn.execute(
                m.merchants.insert().values(
                    id="mer_weekly",
                    name="周卡服务",
                    mcc="5817",
                    category="娱乐",
                    usage_expected=False,
                )
            )
            await conn.execute(
                m.merchant_billing.insert().values(
                    id="bill_zs_weekly",
                    customer_id="cus_zs",
                    account_id="acc_zs_chk",
                    merchant_id="mer_weekly",
                    amount_cents=1000,
                    period="WEEKLY",
                    anchor_day=None,
                    weekday=1,
                    time_of_day=dt_time(6, 0),
                    next_due_at=datetime.fromisoformat("2026-10-20T06:00:00+08:00"),
                    active=True,
                    created_at=NOW,
                )
            )
            await conn.execute(
                m.standing_instructions.insert().values(
                    id="si_zs_weekly",
                    kind="MERCHANT_MANDATE",
                    customer_id="cus_zs",
                    status="ACTIVE",
                    from_account_id="acc_zs_chk",
                    amount_type="DUES",
                    recurrence="AS_PER_DUES",
                    period=None,
                    anchor_day=None,
                    weekday=None,
                    time_of_day=None,
                    next_run_at=None,
                    end_at=None,
                    priority="HIGH",
                    grant_scope_hash="0" * 64,
                    source_plan_id="pln_seed",
                    created_at=NOW,
                    billing_id="bill_zs_weekly",
                    merchant_id="mer_weekly",
                    max_amount_cents=1200,
                )
            )
        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await _read(client, "list_subscriptions", {"status": "ALL", "limit": 50})
            assert r.status_code == 200, r.text
            body = r.json()
            # 4 original + 1 new weekly = 5
            assert len(body["items"]) == 5

            # Find the WEEKLY item
            weekly = [i for i in body["items"] if i["period"] == "WEEKLY"]
            assert len(weekly) == 1
            w = weekly[0]
            assert w["instruction_id"] == "si_zs_weekly"
            assert w["annual_cost_cents"] == 1000 * 52  # = 52000

            # Totals: ACTIVE subscriptions now 5 old + 1 new
            # MONTHLY: 3000+1500+2100+19900 = 26500 (only original 4; mom is
            # ScheduledInstruction, not in subscriptions)
            # WEEKLY: monthly = floor(1000*52/12) = floor(52000/12) = 4333
            # Expected total_monthly = 26500 + 4333 = 30833
            # Expected total_annual = (3000+1500+2100+19900)*12 + 52000 = 318000 + 52000 = 370000
            assert body["total_monthly_cents"] == 30833
            assert body["total_annual_cents"] == 370000
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_r10_pagination_respects_limit(obligations_db):
    """With limit=1 the result has next_cursor; reusing it returns the remaining items."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)
        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r1 = await _read(client, "list_subscriptions", {"status": "ALL", "limit": 1})
            assert r1.status_code == 200, r1.text
            b1 = r1.json()
            assert len(b1["items"]) == 1
            assert b1["next_cursor"] is not None
            r2 = await _read(
                client,
                "list_subscriptions",
                {"status": "ALL", "limit": 50, "cursor": b1["next_cursor"]},
            )
            assert r2.status_code == 200, r2.text
            b2 = r2.json()
            assert len(b2["items"]) == 3
            assert b2["next_cursor"] is None
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_r10_empty_collection_returns_empty_list(obligations_db):
    """cus_sb has no subscriptions; querying as cus_sb must return empty items."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)
        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await client.post(
                "/internal/read/list_subscriptions",
                json={"args": {"status": "ALL", "limit": 50}, "customer_id": "cus_sb"},
                headers={"X-Service-Token": SERVICE_TOKEN},
                timeout=10.0,
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["items"] == []
            assert body["total_monthly_cents"] == 0
            assert body["total_annual_cents"] == 0
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


# ─── R11 upcoming_charges ───


def test_r11_days_7_returns_charges_and_totals(obligations_db):
    """cus_zs has 2 charges within 7-day window (video 10-18, music 10-20), no scheduled."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)
        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await _read(client, "upcoming_charges", {"days": 7, "account_id": None})
            assert r.status_code == 200, r.text
            body = r.json()
            items = body["items"]
            assert len(items) == 2
            billing_ids = {i["billing_id"] for i in items}
            assert billing_ids == {"bill_zs_video", "bill_zs_music"}
            for i in items:
                assert i["account_id"] == "acc_zs_chk"
                assert i["merchant_id"] in ("mer_video", "mer_music")
                assert i["merchant_name"] in ("爱看视频", "乐听音乐")
                assert i["due_at"] is not None
                assert i["amount_cents"] in (3000, 1500)
                assert i["max_amount_cents"] in (3600, 1800)
            assert body["scheduled_items"] == []
            assert body["charges_total_cents"] == 4500
            assert body["scheduled_transfers_total_cents"] == 0
            assert body["reserve_total_cents"] == 4500
            by_account = {a["account_id"]: a for a in body["by_account"]}
            assert set(by_account) == {"acc_zs_chk", "acc_zs_sav"}
            assert by_account["acc_zs_chk"]["reserve_cents"] == 4500
            assert by_account["acc_zs_chk"]["max_outflow_cents"] == 595500
            assert by_account["acc_zs_sav"]["reserve_cents"] == 0
            assert by_account["acc_zs_sav"]["max_outflow_cents"] == 2_000_000
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_r11_days_1_returns_empty(obligations_db):
    """No obligations fall within 1-day window (earliest due is 10-18)."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)
        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await _read(client, "upcoming_charges", {"days": 1, "account_id": None})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["items"] == []
            assert body["scheduled_items"] == []
            assert body["charges_total_cents"] == 0
            assert body["scheduled_transfers_total_cents"] == 0
            assert body["reserve_total_cents"] == 0
            by_account = {a["account_id"]: a for a in body["by_account"]}
            assert by_account["acc_zs_chk"]["reserve_cents"] == 0
            assert by_account["acc_zs_chk"]["max_outflow_cents"] == 600_000
            assert by_account["acc_zs_sav"]["reserve_cents"] == 0
            assert by_account["acc_zs_sav"]["max_outflow_cents"] == 2_000_000
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_r11_days_30_returns_all_charges_and_scheduled(obligations_db):
    """All 4 charges + 1 scheduled transfer appear within 30-day window."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)
        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await _read(client, "upcoming_charges", {"days": 30, "account_id": None})
            assert r.status_code == 200, r.text
            body = r.json()
            items = body["items"]
            assert len(items) == 4
            billing_ids = {i["billing_id"] for i in items}
            assert billing_ids == {"bill_zs_video", "bill_zs_music", "bill_zs_cloud", "bill_zs_gym"}
            assert body["charges_total_cents"] == 26500
            scheduled = body["scheduled_items"]
            assert len(scheduled) == 1
            assert scheduled[0]["instruction_id"] == "si_zs_mom"
            assert scheduled[0]["amount_cents"] == 100_000
            assert scheduled[0]["account_id"] == "acc_zs_chk"
            assert body["scheduled_transfers_total_cents"] == 100_000
            assert body["reserve_total_cents"] == 126_500
            by_account = {a["account_id"]: a for a in body["by_account"]}
            assert by_account["acc_zs_chk"]["reserve_cents"] == 126_500
            assert by_account["acc_zs_chk"]["max_outflow_cents"] == 473_500
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_r11_account_id_filter(obligations_db):
    """account_id filters obligations to the specified account only."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)
        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await _read(client, "upcoming_charges", {"days": 7, "account_id": "acc_zs_chk"})
            assert r.status_code == 200, r.text
            body = r.json()
            assert len(body["items"]) == 2
            assert body["charges_total_cents"] == 4500
            by_account = {a["account_id"]: a for a in body["by_account"]}
            assert set(by_account) == {"acc_zs_chk"}
            assert by_account["acc_zs_chk"]["reserve_cents"] == 4500

            r = await _read(client, "upcoming_charges", {"days": 7, "account_id": "acc_zs_sav"})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["items"] == []
            assert body["scheduled_items"] == []
            assert body["charges_total_cents"] == 0
            assert body["scheduled_transfers_total_cents"] == 0
            assert body["reserve_total_cents"] == 0
            by_account = {a["account_id"]: a for a in body["by_account"]}
            assert set(by_account) == {"acc_zs_sav"}
            assert by_account["acc_zs_sav"]["reserve_cents"] == 0
            assert by_account["acc_zs_sav"]["max_outflow_cents"] == 2_000_000
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_r11_cross_customer_account_id_rejected(obligations_db):
    """Querying with another customer's account_id returns 404."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)
        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await _read(client, "upcoming_charges", {"days": 7, "account_id": "acc_ls_chk"})
            assert r.status_code == 404, r.text

            r = await _read(
                client, "upcoming_charges", {"days": 7, "account_id": "acc_nonexistent"}
            )
            assert r.status_code == 404, r.text
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_r11_empty_obligations_zero_totals(obligations_db):
    """cus_sb has no obligations; query returns empty lists and zero totals."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)
        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await client.post(
                "/internal/read/upcoming_charges",
                json={"args": {"days": 7, "account_id": None}, "customer_id": "cus_sb"},
                headers={"X-Service-Token": SERVICE_TOKEN},
                timeout=10.0,
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["items"] == []
            assert body["scheduled_items"] == []
            assert body["charges_total_cents"] == 0
            assert body["scheduled_transfers_total_cents"] == 0
            assert body["reserve_total_cents"] == 0
            by_account = {a["account_id"]: a for a in body["by_account"]}
            assert by_account["acc_sb_chk"]["reserve_cents"] == 0
            assert by_account["acc_sb_chk"]["max_outflow_cents"] == 3_000_000
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_r11_shortfall_max_outflow_negative(obligations_db):
    """Billing exceeding cus_ww balance (200k) → max_outflow_cents negative."""

    async def check():
        from datetime import time as dt_time

        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)
            from app import models as m

            await conn.execute(
                m.merchants.insert().values(
                    id="mer_big",
                    name="大额服务",
                    mcc="5999",
                    category="其他",
                    usage_expected=False,
                )
            )
            await conn.execute(
                m.merchant_billing.insert().values(
                    id="bill_ww_big",
                    customer_id="cus_ww",
                    account_id="acc_ww_chk",
                    merchant_id="mer_big",
                    amount_cents=250_000,
                    period="MONTHLY",
                    anchor_day=18,
                    weekday=None,
                    time_of_day=dt_time(6, 0),
                    next_due_at=NOW.replace(day=18, hour=6, minute=0),
                    active=True,
                    created_at=NOW,
                )
            )
            await conn.execute(
                m.standing_instructions.insert().values(
                    id="si_ww_big",
                    kind="MERCHANT_MANDATE",
                    customer_id="cus_ww",
                    status="ACTIVE",
                    from_account_id="acc_ww_chk",
                    amount_type="DUES",
                    recurrence="AS_PER_DUES",
                    period=None,
                    anchor_day=None,
                    weekday=None,
                    time_of_day=None,
                    next_run_at=None,
                    end_at=None,
                    priority="HIGH",
                    grant_scope_hash="0" * 64,
                    source_plan_id="pln_seed",
                    created_at=NOW,
                    billing_id="bill_ww_big",
                    merchant_id="mer_big",
                    max_amount_cents=300_000,
                )
            )
        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await client.post(
                "/internal/read/upcoming_charges",
                json={"args": {"days": 7, "account_id": None}, "customer_id": "cus_ww"},
                headers={"X-Service-Token": SERVICE_TOKEN},
                timeout=10.0,
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert len(body["items"]) == 1
            assert body["items"][0]["billing_id"] == "bill_ww_big"
            assert body["items"][0]["amount_cents"] == 250_000
            assert body["charges_total_cents"] == 250_000
            by_account = {a["account_id"]: a for a in body["by_account"]}
            assert by_account["acc_ww_chk"]["max_outflow_cents"] < 0
            assert by_account["acc_ww_chk"]["max_outflow_cents"] == -50_000
            assert by_account["acc_ww_chk"]["reserve_cents"] == 250_000
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_r11_reserve_total_consistent_with_projected_shortfall(obligations_db):
    """For days=7, reserve_total_cents equals the sum of by_account reserves."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)
        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await _read(client, "upcoming_charges", {"days": 7, "account_id": None})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["reserve_total_cents"] == 4500
            by_account_sum = sum(a["reserve_cents"] for a in body["by_account"])
            assert by_account_sum == body["reserve_total_cents"]
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_r11_days_0_invalid(obligations_db):
    """days=0 is below minimum (ge=1) → 422."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)
        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await _read(client, "upcoming_charges", {"days": 0, "account_id": None})
            assert r.status_code == 422, r.text
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_r11_days_31_invalid(obligations_db):
    """days=31 exceeds maximum (le=30) → 422."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)
        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await _read(client, "upcoming_charges", {"days": 31, "account_id": None})
            assert r.status_code == 422, r.text
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


# ─── R4 get_spending_summary ───

from datetime import UTC
from datetime import datetime as dtm

tz_utc = UTC


async def _insert_card_spend(
    conn,
    *,
    tx_id,
    xfer_id,
    customer_id,
    account_id,
    amount_cents,
    category,
    occurred_at,
    balance_cents,
):
    """Insert a minimal transfer + transaction row for a CARD_SPEND."""
    import uuid

    from app import models as m

    occurred = occurred_at.astimezone(tz_utc).replace(tzinfo=None)
    await conn.execute(
        m.transfers.insert().values(
            id=xfer_id,
            debit_account_id=account_id,
            credit_account_id="acc_external_clearing",
            amount_cents=amount_cents,
            code="CARD_SPEND",
            phase="POSTED",
            plan_id="pln_seed",
            step_id=str(uuid.uuid4()),
            leg="DEBIT",
            occurred_at=occurred,
        )
    )
    await conn.execute(
        m.transactions.insert().values(
            id=tx_id,
            transfer_id=xfer_id,
            customer_id=customer_id,
            account_id=account_id,
            direction="OUT",
            kind="CARD_SPEND",
            phase="POSTED",
            status="POSTED",
            amount_cents=amount_cents,
            balance_after_cents=balance_cents,
            available_after_cents=balance_cents,
            counterparty="merchant",
            category=category,
            occurred_at=occurred,
        )
    )


def test_r4_returns_categories_and_daily(obligations_db):
    """Insert CARD_SPEND for 2026-10 with 3 categories + prev month data."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)

            bal = 600_000
            # October 2026: 餐饮 3000, 交通 5000, 娱乐 2000
            await _insert_card_spend(
                conn,
                tx_id="tx_oct_1",
                xfer_id="xfr_oct_1",
                customer_id="cus_zs",
                account_id="acc_zs_chk",
                amount_cents=3000,
                category="餐饮",
                occurred_at=dtm(2026, 10, 10, 12, 0, 0, tzinfo=tz_utc),
                balance_cents=bal - 3000,
            )
            bal -= 3000
            await _insert_card_spend(
                conn,
                tx_id="tx_oct_2",
                xfer_id="xfr_oct_2",
                customer_id="cus_zs",
                account_id="acc_zs_chk",
                amount_cents=5000,
                category="交通",
                occurred_at=dtm(2026, 10, 15, 14, 0, 0, tzinfo=tz_utc),
                balance_cents=bal - 5000,
            )
            bal -= 5000
            await _insert_card_spend(
                conn,
                tx_id="tx_oct_3",
                xfer_id="xfr_oct_3",
                customer_id="cus_zs",
                account_id="acc_zs_chk",
                amount_cents=2000,
                category="娱乐",
                occurred_at=dtm(2026, 10, 20, 18, 0, 0, tzinfo=tz_utc),
                balance_cents=bal - 2000,
            )
            # September 2026: 餐饮 2000 (prev month reference)
            await _insert_card_spend(
                conn,
                tx_id="tx_sep_1",
                xfer_id="xfr_sep_1",
                customer_id="cus_zs",
                account_id="acc_zs_chk",
                amount_cents=2000,
                category="餐饮",
                occurred_at=dtm(2026, 9, 8, 12, 0, 0, tzinfo=tz_utc),
                balance_cents=bal - 2000,
            )

        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await _read(
                client, "get_spending_summary", {"month": "2026-10", "account_id": None}
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["month"] == "2026-10"
            assert body["total_cents"] == 10000  # 3000+5000+2000
            assert body["previous_total_cents"] == 2000
            assert body["mom_change_cents"] == 8000
            assert body["mom_change_bp"] == 40000  # +400%
            assert body["transfer_out_cents"] == 0

            cats = {c["category"]: c for c in body["categories"]}
            assert set(cats) == {"餐饮", "交通", "娱乐"}
            assert cats["餐饮"]["amount_cents"] == 3000
            assert cats["餐饮"]["previous_amount_cents"] == 2000
            assert cats["交通"]["amount_cents"] == 5000
            assert cats["交通"]["previous_amount_cents"] == 0
            assert cats["娱乐"]["amount_cents"] == 2000
            assert cats["娱乐"]["previous_amount_cents"] == 0

            daily = {d["date"]: d["amount_cents"] for d in body["daily"]}
            assert daily["2026-10-10"] == 3000
            assert daily["2026-10-15"] == 5000
            assert daily["2026-10-20"] == 2000
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_r4_mom_change_bp_null_when_prev_zero(obligations_db):
    """Only current-month spending → previous_total_cents=0, mom_change_bp=None."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)

            await _insert_card_spend(
                conn,
                tx_id="tx_oct_a",
                xfer_id="xfr_oct_a",
                customer_id="cus_zs",
                account_id="acc_zs_chk",
                amount_cents=4500,
                category="生活服务",
                occurred_at=dtm(2026, 10, 12, 10, 0, 0, tzinfo=tz_utc),
                balance_cents=600_000 - 4500,
            )

        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await _read(
                client, "get_spending_summary", {"month": "2026-10", "account_id": None}
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["total_cents"] == 4500
            assert body["previous_total_cents"] == 0
            assert body["mom_change_cents"] == 4500
            assert body["mom_change_bp"] is None
            assert len(body["categories"]) == 1
            assert body["categories"][0]["category"] == "生活服务"
            assert body["categories"][0]["previous_amount_cents"] == 0
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_r4_account_id_filter(obligations_db):
    """Filter by account_id — only that account's spending is returned."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)

            await _insert_card_spend(
                conn,
                tx_id="tx_chk_1",
                xfer_id="xfr_chk_1",
                customer_id="cus_zs",
                account_id="acc_zs_chk",
                amount_cents=3000,
                category="餐饮",
                occurred_at=dtm(2026, 10, 5, 12, 0, 0, tzinfo=tz_utc),
                balance_cents=600_000 - 3000,
            )
            await _insert_card_spend(
                conn,
                tx_id="tx_sav_1",
                xfer_id="xfr_sav_1",
                customer_id="cus_zs",
                account_id="acc_zs_sav",
                amount_cents=8000,
                category="购物",
                occurred_at=dtm(2026, 10, 8, 15, 0, 0, tzinfo=tz_utc),
                balance_cents=2_000_000 - 8000,
            )

        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await _read(
                client, "get_spending_summary", {"month": "2026-10", "account_id": "acc_zs_chk"}
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["total_cents"] == 3000
            cats = {c["category"] for c in body["categories"]}
            assert cats == {"餐饮"}

            r = await _read(
                client, "get_spending_summary", {"month": "2026-10", "account_id": "acc_zs_sav"}
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["total_cents"] == 8000
            assert {c["category"] for c in body["categories"]} == {"购物"}
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_r4_cross_customer_account_id_rejected(obligations_db):
    """Query with another customer's account_id → 404."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)

        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await _read(
                client, "get_spending_summary", {"month": "2026-10", "account_id": "acc_ls_chk"}
            )
            assert r.status_code == 404, r.text

            r = await _read(
                client,
                "get_spending_summary",
                {"month": "2026-10", "account_id": "acc_nonexistent"},
            )
            assert r.status_code == 404, r.text
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_r4_invalid_month_returns_422(obligations_db):
    """Non YYYY-MM month parameter → 422."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)

        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await _read(client, "get_spending_summary", {"month": "202613", "account_id": None})
            assert r.status_code == 422, r.text

            r = await _read(
                client, "get_spending_summary", {"month": "not-a-month", "account_id": None}
            )
            assert r.status_code == 422, r.text

            r = await _read(
                client, "get_spending_summary", {"month": "2026-00", "account_id": None}
            )
            assert r.status_code == 422, r.text
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())
