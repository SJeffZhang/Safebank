"""REMINDER jobs execute read-only business effects and preserve customer identity."""

import asyncio
from datetime import timedelta

from app import models as m
from app.db import engine
from app.events import EventService
from app.scheduler import Scheduler
from sqlalchemy import select
from sqlalchemy.pool import NullPool

from tests.obligations_data.fixtures import install_core, install_obligations
from tests.obligations_data.test_demo_api import clean_database  # noqa: F401


async def _queued(config):
    admin = engine(config.admin_url, poolclass=NullPool)
    async with admin.begin() as conn:
        await install_core(conn)
        await install_obligations(conn)
        billing = (
            (await conn.execute(select(m.merchant_billing).order_by(m.merchant_billing.c.id)))
            .mappings()
            .first()
        )
        scan_now = billing["next_due_at"] - timedelta(days=2) + timedelta(hours=3)
        await conn.execute(m.clock.update().where(m.clock.c.id == 1).values(sim_now=scan_now))
        scheduler = Scheduler(admin, s1_url="http://unused", s1_token="unused")
        await scheduler.scan_reminders(conn, scan_now)
        job = (
            (
                await conn.execute(
                    select(m.event_jobs).where(
                        m.event_jobs.c.kind == "REMINDER", m.event_jobs.c.source_id == billing["id"]
                    )
                )
            )
            .mappings()
            .one()
        )
    return admin, job


async def _execute(db, job):
    async with db.begin() as conn:
        cust = job["immutable_payload"]["payload"]["customer_id"]
        clock = (await conn.execute(select(m.clock))).mappings().one()
        version = await conn.scalar(
            select(m.state_versions.c.version).where(m.state_versions.c.customer_id == cust)
        )
        return await EventService().execute_event(
            conn,
            job["id"],
            {cust: version},
            clock["version"],
            job["payload_hash"],
            {
                "type": "SYSTEM_REMINDER",
                "billing_id": job["source_id"],
                "instruction_id": job["immutable_payload"]["payload"]["instruction_id"],
            },
        )


def test_reminder_definition_and_execution_are_current_and_do_not_move_money(obligations_db):
    async def run():
        db, job = await _queued(obligations_db)
        try:
            async with db.connect() as conn:
                event = await EventService().get_event(conn, job["id"], None)
                before = (await conn.execute(select(m.accounts))).mappings().all()
                versions_before = (await conn.execute(select(m.state_versions))).mappings().all()
                ledger_before = (await conn.execute(select(m.transfers))).mappings().all()
            assert event["customer_id"] == job["immutable_payload"]["payload"]["customer_id"]
            assert event["kind"] == "REMINDER"
            result = await _execute(db, job)
            assert result["status"] == "SUCCESS", result
            replay = await _execute(db, job)
            assert replay["replayed"]
            async with db.connect() as conn:
                assert (await conn.execute(select(m.accounts))).mappings().all() == before
                assert (
                    await conn.execute(select(m.state_versions))
                ).mappings().all() == versions_before
                assert (await conn.execute(select(m.transfers))).mappings().all() == ledger_before
                assert (await conn.execute(select(m.event_executions))).mappings().one()[
                    "event_id"
                ] == job["id"]
        finally:
            await db.dispose()

    asyncio.run(run())


def test_cancelled_mandate_reminder_finishes_without_success_or_money(obligations_db):
    async def run():
        db, job = await _queued(obligations_db)
        try:
            async with db.begin() as conn:
                await conn.execute(
                    m.standing_instructions.update()
                    .where(
                        m.standing_instructions.c.id
                        == job["immutable_payload"]["payload"]["instruction_id"]
                    )
                    .values(status="DELETED")
                )
            result = await _execute(db, job)
            assert result["status"] == "BLOCKED", result
        finally:
            await db.dispose()

    asyncio.run(run())
