"""L3 demo/eval routes acceptance tests (PRD §9.9 D1/D2 reset+prepare, D4 events).

Reset/prepare re-run the idempotent demo seed installer on the owner role; the
ledger is append-only so no route ever deletes rows.  Events inserts exactly one
``event_jobs`` row (kind DEMO_EVENT) idempotently on the payload hash.

These routes only exist when ``demo_mode`` is true; otherwise they 404.
Executed with ``make test-obligations`` against a tmpfs PostgreSQL.
"""

import asyncio
import socket
import threading

import httpx
import pytest
import uvicorn
from app import models as m
from app.db import engine
from app.main import create_app
from sqlalchemy import func, select
from sqlalchemy import text as sa_text
from sqlalchemy.pool import NullPool

from tests.obligations_data.fixtures import KEY, install_core

SERVICE_TOKEN = "obligations-test-token"
DUE_AT = "2026-10-15T09:00:00+08:00"


@pytest.fixture(autouse=True)
def clean_database(obligations_db):
    """Truncate every application table before each test for an isolated dataset."""

    async def _truncate():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        async with admin.begin() as conn:
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


def _post(client, path, body, auth=SERVICE_TOKEN):
    return client.post(path, json=body, headers={"X-Service-Token": auth}, timeout=10.0)


def _event_body(**overrides):
    body = {
        "kind": "EXTERNAL_CREDIT",
        "customer_id": "cus_zs",
        "account_id": "acc_zs_chk",
        "amount_cents": 100,
        "due_at": DUE_AT,
    }
    body.update(overrides)
    return body


def test_demo_routes_404_when_demo_mode_false(obligations_db):
    async def check():
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        app = create_app(
            db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY, demo_mode=False
        )
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await _post(client, "/internal/demo/reset", {"seed_id": "l1_seed_v1"})
            assert r.status_code == 404, r.text
            r = await _post(client, "/internal/demo/prepare", {"scenario_id": "l1_seed_v1"})
            assert r.status_code == 404, r.text
            r = await _post(client, "/internal/demo/events", _event_body())
            assert r.status_code == 404, r.text
        await runtime.dispose()

    asyncio.run(check())


def test_reset_idempotent(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)

        async def count(table):
            async with admin.connect() as conn:
                return (await conn.execute(select(func.count()).select_from(table))).scalar_one()

        before = await count(m.customers)
        app = create_app(
            db=runtime,
            service_token=SERVICE_TOKEN,
            fingerprint_key=KEY,
            demo_mode=True,
            admin_db=admin,
        )
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r1 = await _post(client, "/internal/demo/reset", {"seed_id": "l1_seed_v1"})
            assert r1.status_code == 200, r1.text
            assert r1.json() == {"dataset_revision": "l1_seed_v1.0"}
            r2 = await _post(client, "/internal/demo/reset", {"seed_id": "l1_seed_v1"})
            assert r2.status_code == 200, r2.text
            assert r2.json() == {"dataset_revision": "l1_seed_v1.0"}
            bad = await _post(client, "/internal/demo/reset", {"seed_id": "nope"})
            assert bad.status_code == 422
            bad = await _post(client, "/internal/demo/prepare", {"scenario_id": "nope"})
            assert bad.status_code == 422

        after = await count(m.customers)
        assert before == 4
        assert after == before
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_events_creates_one_job_and_retry_returns_same_id(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
        app = create_app(
            db=runtime,
            service_token=SERVICE_TOKEN,
            fingerprint_key=KEY,
            demo_mode=True,
            admin_db=admin,
        )
        port = free_port()
        _serve(app, port)
        body = _event_body()
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r1 = await _post(client, "/internal/demo/events", body)
            assert r1.status_code == 200, r1.text
            eid = r1.json()["event_id"]
            assert eid.startswith("evt_demo_")
            r2 = await _post(client, "/internal/demo/events", body)
            assert r2.status_code == 200, r2.text
            assert r2.json()["event_id"] == eid

        async with admin.connect() as conn:
            rows = (await conn.execute(select(m.event_jobs))).mappings().all()
            assert len(rows) == 1
            row = rows[0]
            assert row["id"] == eid
            assert row["kind"] == "DEMO_EVENT"
            assert row["status"] == "PENDING"
            assert row["period_key"] == DUE_AT
            assert row["source_id"] == f"demo:{row['payload_hash'][:16]}"
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_events_bad_kind_and_amount_422(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        app = create_app(
            db=runtime,
            service_token=SERVICE_TOKEN,
            fingerprint_key=KEY,
            demo_mode=True,
            admin_db=admin,
        )
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            r = await _post(client, "/internal/demo/events", _event_body(kind="TRANSFER"))
            assert r.status_code == 422, r.text
            r = await _post(client, "/internal/demo/events", _event_body(amount_cents=0))
            assert r.status_code == 422, r.text
            r = await _post(
                client, "/internal/demo/events", _event_body(due_at="2026-10-15T09:00:00")
            )
            assert r.status_code == 422, r.text
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_events_foreign_account_404(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
        app = create_app(
            db=runtime,
            service_token=SERVICE_TOKEN,
            fingerprint_key=KEY,
            demo_mode=True,
            admin_db=admin,
        )
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            await _await_app(client, port)
            # acc_zs_chk belongs to cus_zs, not cus_ls.
            r = await _post(
                client,
                "/internal/demo/events",
                _event_body(customer_id="cus_ls", account_id="acc_zs_chk"),
            )
            assert r.status_code == 404, r.text
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())
