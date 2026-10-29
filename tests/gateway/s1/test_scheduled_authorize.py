"""S1 `POST /internal/scheduled/authorize` endpoint tests (PRD §9.5a).

Run with ``GATEWAY_DB_TESTS=1 pytest tests/gateway/s1 -q`` (tmpfs PG).
"""

from __future__ import annotations

import asyncio

from sqlalchemy import text
from sqlalchemy.pool import NullPool

from safebank_contracts.codes import Code
from services.gateway.app.db import engine
from tests.gateway.s1.conftest import (
    _seed_event_decision,
    build_app,
)

URL = "/internal/scheduled/authorize"


def _runtime_db(app):
    return engine(app.db_url, poolclass=NullPool)


def test_terminal_replay_returns_stored_result(app_config):
    """A stored terminal event_decisions row is returned without re-evaluation."""

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        async with db.begin() as conn:
            await _seed_event_decision(
                conn, event_id="evt_terminal_1", status="SUCCESS", code=None
            )

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
