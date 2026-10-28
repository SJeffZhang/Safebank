"""Real PostgreSQL + HTTP acceptance for obligation storage and the unique loader."""

import asyncio
import hashlib
import socket
import threading
from copy import deepcopy
from datetime import time, timedelta
from pathlib import Path

import httpx
import pytest
import uvicorn
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from app import models as m
from app.db import engine, ready
from app.main import create_app
from app.state import fingerprint, load_bank_state, resolve_scope
from safebank_bankcore import projected_shortfall
from safebank_bankcore.banking import Context, apply_transfer
from safebank_contracts.enums import Risk
from safebank_contracts.obligations import InstructionRun, ScheduledInstruction
from safebank_contracts.plan import (
    Execute,
    InteractiveEvidence,
    PlanBody,
    Prepare,
    SnapshotRequest,
)
from safebank_contracts.state import BankState
from safebank_contracts.tools import Transfer
from sqlalchemy import ForeignKeyConstraint, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from tests.contracts.samples import step
from tests.obligations_data.fixtures import KEY, NOW, install_core, install_obligations

ROOT = Path(__file__).resolve().parents[2]

SERVICE_TOKEN = "obligations-test-token"


@pytest.fixture(autouse=True)
def clean_database(obligations_db):
    """Truncate every application table before each test for an isolated dataset."""

    async def _truncate():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        async with admin.begin() as conn:
            names = ",".join(m.metadata.tables)
            await conn.execute(text(f"TRUNCATE {names} CASCADE"))
        await admin.dispose()

    asyncio.run(_truncate())
    yield


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _transfer_steps(amount_cents=80000):
    return PlanBody(
        intents=(),
        steps=[
            step(
                from_account_id="acc_zs_chk",
                payee_id="pye_zs_ww",
                to_account_id=None,
                amount_cents=amount_cents,
            )
        ],
        rationale="test",
    ).steps


def test_migration_and_runtime_roles(obligations_db):
    async def check():
        db = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with db.connect() as c:
            assert (
                await c.scalar(text("SELECT version_num FROM alembic_version"))
                == "0006_risk_assessments"
            )
            owner = await c.scalar(
                text("SELECT tableowner FROM pg_tables WHERE tablename='standing_instructions'")
            )
            assert owner == "postgres"
            for statement in (
                "CREATE TABLE illegal_obligations(id integer)",
                "TRUNCATE instruction_runs",
                "DELETE FROM instruction_runs",
            ):
                with pytest.raises(DBAPIError):
                    async with c.begin_nested():
                        await c.execute(text(statement))
        await db.dispose()

    asyncio.run(check())


def test_readiness_requires_clock_and_0003(obligations_db):
    async def check():
        db = engine(obligations_db.runtime_url, poolclass=NullPool)
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        assert not await ready(db)
        async with admin.begin() as c:
            await c.execute(
                text(
                    "INSERT INTO clock VALUES(1,now(),1,'obligations_acceptance') "
                    "ON CONFLICT(id) DO NOTHING"
                )
            )
        assert await ready(db)
        await db.dispose()
        await admin.dispose()

    asyncio.run(check())


def test_migrated_database_matches_metadata(obligations_db):
    async def check():
        db = engine(obligations_db.admin_url, poolclass=NullPool)
        async with db.connect() as conn:
            metadata = deepcopy(m.metadata)
            accounts = metadata.tables["accounts"]
            accounts.append_constraint(
                ForeignKeyConstraint(
                    ["current_card_id"],
                    ["cards.id"],
                    name="accounts_current_card_fk",
                    deferrable=True,
                    initially="DEFERRED",
                )
            )

            def run_sync(sync_conn):
                ctx = MigrationContext.configure(sync_conn, opts={"compare_type": True})
                return compare_metadata(ctx, metadata)

            diffs = await conn.run_sync(run_sync)
        await db.dispose()
        assert diffs == []

    asyncio.run(check())


def test_historical_migration_bytes_unchanged():
    expected = {
        "services/bank-backend/migrations/versions/0001_core.py": (
            "427a39cc00ffba19984d2e43d1c6ce45d0198da9e247eeff4980e5ffb9ef559c"
        ),
        "services/bank-backend/migrations/versions/0002_identity.py": (
            "12678a5f7078969e8b5a06c18a9bfde2b32933ebeba20db5c44223e2d256546d"
        ),
        "services/bank-backend/migrations/schema_v1.json": (
            "e49b26f72d583ba7579f2b4045311291e973f45f76f3cd5da36be378a352898f"
        ),
    }
    for rel, digest in expected.items():
        assert hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == digest


def _serve(app, port):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server


def _green_evidence(plan_hash: str) -> InteractiveEvidence:
    return InteractiveEvidence(
        type="INTERACTIVE",
        origin="agent",
        scope_ids=("utt_1",),
        utterance_ids=("utt_1",),
        evaluation_id="evl_1",
        approved_evaluation_id=None,
        plan_hash=plan_hash,
        approval_id=None,
        confirmed_view_hash=None,
        final_view_hash=plan_hash,
        risk=Risk.GREEN,
        confirmed_at_wall=None,
        authorization_epoch=0,
    )


def test_real_http_snapshot_feeds_s2_projection(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)

        steps = _transfer_steps(300000)
        plan_hash = "hash_prd_http"
        evidence = _green_evidence(plan_hash)
        deadline = "2099-01-01T00:00:00Z"
        prepare_body = Prepare(
            customer_id="cus_zs",
            plan_id="pln_prd_http",
            attempt=1,
            plan_hash=plan_hash,
            steps=steps,
            deadline_wall=deadline,
            deadline_cap_wall=deadline,
            authorization_evidence=evidence,
        )
        execute_body = Execute(
            customer_id="cus_zs",
            plan_id="pln_prd_http",
            attempt=1,
            plan_hash=plan_hash,
            steps=steps,
            expected_versions={"cus_zs": 0, "cus_ww": 0},
            clock_version=1,
            authorization_evidence=evidence,
        )

        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        headers = {"X-Service-Token": SERVICE_TOKEN}
        snapshot = SnapshotRequest(customer_id="cus_zs", steps=steps, event_id=None)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            for _ in range(40):
                try:
                    prepared = await client.post(
                        "/internal/plans/prepare",
                        json=prepare_body.model_dump(mode="json"),
                        headers=headers,
                        timeout=2.0,
                    )
                    break
                except httpx.TransportError:
                    await asyncio.sleep(0.2)
            else:
                raise RuntimeError("uvicorn listener never became ready")
            assert prepared.status_code == 200, prepared.text
            assert prepared.json()["status"] == "PREPARED"
            executed = await client.post(
                "/internal/plans/execute",
                json=execute_body.model_dump(mode="json"),
                headers=headers,
                timeout=10.0,
            )
            assert executed.status_code == 200, executed.text
            assert executed.json()["status"] == "EXECUTED"
            response = await client.post(
                "/internal/snapshot",
                json=snapshot.model_dump(mode="json"),
                headers=headers,
                timeout=10.0,
            )
        assert response.status_code == 200, response.text
        state = BankState.model_validate_json(response.text)
        assert state.accounts["acc_zs_chk"].available_cents == 300000
        assert set(state.obligations.covered_customer_ids) == {"cus_zs", "cus_ww"}
        assert set(state.obligations.standing_instructions) == {
            "si_zs_mom",
            "si_zs_video",
            "si_zs_music",
            "si_zs_cloud",
            "si_zs_gym",
        }
        assert "si_ls_video" not in state.obligations.standing_instructions
        # The real execute path bumped each scoped customer exactly once.
        assert state.versions == {"cus_zs": 1, "cus_ww": 1}
        report = projected_shortfall(state)
        assert report.accounts["acc_zs_chk"].reserve_cents == 4500
        assert report.accounts["acc_zs_chk"].max_outflow_cents == 295500
        assert report.total_reserve_cents == 4500

        async with admin.connect() as conn:
            status = await conn.scalar(
                text(
                    "SELECT status FROM execution_intents "
                    "WHERE customer_id='cus_zs' AND plan_id='pln_prd_http'"
                )
            )
            assert status == "COMMITTED"
            assert (
                await conn.scalar(
                    text(
                        "SELECT count(*) FROM plan_executions "
                        "WHERE customer_id='cus_zs' AND plan_id='pln_prd_http'"
                    )
                )
                == 1
            )
            assert (
                await conn.scalar(
                    text(
                        "SELECT count(*) FROM operation_events "
                        "WHERE customer_id='cus_zs' AND plan_id='pln_prd_http'"
                    )
                )
                >= 1
            )
            assert (await conn.scalar(text("SELECT count(*) FROM outbox"))) >= 1

        for amount, expected_shortfall in [(295500, 0), (295501, 1)]:
            candidate, _, errors = apply_transfer(
                state,
                Transfer(
                    from_account_id="acc_zs_chk",
                    payee_id="pye_zs_ww",
                    to_account_id=None,
                    amount_cents=amount,
                    memo=None,
                ),
                NOW,
                Context("cus_zs", "pln_candidate", "s2"),
            )
            assert errors == ()
            assert projected_shortfall(candidate).total_shortfall_cents == expected_shortfall

        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_empty_scope_returns_complete_empty_snapshot(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
        steps = _transfer_steps()
        async with runtime.connect() as conn:
            await conn.execution_options(isolation_level="REPEATABLE READ")
            async with conn.begin():
                await conn.execute(text("SET TRANSACTION READ ONLY"))
                scope = await resolve_scope(conn, "cus_zs", steps, KEY)
                state = await load_bank_state(conn, scope, NOW, customer_id="cus_zs")
                assert state.obligations is not None
                assert set(state.obligations.covered_customer_ids) == {"cus_zs", "cus_ww"}
                assert state.obligations.billings == {}
                assert state.obligations.standing_instructions == {}
                assert projected_shortfall(state).total_reserve_cents == 0
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


@pytest.mark.parametrize("final_status", ["DELETED", "DISABLED"])
def test_run_f1_matrix_rejects_unlinked_success_and_keeps_history(obligations_db, final_status):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)

        async with admin.begin() as conn:
            await conn.execute(
                m.transfers.insert().values(
                    id="trf_zs_video_1",
                    debit_account_id="acc_zs_chk",
                    credit_account_id="acc_external_clearing",
                    amount_cents=3000,
                    code="MANDATE",
                    phase="POSTED",
                    pending_id=None,
                    plan_id="pln_seed",
                    step_id="s1",
                    leg="main",
                    occurred_at=NOW - timedelta(days=30),
                    expires_at=None,
                )
            )

        blocked = InstructionRun(
            id="run_zs_blocked",
            customer_id="cus_zs",
            account_id="acc_zs_chk",
            kind="MERCHANT_MANDATE",
            instruction_id=None,
            billing_id="bill_zs_video",
            due_at=NOW - timedelta(days=1),
            event_id="evt_zs_blocked",
            status="BLOCKED",
            code="MANDATE_NOT_ACTIVE",
            amount_cents=3000,
            transfer_id=None,
        )
        success = InstructionRun(
            id="run_zs_success",
            customer_id="cus_zs",
            account_id="acc_zs_chk",
            kind="MERCHANT_MANDATE",
            instruction_id="si_zs_video",
            billing_id="bill_zs_video",
            due_at=NOW - timedelta(days=30),
            event_id="evt_zs_success",
            status="SUCCESS",
            code=None,
            amount_cents=3000,
            transfer_id="trf_zs_video_1",
        )
        unlinked = dict(
            id="run_zs_unlinked_success",
            customer_id="cus_zs",
            account_id="acc_zs_chk",
            kind="MERCHANT_MANDATE",
            instruction_id=None,
            billing_id="bill_zs_video",
            due_at=NOW - timedelta(days=29),
            event_id="evt_zs_unlinked",
            status="SUCCESS",
            code=None,
            amount_cents=3000,
            transfer_id="trf_zs_video_1",
        )
        async with admin.begin() as conn:
            await conn.execute(
                m.instruction_runs.insert().values(**blocked.model_dump(mode="python"))
            )
            with pytest.raises(DBAPIError):
                async with conn.begin_nested():
                    await conn.execute(m.instruction_runs.insert().values(**unlinked))
            await conn.execute(
                m.instruction_runs.insert().values(**success.model_dump(mode="python"))
            )

        async with admin.begin() as conn:
            await conn.execute(
                m.standing_instructions.update()
                .where(m.standing_instructions.c.id == "si_zs_video")
                .values(status=final_status)
            )

        async with runtime.connect() as conn:
            await conn.execution_options(isolation_level="REPEATABLE READ")
            async with conn.begin():
                await conn.execute(text("SET TRANSACTION READ ONLY"))
                scope = await resolve_scope(conn, "cus_zs", _transfer_steps(), KEY)
                state = await load_bank_state(conn, scope, NOW, customer_id="cus_zs")
                runs = {r.id: r for r in state.obligations.runs}
                assert runs["run_zs_success"].instruction_id == "si_zs_video"
                assert runs["run_zs_blocked"].instruction_id is None
                assert state.obligations.standing_instructions["si_zs_video"].status == final_status
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_runs_append_only_and_bank_ro_read_only(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        ro = engine(obligations_db.ro_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)
            blocked = dict(
                id="run_x",
                customer_id="cus_zs",
                account_id="acc_zs_chk",
                kind="MERCHANT_MANDATE",
                instruction_id=None,
                billing_id="bill_zs_video",
                due_at=NOW - timedelta(days=1),
                event_id="evt_x",
                status="BLOCKED",
                code="MANDATE_NOT_ACTIVE",
                amount_cents=3000,
                transfer_id=None,
            )
            await conn.execute(m.instruction_runs.insert().values(**blocked))
            # Append-only trigger rejects UPDATE even for the table owner; isolate it in a
            # savepoint so the outer transaction still commits the run_x INSERT.
            with pytest.raises(DBAPIError) as update_err:
                async with conn.begin_nested():
                    await conn.execute(
                        text("UPDATE instruction_runs SET code='x' WHERE id='run_x'")
                    )
            assert getattr(update_err.value.orig, "sqlstate", None) == "23514"

        # The run_x row survived and was not mutated by the rejected owner UPDATE.
        async with admin.connect() as conn:
            assert (
                await conn.scalar(text("SELECT count(*) FROM instruction_runs WHERE id='run_x'"))
                == 1
            )
            assert (
                await conn.scalar(text("SELECT code FROM instruction_runs WHERE id='run_x'"))
                == "MANDATE_NOT_ACTIVE"
            )

        async with ro.connect() as conn:
            with pytest.raises(DBAPIError) as insert_err:
                async with conn.begin_nested():
                    await conn.execute(text("INSERT INTO instruction_runs (id) VALUES ('run_ro')"))
            assert getattr(insert_err.value.orig, "sqlstate", None) == "42501"
            with pytest.raises(DBAPIError) as ro_update_err:
                async with conn.begin_nested():
                    await conn.execute(
                        text("UPDATE instruction_runs SET code='x' WHERE id='run_x'")
                    )
            assert getattr(ro_update_err.value.orig, "sqlstate", None) == "42501"
            # bank_ro keeps read-only access.
            assert (
                await conn.scalar(text("SELECT count(*) FROM instruction_runs WHERE id='run_x'"))
                == 1
            )
        await admin.dispose()
        await ro.dispose()

    asyncio.run(check())


def test_ownership_other_target_does_not_recurse_third_customer(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)
            sb_no = "6222020000000005"
            await conn.execute(
                m.payees.insert().values(
                    id="pye_ww_sb",
                    customer_id="cus_ww",
                    name="哨兵收款人",
                    alias="哨兵别名",
                    phone=None,
                    bank_name="本行",
                    account_no=sb_no,
                    target_account_id="acc_sb_chk",
                    target_fingerprint=fingerprint(sb_no, KEY),
                    created_at=NOW,
                )
            )
            own = ScheduledInstruction(
                id="si_zs_own",
                customer_id="cus_zs",
                status="ACTIVE",
                from_account_id="acc_zs_chk",
                payee_id=None,
                to_account_id="acc_zs_sav",
                amount_cents=1000,
                period="ONCE",
                anchor_day=None,
                weekday=None,
                time_of_day=time(6, 0),
                next_run_at="2026-10-18T06:00:00+08:00",
                end_at=None,
                priority="HIGH",
                grant_scope_hash="0" * 64,
                source_plan_id="pln_seed",
                created_at=NOW,
            )
            other = ScheduledInstruction(
                id="si_ww_sb",
                customer_id="cus_ww",
                status="ACTIVE",
                from_account_id="acc_ww_chk",
                payee_id="pye_ww_sb",
                to_account_id=None,
                amount_cents=5000,
                period="MONTHLY",
                anchor_day=20,
                weekday=None,
                time_of_day=time(6, 0),
                next_run_at="2026-10-20T06:00:00+08:00",
                end_at=None,
                priority="HIGH",
                grant_scope_hash="0" * 64,
                source_plan_id="pln_seed",
                created_at=NOW,
            )
            await conn.execute(
                m.standing_instructions.insert().values(**own.model_dump(mode="python"))
            )
            await conn.execute(
                m.standing_instructions.insert().values(**other.model_dump(mode="python"))
            )

        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        body = SnapshotRequest(customer_id="cus_zs", steps=_transfer_steps(), event_id=None)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            for _ in range(40):
                try:
                    response = await client.post(
                        "/internal/snapshot",
                        json=body.model_dump(mode="json"),
                        headers={"X-Service-Token": SERVICE_TOKEN},
                        timeout=2.0,
                    )
                    break
                except httpx.TransportError:
                    await asyncio.sleep(0.2)
            else:
                raise RuntimeError("uvicorn listener never became ready")
        assert response.status_code == 200, response.text
        state = BankState.model_validate_json(response.text)
        assert set(state.obligations.covered_customer_ids) == {"cus_zs", "cus_ww"}
        assert "cus_sb" not in state.customers
        assert "acc_sb_chk" not in state.accounts
        assert "cus_sb" not in state.versions
        assert "13900009999" not in response.text
        assert "哨兵收款人" not in response.text
        assert "哨兵别名" not in response.text

        target = state.obligations.instruction_targets["si_ww_sb"]
        assert target.ownership == "OTHER"
        assert target.target_account_id == "acc_sb_chk"
        assert target.target_customer_id == "cus_sb"
        own_target = state.obligations.instruction_targets["si_zs_own"]
        assert own_target.ownership == "OWN"
        assert own_target.target_account_id == "acc_zs_sav"

        report = projected_shortfall(state)
        legs = {
            (o.id, o.account_id, o.delta_cents) for o in report.obligations if o.id == "si_zs_own"
        }
        assert ("si_zs_own", "acc_zs_chk", -1000) in legs
        assert ("si_zs_own", "acc_zs_sav", 1000) in legs
        assert report.accounts["acc_zs_sav"].reserve_cents == 0
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_repeatable_read_snapshot_isolation(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)

        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        body = SnapshotRequest(customer_id="cus_zs", steps=_transfer_steps(), event_id=None)

        async with runtime.connect() as conn:
            await conn.execution_options(isolation_level="REPEATABLE READ")
            async with conn.begin():
                await conn.execute(text("SET TRANSACTION READ ONLY"))
                scope = await resolve_scope(conn, "cus_zs", _transfer_steps(), KEY)
                before = await load_bank_state(conn, scope, NOW, customer_id="cus_zs")
                assert before.obligations.billings["bill_zs_video"].amount_cents == 3000
                assert before.versions["cus_zs"] == 0

                async with admin.begin() as writer:
                    await writer.execute(
                        m.merchant_billing.update()
                        .where(m.merchant_billing.c.id == "bill_zs_video")
                        .values(amount_cents=4000)
                    )
                    await writer.execute(
                        m.state_versions.update()
                        .where(m.state_versions.c.customer_id == "cus_zs")
                        .values(version=1)
                    )

                after = await load_bank_state(conn, scope, NOW, customer_id="cus_zs")
                assert after.obligations.billings["bill_zs_video"].amount_cents == 3000
                assert after.versions["cus_zs"] == 0
                with pytest.raises(DBAPIError):
                    async with conn.begin_nested():
                        await conn.execute(text("UPDATE state_versions SET version=9"))

        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            for _ in range(40):
                try:
                    response = await client.post(
                        "/internal/snapshot",
                        json=body.model_dump(mode="json"),
                        headers={"X-Service-Token": SERVICE_TOKEN},
                        timeout=2.0,
                    )
                    break
                except httpx.TransportError:
                    await asyncio.sleep(0.2)
            else:
                raise RuntimeError("uvicorn listener never became ready")
        assert response.status_code == 200, response.text
        state = BankState.model_validate_json(response.text)
        assert state.obligations.billings["bill_zs_video"].amount_cents == 4000
        assert state.versions["cus_zs"] == 1
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())
