"""Real L3 HTTP/PG definitions consumed by real S1 HTTP/PG, without rich event fakes."""

import asyncio

import httpx
import pytest
from app import models as m
from app.main import create_app
from safebank_bankcore.grants import instruction_scope
from sqlalchemy import select, text

from tests.gateway.db.conftest import gateway_db  # noqa: F401
from tests.gateway.s1.conftest import _seed_grant, app_config, build_app  # noqa: F401
from tests.obligations_data.fixtures import KEY
from tests.obligations_data.test_events_api import (
    SI_DUE,  # noqa: F401
    _admin_conn,
    _runtime_conn,
    _seed_event,
    _setup,
    clean_database,  # noqa: F401
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "kind,cancelled",
    [("SCHEDULED_TRANSFER", False), ("MERCHANT_CHARGE", False), ("MERCHANT_CHARGE", True)],
)
def test_real_event_roundtrip(obligations_db, app_config, kind, cancelled):  # noqa: F811
    async def run():
        await _setup(obligations_db)
        admin = _admin_conn(obligations_db)
        source_id = "si_zs_mom" if kind == "SCHEDULED_TRANSFER" else "bill_zs_video"
        instruction_id = "si_zs_mom" if kind == "SCHEDULED_TRANSFER" else "si_zs_video"
        async with admin.begin() as conn:
            await conn.execute(m.clock.update().values(sim_now=SI_DUE))
            if cancelled:
                await conn.execute(
                    m.standing_instructions.update()
                    .where(m.standing_instructions.c.id == instruction_id)
                    .values(status="DELETED")
                )
            await _seed_event(
                conn,
                event_id="evt_real_s1",
                kind=kind,
                source_id=source_id,
                due_at=SI_DUE,
                payload_hash="c" * 64,
            )
            inst = (
                (
                    await conn.execute(
                        select(m.standing_instructions).where(
                            m.standing_instructions.c.id == instruction_id
                        )
                    )
                )
                .mappings()
                .one()
            )
            billing = None
            if kind == "MERCHANT_CHARGE":
                billing = (
                    (
                        await conn.execute(
                            select(m.merchant_billing).where(m.merchant_billing.c.id == source_id)
                        )
                    )
                    .mappings()
                    .one()
                )
            scope = instruction_scope(inst, billing)
        bank_db = _runtime_conn(obligations_db)
        app = await build_app(app_config)
        real = create_app(
            db=bank_db,
            service_token="test-gateway-service-token-32b!",
            fingerprint_key=KEY,
            demo_mode=False,
            scheduler_enabled=False,
        )
        app.l3._client._transport = httpx.ASGITransport(app=real)
        from sqlalchemy.pool import NullPool

        from services.gateway.app.db import engine

        gateway = engine(app.db_url, poolclass=NullPool)
        async with gateway.begin() as conn:
            await _seed_grant(
                conn,
                instruction_id=instruction_id,
                customer_id="cus_zs",
                source_plan_id="pln_seed",
                scope_json={
                    "instruction_scope": scope,
                    "first_run_at": inst["next_run_at"].isoformat()
                    if inst["next_run_at"]
                    else None,
                },
                scope_hash="0" * 64,
            )
        async with admin.connect() as conn:
            before = (
                await conn.execute(
                    select(
                        m.accounts.c.credits_posted_cents, m.accounts.c.debits_posted_cents
                    ).where(m.accounts.c.id == "acc_zs_chk")
                )
            ).one()
        response = await app.client.post(
            "/internal/scheduled/authorize",
            json={"event_id": "evt_real_s1"},
            headers=app.scheduler_headers(),
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == ("BLOCKED" if cancelled else "SUCCESS"), response.text
        replay = await app.client.post(
            "/internal/scheduled/authorize",
            json={"event_id": "evt_real_s1"},
            headers=app.scheduler_headers(),
        )
        assert replay.json()["status"] == response.json()["status"]
        async with admin.connect() as conn:
            after = (
                await conn.execute(
                    select(
                        m.accounts.c.credits_posted_cents, m.accounts.c.debits_posted_cents
                    ).where(m.accounts.c.id == "acc_zs_chk")
                )
            ).one()
            cursor_table = (
                m.standing_instructions if kind == "SCHEDULED_TRANSFER" else m.merchant_billing
            )
            cursor_column = (
                cursor_table.c.next_run_at
                if kind == "SCHEDULED_TRANSFER"
                else cursor_table.c.next_due_at
            )
            cursor = await conn.scalar(select(cursor_column).where(cursor_table.c.id == source_id))
            assert cursor > SI_DUE, "completed or blocked period must durably advance its cursor"
            assert after[0] == before[0]
            expected = 0 if cancelled else (100000 if kind == "SCHEDULED_TRANSFER" else 3000)
            assert after[1] - before[1] == expected
            assert (
                await conn.execute(
                    text("SELECT count(*) FROM event_executions WHERE event_id='evt_real_s1'")
                )
            ).scalar() == 1
            assert (
                await conn.execute(
                    text("SELECT sum(debits_posted_cents)=sum(credits_posted_cents) FROM accounts")
                )
            ).scalar() is True
        # The next period must be discovered and consumed, rather than repeatedly
        # scanning the stale completed period. An aborted transaction cannot move
        # either the source cursor or its money/run/event records.
        async with admin.begin() as conn:
            await conn.execute(m.clock.update().values(sim_now=cursor))
            second_id = await conn.scalar(
                select(m.event_jobs.c.id).where(
                    m.event_jobs.c.kind == kind,
                    m.event_jobs.c.source_id == source_id,
                    m.event_jobs.c.due_at == cursor,
                )
            )
            if second_id is None:
                second_id = "evt_real_s1_second"
                await _seed_event(
                    conn,
                    event_id=second_id,
                    kind=kind,
                    source_id=source_id,
                    due_at=cursor,
                    payload_hash="c" * 64,
                )
        from app.events import EventService

        from tests.obligations_data.test_events_api import _clock_and_version

        with pytest.raises(RuntimeError, match="simulated commit failure"):
            async with bank_db.begin() as conn:
                clock_version, customer_version = await _clock_and_version(conn, "cus_zs")
                event_hash = await conn.scalar(
                    select(m.event_jobs.c.payload_hash).where(m.event_jobs.c.id == second_id)
                )
                await EventService().execute_event(
                    conn,
                    second_id,
                    {"cus_zs": customer_version},
                    clock_version,
                    event_hash,
                    {"by": "test"},
                )
                raise RuntimeError("simulated commit failure")
        async with admin.connect() as conn:
            assert (
                await conn.scalar(select(cursor_column).where(cursor_table.c.id == source_id))
                == cursor
            )
            assert (
                await conn.scalar(
                    select(m.accounts.c.debits_posted_cents).where(m.accounts.c.id == "acc_zs_chk")
                )
                == after[1]
            )
            assert (
                await conn.scalar(
                    select(m.event_executions.c.event_id).where(
                        m.event_executions.c.event_id == second_id
                    )
                )
                is None
            )
        second = await app.client.post(
            "/internal/scheduled/authorize",
            json={"event_id": second_id},
            headers=app.scheduler_headers(),
        )
        assert second.json()["status"] == ("BLOCKED" if cancelled else "SUCCESS"), second.text
        async with admin.connect() as conn:
            assert (
                await conn.scalar(select(cursor_column).where(cursor_table.c.id == source_id))
                > cursor
            )
            assert (
                await conn.scalar(
                    select(m.accounts.c.debits_posted_cents).where(m.accounts.c.id == "acc_zs_chk")
                )
                == before[1] + 2 * expected
            )
            assert await conn.scalar(select(text("count(*)")).select_from(m.instruction_runs)) == 2
        await gateway.dispose()
        await bank_db.dispose()
        await admin.dispose()

    asyncio.run(run())


def test_real_created_schedule_backfills_from_committed_source(obligations_db, app_config):  # noqa: F811
    """Real prepare/execute public result has no internal InsertInstruction effect."""
    from app.execution import ExecutionService
    from safebank_contracts.plan import InteractiveEvidence

    from tests.obligations_data.test_standing_exec import _commands, _schedule_step, _steps

    async def run():
        await _setup(obligations_db)
        bank_db = _runtime_conn(obligations_db)
        svc = ExecutionService(bank_db, KEY)
        prepare, execute = _commands(
            customer_id="cus_zs",
            plan_id="pln_real_source",
            steps=_steps(_schedule_step()),
            expected_versions={"cus_zs": 0},
        )
        evidence = InteractiveEvidence.model_validate(
            {
                **prepare.authorization_evidence.model_dump(mode="json"),
                "risk": "YELLOW",
                "approval_id": "apr_fixture",
                "confirmed_view_hash": prepare.plan_hash,
                "confirmed_at_wall": "2026-10-30T00:00:00+08:00",
            }
        )
        prepare = prepare.model_copy(update={"authorization_evidence": evidence})
        execute = execute.model_copy(update={"authorization_evidence": evidence})
        await svc.prepare(prepare)
        result = await svc.execute(execute)
        assert result.status == "EXECUTED"
        assert not result.steps[0].effects
        instruction_id = result.steps[0].created_ids["instruction_id"]
        admin = _admin_conn(obligations_db)
        async with admin.begin() as conn:
            await conn.execute(m.clock.update().values(sim_now=SI_DUE))
            event_id = (
                await conn.execute(
                    select(m.event_jobs.c.id).where(m.event_jobs.c.source_id == instruction_id)
                )
            ).scalar_one()
        app = await build_app(app_config)
        real = create_app(
            db=bank_db,
            service_token="test-gateway-service-token-32b!",
            fingerprint_key=KEY,
            demo_mode=False,
            scheduler_enabled=False,
        )
        app.l3._client._transport = httpx.ASGITransport(app=real)
        response = await app.client.post(
            "/internal/scheduled/authorize",
            json={"event_id": event_id},
            headers=app.scheduler_headers(),
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "SUCCESS", response.text
        async with admin.connect() as conn:
            assert (
                await conn.execute(
                    select(m.instruction_runs.c.status).where(
                        m.instruction_runs.c.instruction_id == instruction_id
                    )
                )
            ).scalar_one() == "SUCCESS"
        await admin.dispose()
        await bank_db.dispose()

    asyncio.run(run())
