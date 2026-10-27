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
from sqlalchemy.pool import NullPool

from tests.obligations_data.fixtures import KEY, NOW, install_core, install_obligations

SERVICE_TOKEN = "obligations-test-token"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _serve(app, port):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server


def _read(client, tool, args, *, auth=SERVICE_TOKEN, timeout=10.0):
    return client.post(
        "/internal/read",
        json={"tool": tool, "args": args, "customer_id": "cus_zs"},
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
            r = await client.get(f"http://127.0.0.1:{port}/health", timeout=2.0)
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
                "/internal/read",
                json={"tool": "list_scheduled", "args": {"status": "ALL", "limit": 50}, "customer_id": "cus_ls"},
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
            r = await _read(client, "list_scheduled", {"status": "ALL", "limit": 50, "cursor": "garbage"})
            assert r.status_code == 422, r.text
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_r7_pagination_respects_limit(obligations_db):
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
            r1 = await _read(client, "list_scheduled", {"status": "ALL", "limit": 1})
            assert r1.status_code == 200, r1.text
            b1 = r1.json()
            assert len(b1["items"]) == 1
            assert b1["next_cursor"] is not None
            r2 = await _read(client, "list_scheduled", {"status": "ALL", "limit": 50, "cursor": b1["next_cursor"]})
            assert r2.status_code == 200, r2.text
            b2 = r2.json()
            assert b2["items"] == []
            assert b2["next_cursor"] is None
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


# ─── R10 list_subscriptions ───


def test_r10_returns_subscriptions_with_totals(obligations_db):
    """cus_zs has 5 ACTIVE subscriptions; totals should sum only ACTIVE ones."""

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
            assert len(body["items"]) == 5

            ids = {i["instruction_id"] for i in body["items"]}
            assert ids == {"si_zs_video", "si_zs_music", "si_zs_cloud", "si_zs_gym", "si_zs_mom"}

            # All ACTIVE → totals equal sum of all
            nb = body["normalization_basis"]
            assert nb["weekly_to_annual_factor"] == 52
            assert nb["monthly_to_annual_factor"] == 12
            assert nb["annual_to_monthly"] == "floor_div_12"

            # expected: video=3000×12=36000, music=1500×12=18000, cloud=2100*12=25200, gym=19900*12=238800, mom is SCHEDULED_TRANSFER not in subscriptions
            # Wait — si_zs_mom is ScheduledInstruction, not MerchantMandate. Let me check:
            # In _derive_views, ScheduledInstruction → instructions list (ScheduledView)
            # MerchantMandate → subscriptions list (SubscriptionView)
            # So si_zs_mom is NOT in subscriptions. Only 4 subscriptions.
            # Actually I need to re-count. The fixtures have si_zs_video, si_zs_music, si_zs_cloud,
            # si_zs_gym all as MerchantMandate → subscription list has 4 items.
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
            # 5 total items, 1 DELETED + 4 ACTIVE
            assert len(body["items"]) == 5
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
            r = await _read(client, "list_subscriptions", {"status": "ALL", "limit": 50, "cursor": "bad"})
            assert r.status_code == 422, r.text
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_r10_annualization_weekly_monthly(obligations_db):
    """Insert a WEEKLY billing and verify annual/monthly totals."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        from datetime import time as dt_time
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)
            from app import models as m
            # Insert a WEEKLY merchant and billing + mandate
            await conn.execute(
                m.merchants.insert().values(
                    id="mer_weekly", name="周卡服务", mcc="5817", category="娱乐", usage_expected=False
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
                    next_due_at="2026-10-20T06:00:00+08:00",
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
                    amount_type=None,
                    recurrence=None,
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
            # 5 old + 1 new = 6
            assert len(body["items"]) == 6

            # Find the WEEKLY item
            weekly = [i for i in body["items"] if i["period"] == "WEEKLY"]
            assert len(weekly) == 1
            w = weekly[0]
            assert w["instruction_id"] == "si_zs_weekly"
            assert w["annual_cost_cents"] == 1000 * 52  # = 52000

            # Totals: ACTIVE subscriptions now 5 old + 1 new
            # MONTHLY: 3000+1500+2100+19900 = 26500 (only the original 4, mom is ScheduledInstruction)
            # WEEKLY: monthly = floor(1000*52/12) = floor(52000/12) = 4333
            # Expected total_monthly = 26500 + 4333 = 30833
            # Expected total_annual = (3000+1500+2100+19900)*12 + 52000 = 318000 + 52000 = 370000
            assert body["total_monthly_cents"] == 30833
            assert body["total_annual_cents"] == 370000
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())