"""R14/R15/R17 product read tool acceptance tests (C6b).

Uses the real obligations fixtures against a tmpfs Postgres database served via
HTTP, matching the pattern in test_reads.py. Executed with make test-obligations.

The three reads are customer-agnostic (global product catalog): any customer
sees the same products.
"""

import asyncio
import socket
import threading
from datetime import date, datetime, timezone
from decimal import Decimal

import httpx
import pytest
import uvicorn
from app.db import engine
from app.main import create_app
from safebank_bankcore.nav import nav_history
from safebank_bankcore.products import (
    RECENT_RETURN_WINDOW_TRADING_DAYS,
    ProductDef,
    get_product,
    recent_return_bp,
)
from sqlalchemy import text as sa_text
from sqlalchemy.pool import NullPool

from tests.obligations_data.fixtures import KEY, install_core, install_obligations

SERVICE_TOKEN = "obligations-test-token"
ON_DATE = date(2026, 10, 15)

PRODUCTS = {
    "prod_zmf": ProductDef(
        id="prod_zmf",
        name="货币基金A",
        product_type="MMF",
        risk_level=1,
        risk_label="低风险",
        base_nav=Decimal("1.0000"),
        base_date=date(2026, 1, 1),
        confirm_days=1,
        settle_days=1,
        min_amount_cents=100,
        risk_disclosure="货币市场基金，风险较低，不保本。",
        disclosure_version="v1",
    ),
    "prod_zq": ProductDef(
        id="prod_zq",
        name="纯债基金B",
        product_type="BOND",
        risk_level=2,
        risk_label="中低风险",
        base_nav=Decimal("1.5000"),
        base_date=date(2026, 1, 1),
        confirm_days=2,
        settle_days=2,
        min_amount_cents=1000,
        risk_disclosure="债券型基金，主要投资债券。",
        disclosure_version="v1",
    ),
    "prod_gp": ProductDef(
        id="prod_gp",
        name="股票基金C",
        product_type="EQUITY",
        risk_level=4,
        risk_label="中高风险",
        base_nav=Decimal("2.0000"),
        base_date=date(2026, 1, 1),
        confirm_days=3,
        settle_days=3,
        min_amount_cents=10000,
        risk_disclosure="股票型基金，净值波动较大。",
        disclosure_version="v1",
    ),
}

COMPARISON_FIELDS = [
    "name",
    "risk_level",
    "risk_label",
    "product_type",
    "nav",
    "recent_return_bp",
    "min_amount_cents",
    "confirm_days",
    "settle_days",
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


async def _install_products(conn) -> None:
    from app import models as m

    for p in PRODUCTS.values():
        await conn.execute(
            m.products.insert().values(
                id=p.id,
                name=p.name,
                product_type=p.product_type,
                risk_level=p.risk_level,
                risk_label=p.risk_label,
                base_nav=p.base_nav,
                base_date=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
                confirm_days=p.confirm_days,
                settle_days=p.settle_days,
                min_amount_cents=p.min_amount_cents,
                risk_disclosure=p.risk_disclosure,
                disclosure_version=p.disclosure_version,
            )
        )


async def _prepare_app(obligations_db):
    admin = engine(obligations_db.admin_url, poolclass=NullPool)
    runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
    async with admin.begin() as conn:
        await install_core(conn)
        await install_obligations(conn)
        await _install_products(conn)
    app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
    port = free_port()
    _serve(app, port)
    return app, port, admin, runtime


async def _await_app(client, port):
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


def _expected_view(product_id: str) -> dict:
    """Compute the expected ProductView payload via bankcore for on_date."""
    p = PRODUCTS[product_id]
    view = get_product((*PRODUCTS.values(),), product_id, ON_DATE)
    hist = nav_history(p.base_nav, p.base_date, p.product_type, ON_DATE, count=30)
    return {
        "id": view["id"],
        "name": view["name"],
        "product_type": view["product_type"],
        "risk_level": view["risk_level"],
        "risk_label": view["risk_label"],
        "nav": format(view["nav"], ".4f"),
        "nav_date": view["nav_date"].isoformat(),
        "recent_return_bp": view["recent_return_bp"],
        "confirm_days": view["confirm_days"],
        "settle_days": view["settle_days"],
        "min_amount_cents": view["min_amount_cents"],
        "risk_disclosure": view["risk_disclosure"],
        "disclosure_version": view["disclosure_version"],
        "nav_history": [{"date": d.isoformat(), "nav": format(n, ".4f")} for d, n in hist],
    }


# ─── R14 list_products ───


def test_r14_returns_all_products_sorted(obligations_db):
    async def check():
        app, port, admin, runtime = await _prepare_app(obligations_db)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await _read(
                client, "list_products", {"limit": 50, "max_risk_level": None, "product_type": None}
            )
            assert r.status_code == 200, r.text
            body = r.json()
            ids = [i["id"] for i in body["items"]]
            assert ids == sorted(PRODUCTS)
            assert body["next_cursor"] is None
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_r14_filters_max_risk_level(obligations_db):
    async def check():
        app, port, admin, runtime = await _prepare_app(obligations_db)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await _read(
                client,
                "list_products",
                {"limit": 50, "max_risk_level": 1, "product_type": None},
            )
            assert r.status_code == 200, r.text
            ids = [i["id"] for i in r.json()["items"]]
            assert ids == ["prod_zmf"]
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_r14_filters_product_type(obligations_db):
    async def check():
        app, port, admin, runtime = await _prepare_app(obligations_db)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await _read(
                client,
                "list_products",
                {"limit": 50, "max_risk_level": None, "product_type": "EQUITY"},
            )
            assert r.status_code == 200, r.text
            ids = [i["id"] for i in r.json()["items"]]
            assert ids == ["prod_gp"]
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_r14_pagination_respects_limit(obligations_db):
    async def check():
        app, port, admin, runtime = await _prepare_app(obligations_db)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r1 = await _read(
                client, "list_products", {"limit": 1, "max_risk_level": None, "product_type": None}
            )
            assert r1.status_code == 200, r1.text
            b1 = r1.json()
            assert len(b1["items"]) == 1
            assert b1["next_cursor"] is not None
            r2 = await _read(
                client,
                "list_products",
                {"limit": 50, "max_risk_level": None, "product_type": None, "cursor": b1["next_cursor"]},
            )
            assert r2.status_code == 200, r2.text
            b2 = r2.json()
            assert len(b2["items"]) == len(PRODUCTS) - 1
            assert b2["next_cursor"] is None
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


# ─── R15 compare_products ───


def test_r15_compare_fields_and_values(obligations_db):
    async def check():
        app, port, admin, runtime = await _prepare_app(obligations_db)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await _read(
                client, "compare_products", {"product_ids": ["prod_zmf", "prod_gp"]}
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["comparison_fields"] == COMPARISON_FIELDS
            ids = [i["id"] for i in body["items"]]
            assert ids == ["prod_zmf", "prod_gp"]
            zmf = body["items"][0]
            assert zmf["name"] == "货币基金A"
            assert zmf["risk_level"] == 1
            assert zmf["nav"] == format(Decimal("1.0000"), ".4f")
            assert zmf["recent_return_bp"] == recent_return_bp(
                PRODUCTS["prod_zmf"], ON_DATE, RECENT_RETURN_WINDOW_TRADING_DAYS
            )
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_r15_requires_2_to_4_unique_ids(obligations_db):
    async def check():
        app, port, admin, runtime = await _prepare_app(obligations_db)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await _read(client, "compare_products", {"product_ids": ["prod_zmf"]})
            assert r.status_code == 422, r.text
            r = await _read(
                client,
                "compare_products",
                {"product_ids": ["prod_zmf", "prod_zq", "prod_gp", "prod_zmf", "prod_zmf"]},
            )
            assert r.status_code == 422, r.text
            r = await _read(
                client, "compare_products", {"product_ids": ["prod_zmf", "prod_zmf"]}
            )
            assert r.status_code == 422, r.text
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_r15_unknown_id_404(obligations_db):
    async def check():
        app, port, admin, runtime = await _prepare_app(obligations_db)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await _read(
                client, "compare_products", {"product_ids": ["prod_zmf", "prod_nope"]}
            )
            assert r.status_code == 404, r.text
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


# ─── R17 get_product ───


def test_r17_full_disclosure_and_nav(obligations_db):
    async def check():
        app, port, admin, runtime = await _prepare_app(obligations_db)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await _read(client, "get_product", {"product_id": "prod_zq"})
            assert r.status_code == 200, r.text
            body = r.json()
            expected = _expected_view("prod_zq")
            assert body["id"] == expected["id"]
            assert body["risk_disclosure"] == expected["risk_disclosure"]
            assert body["disclosure_version"] == expected["disclosure_version"]
            assert body["nav"] == expected["nav"]
            assert body["nav_date"] == expected["nav_date"]
            assert body["recent_return_bp"] == expected["recent_return_bp"]
            assert len(body["nav_history"]) == 30
            assert body["nav_history"] == expected["nav_history"]
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_r17_unknown_id_404(obligations_db):
    async def check():
        app, port, admin, runtime = await _prepare_app(obligations_db)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await _read(client, "get_product", {"product_id": "prod_nope"})
            assert r.status_code == 404, r.text
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())
