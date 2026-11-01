"""Real gateway PG+HTTP with authoritative L3 test double; never a live demo."""

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.pool import NullPool

from services.gateway.app import tables as t
from services.gateway.app.db import engine
from services.gateway.app.internal.scheduled import _backfill_grant
from tests.gateway.s1.conftest import WALL, build_app, make_event_def
from tests.gateway.s1.test_revoked_grant_boundary_unit import merchant_case


@pytest.mark.parametrize("tombstone", [False, True])
@pytest.mark.parametrize("bank_status", ["DELETED", "ACTIVE"])
def test_revoked_merchant_is_persistently_refused_without_execute(
    app_config, tombstone, bank_status
):
    async def run():
        app = await build_app(app_config)
        db = engine(app.db_url, poolclass=NullPool)
        try:
            event, grant, state = merchant_case(tombstone=tombstone, bank_status=bank_status)
            app.fake.set_snapshot(state)
            app.fake.set_event(event)
            async with db.begin() as conn:
                await conn.execute(t.scheduled_grants.insert().values(**grant))
            for _ in range(2):
                response = await app.client.post(
                    "/internal/scheduled/authorize",
                    json={"event_id": event["event_id"], "attempt": 1},
                    headers=app.scheduler_headers(),
                )
                assert response.status_code == 200
                result = response.json()
                assert result["status"] == "BLOCKED"
                assert result["code"] == (
                    "MANDATE_NOT_ACTIVE" if bank_status == "DELETED" else "SCHEDULED_BYPASS"
                )
            assert not [call for call in app.fake.calls if call["op"] == "event_execute"]
            assert len([c for c in app.fake.calls if c["op"] == "event_block"]) == 1
            async with db.connect() as conn:
                stored = (await conn.execute(select(t.event_decisions))).mappings().one()
                assert stored["code"] == result["code"] and stored["status"] == "BLOCKED"
                notices = (await conn.execute(select(t.notifications))).mappings().all()
                assert len(notices) == 1 and notices[0]["type"] == "CHARGE_BLOCKED"
                assert await conn.scalar(select(t.scheduled_grants.c.status)) == "REVOKED"
        finally:
            await app.client.aclose()
            await db.dispose()

    asyncio.run(run())


def test_actual_pg_insert_conflict_returns_revoked_row_instead_of_local_active(app_config):
    async def run():
        app = await build_app(app_config)
        db = engine(app.db_url, poolclass=NullPool)
        event = make_event_def(event_id="evt_backfill", source_id="si_backfill")
        try:
            async with db.begin() as conn:
                await conn.execute(
                    t.scheduled_grants.insert().values(
                        instruction_id="si_backfill",
                        customer_id="cus_a",
                        source_plan_id="pln_cancel",
                        scope_json={"revocation": {}},
                        scope_hash="0" * 64,
                        approved_risk="RED",
                        status="REVOKED",
                        created_at=WALL,
                        revoked_at=WALL,
                    )
                )

            async def execution(*args):
                return {
                    "status": "COMMITTED",
                    "result": {
                        "status": "EXECUTED",
                        "steps": [
                            {
                                "tool": "schedule_transfer",
                                "status": "EXECUTED",
                                "created_ids": {"instruction_id": "si_backfill"},
                                "result": {
                                    "instruction_id": "si_backfill",
                                    "grant_scope_hash": event["grant_scope_hash"],
                                },
                            }
                        ],
                    },
                }

            actual = await _backfill_grant(
                db, SimpleNamespace(execution=execution), event, lambda: WALL
            )
            assert actual["status"] == "REVOKED" and actual["source_plan_id"] == "pln_cancel"
        finally:
            await app.client.aclose()
            await db.dispose()

    asyncio.run(run())
