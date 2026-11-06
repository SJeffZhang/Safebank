"""Original failures stay visible: idempotency cleanup and unhandled-error logging."""

from __future__ import annotations

import asyncio
import logging

import httpx
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from services.gateway.app.db import engine
from services.gateway.app.idempotency import request_idempotent
from services.gateway.app.middleware import install_middleware


def test_database_error_in_produce_is_reraised_unmasked(gateway_db):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)

        caught = None
        try:
            async with db.begin() as conn:

                async def produce():
                    await conn.execute(text("SELECT 1 / 0"))
                    return 200, {}

                await request_idempotent(
                    conn,
                    customer_id="cus_err",
                    route="POST /robustness",
                    request_id="req_err",
                    body={"a": 1},
                    produce=produce,
                )
        except DBAPIError as exc:
            caught = exc
        assert caught is not None
        assert "division by zero" in str(caught), str(caught)
        # The aborted transaction rolled back, so the same id can be retried.
        async with db.begin() as conn:
            status, body, replayed = await request_idempotent(
                conn,
                customer_id="cus_err",
                route="POST /robustness",
                request_id="req_err",
                body={"a": 1},
                produce=lambda: _ok(),
            )
        assert (status, body, replayed) == (200, {"ok": True}, False)
        await db.dispose()

    asyncio.run(run())


async def _ok():
    return 200, {"ok": True}


def test_unhandled_exception_is_logged_with_trace_id(caplog):
    app = FastAPI()
    install_middleware(app)

    @app.get("/boom")
    async def boom():
        raise RuntimeError("boom-detail")

    async def run():
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://gateway") as client:
            return await client.get("/boom", headers={"X-Request-ID": "trace-robust-1"})

    with caplog.at_level(logging.ERROR, logger="safebank.gateway"):
        response = asyncio.run(run())
    assert response.status_code == 503
    body = response.json()["error"]
    assert body["trace_id"] == "trace-robust-1"
    assert "boom-detail" not in response.text
    records = [r for r in caplog.records if r.name == "safebank.gateway"]
    assert records, "unhandled exception was not logged"
    assert "trace-robust-1" in records[-1].getMessage()
    assert records[-1].exc_info is not None
    assert "boom-detail" in str(records[-1].exc_info[1])
