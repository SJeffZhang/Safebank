"""L3 event API real-PG acceptance tests (PRD §7.11, §8.5).

Tests GET /internal/events/{id}, POST /internal/events/{id}/execute,
and POST /internal/events/{id}/block against a tmpfs PostgreSQL database
via the EventService directly (same pattern as test_standing_exec.py).
"""

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from app import models as m
from app.db import engine
from app.events import EventService
from safebank_contracts.codes import Code
from sqlalchemy import text
from sqlalchemy.pool import NullPool

from tests.obligations_data.fixtures import KEY, NOW, install_core, install_obligations

pytestmark = pytest.mark.integration

EVT_ID = "evt_test_001"
PAYLOAD = "si_zs_mom|100000|acc_zs_chk"
PAYLOAD_HASH = hashlib.sha256(PAYLOAD.encode()).hexdigest()
DUE = NOW + timedelta(hours=1)


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


async def _seed_event(conn, *, event_id, kind, source_id, due_at, payload_hash,
                      immutable_payload=None, period_key=None,
                      status="PENDING", rank=3):
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


# ── test 1: GET shape ────────────────────────────────────────────────────


def test_get_event_shape(obligations_db):
    """GET /internal/events/{id} returns event definition with expected keys."""

    async def check():
        await _setup(obligations_db)
        admin = _admin_conn(obligations_db)
        async with admin.begin() as conn:
            await _seed_event(
                conn, event_id=EVT_ID, kind="SCHEDULED_TRANSFER",
                source_id="si_zs_mom", due_at=DUE, payload_hash=PAYLOAD_HASH,
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
        assert result["due_at"] == DUE.astimezone(timezone.utc).isoformat()
        assert result["payload_hash"] == PAYLOAD_HASH
        assert result["source_id"] == "si_zs_mom"
        assert result["job_status"] == "PENDING"
        assert result["execution"] is None

    asyncio.run(check())