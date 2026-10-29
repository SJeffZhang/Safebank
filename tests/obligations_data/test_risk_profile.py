"""get_risk_profile (R6) read tool acceptance tests.

Real-PG tests against a tmpfs Postgres database served via HTTP, following the
pattern in test_reads.py. These cover the live bug where customers.risk_valid_until
holds a timezone-aware datetime string like "2027-09-01T10:00:00+08:00" that
date.fromisoformat cannot parse (HTTP 500).
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


async def _risk_profile(client, customer_id):
    return await client.post(
        "/internal/read/get_risk_profile",
        json={"args": {}, "customer_id": customer_id},
        headers={"X-Service-Token": SERVICE_TOKEN},
        timeout=10.0,
    )


async def _set_customer_risk(conn, customer_id, risk_level, risk_valid_until):
    from app import models as m

    await conn.execute(
        m.customers.update()
        .where(m.customers.c.id == customer_id)
        .values(
            risk_level=risk_level,
            risk_assessed_at=NOW,
            risk_valid_until=risk_valid_until,
        )
    )


def test_risk_profile_future_datetime_string_is_valid(obligations_db):
    """A +08:00 datetime risk_valid_until in the future → 200 and is_valid true."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)
            await _set_customer_risk(
                conn, "cus_ls", risk_level=3, risk_valid_until="2027-09-01T10:00:00+08:00"
            )
        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await _risk_profile(client, "cus_ls")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["level"] == 3
            assert body["is_valid"] is True
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_risk_profile_expired_datetime_string_is_invalid(obligations_db):
    """A +08:00 datetime risk_valid_until before sim_now → 200 and is_valid false."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)
            await _set_customer_risk(
                conn, "cus_ls", risk_level=3, risk_valid_until="2026-01-01T00:00:00+08:00"
            )
        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await _risk_profile(client, "cus_ls")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["level"] == 3
            assert body["is_valid"] is False
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_risk_profile_without_assessment_is_invalid(obligations_db):
    """A customer with no risk assessment → 200 and is_valid false."""

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
            r = await _risk_profile(client, "cus_ww")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["level"] is None
            assert body["assessment_id"] is None
            assert body["is_valid"] is False
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())
