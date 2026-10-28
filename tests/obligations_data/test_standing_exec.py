"""C3 standing instruction – real PostgreSQL execution acceptance tests.

Tests the full prepare → execute → persist chain for schedule_transfer,
cancel_scheduled, and cancel_subscription (revoke_mandate) tools against
a tmpfs PostgreSQL database, following the sync-function + asyncio.run(check())
convention of test_reads.py.

Fixture pattern: obligations_db (session-scoped tmpfs container) +
install_core + install_obligations, same as test_reads.py.  Each test runs
with an autouse clean_database fixture that truncates every table first.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from app.db import engine
from app.execution import ExecutionService, steps_digest
from app.state import load_bank_state, resolve_scope
from safebank_bankcore.banking import apply_plan
from safebank_contracts.enums import Risk
from safebank_contracts.plan import Execute, InteractiveEvidence, PlanBody, Prepare
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from tests.contracts.samples import step
from tests.obligations_data.fixtures import KEY, NOW, install_core, install_obligations

pytestmark = pytest.mark.integration

FIRST_RUN = "2026-11-05T06:00:00+08:00"


@pytest.fixture(autouse=True)
def clean_database(obligations_db):
    """Truncate every application table before each test for an isolated dataset."""

    async def _truncate():
        from app import models as m

        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        async with admin.begin() as conn:
            names = ",".join(m.metadata.tables)
            await conn.execute(text(f"TRUNCATE {names} CASCADE"))
        await admin.dispose()

    asyncio.run(_truncate())
    yield


# ── helpers ──────────────────────────────────────────────────────────────


def _steps(*tool_steps):
    return PlanBody(intents=(), steps=list(tool_steps), rationale="C3 standing test").steps


def _schedule_step(*, step_id="s1", amount_cents=50_000, payee_id="pye_zs_mom"):
    return step(
        tool="schedule_transfer",
        step_id=step_id,
        from_account_id="acc_zs_chk",
        payee_id=payee_id,
        to_account_id=None,
        amount_cents=amount_cents,
        period="MONTHLY",
        first_run_at=FIRST_RUN,
        end_at=None,
    )


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


def _commands(*, customer_id, plan_id, steps, expected_versions, clock_version=1, attempt=1):
    plan_hash = steps_digest(steps)
    evidence = _green_evidence(plan_hash)
    deadline = datetime.now(UTC) + timedelta(minutes=10)
    prepare = Prepare(
        customer_id=customer_id,
        plan_id=plan_id,
        attempt=attempt,
        plan_hash=plan_hash,
        steps=steps,
        deadline_wall=deadline,
        deadline_cap_wall=deadline,
        authorization_evidence=evidence,
    )
    execute = Execute(
        customer_id=customer_id,
        plan_id=plan_id,
        attempt=attempt,
        plan_hash=plan_hash,
        steps=steps,
        expected_versions=expected_versions,
        clock_version=clock_version,
        authorization_evidence=evidence,
    )
    return prepare, execute


async def _prepare_execute(
    runtime,
    *,
    customer_id="cus_zs",
    plan_id,
    steps,
    expected_versions,
    clock_version=1,
    attempt=1,
):
    prepare, execute = _commands(
        customer_id=customer_id,
        plan_id=plan_id,
        steps=steps,
        expected_versions=expected_versions,
        clock_version=clock_version,
        attempt=attempt,
    )
    svc = ExecutionService(runtime, KEY)
    await svc.prepare(prepare)
    result = await svc.execute(execute)
    return result, svc, execute


async def _install(admin):
    async with admin.begin() as conn:
        await install_core(conn)
        await install_obligations(conn)


# ── tests ────────────────────────────────────────────────────────────────


class TestScheduleTransferExecution:
    def test_schedule_transfer_creates_instruction_and_job(self, obligations_db):
        """schedule_transfer creates one instruction, one event job, one op; balance unchanged."""

        async def check():
            admin = engine(obligations_db.admin_url, poolclass=NullPool)
            runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
            await _install(admin)
            steps = _steps(_schedule_step())
            result, _, _ = await _prepare_execute(
                runtime, plan_id="pln_c3_test", steps=steps, expected_versions={"cus_zs": 0}
            )
            assert result.status == "EXECUTED"
            new_id = result.steps[0].result["instruction_id"]
            async with admin.connect() as conn:
                row = (
                    (
                        await conn.execute(
                            text("SELECT status, kind FROM standing_instructions WHERE id=:id"),
                            {"id": new_id},
                        )
                    )
                    .mappings()
                    .one()
                )
                assert row["status"] == "ACTIVE"
                assert row["kind"] == "SCHEDULED_TRANSFER"
                assert (
                    await conn.scalar(
                        text("SELECT count(*) FROM event_jobs WHERE source_id=:id"), {"id": new_id}
                    )
                    == 1
                )
                assert (
                    await conn.scalar(
                        text("SELECT count(*) FROM operation_events WHERE plan_id='pln_c3_test'")
                    )
                    == 1
                )
                assert (
                    await conn.scalar(
                        text(
                            "SELECT credits_posted_cents - debits_posted_cents "
                            "FROM accounts WHERE id='acc_zs_chk'"
                        )
                    )
                    == 600000
                )
            await admin.dispose()
            await runtime.dispose()

        asyncio.run(check())

    def test_event_job_rank_matches_kind(self, obligations_db):
        """Persisted event_jobs.rank follows the PRD §8.6 fixed per-kind rank table."""

        async def check():
            admin = engine(obligations_db.admin_url, poolclass=NullPool)
            runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
            await _install(admin)
            steps = _steps(_schedule_step())
            result, _, _ = await _prepare_execute(
                runtime, plan_id="pln_c3_rank", steps=steps, expected_versions={"cus_zs": 0}
            )
            assert result.status == "EXECUTED"
            new_id = result.steps[0].result["instruction_id"]
            async with admin.connect() as conn:
                rank = await conn.scalar(
                    text("SELECT rank FROM event_jobs WHERE source_id=:id"), {"id": new_id}
                )
                assert rank == 3  # SCHEDULED_TRANSFER
            await admin.dispose()
            await runtime.dispose()

        asyncio.run(check())

    def test_same_plan_retry_20_times_idempotent(self, obligations_db):
        """Re-executing the same plan_id 20 times produces exactly one of each row."""

        async def check():
            admin = engine(obligations_db.admin_url, poolclass=NullPool)
            runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
            await _install(admin)
            steps = _steps(_schedule_step())
            result, svc, execute = await _prepare_execute(
                runtime, plan_id="pln_retry", steps=steps, expected_versions={"cus_zs": 0}
            )
            assert result.status == "EXECUTED"
            assert result.replayed is False
            new_id = result.steps[0].result["instruction_id"]
            for _ in range(19):
                replayed = await svc.execute(execute)
                assert replayed.status == "EXECUTED"
                assert replayed.replayed is True
            async with admin.connect() as conn:
                assert (
                    await conn.scalar(
                        text("SELECT count(*) FROM standing_instructions WHERE id=:id"),
                        {"id": new_id},
                    )
                    == 1
                )
                assert (
                    await conn.scalar(
                        text("SELECT count(*) FROM event_jobs WHERE source_id=:id"), {"id": new_id}
                    )
                    == 1
                )
                assert (
                    await conn.scalar(
                        text("SELECT count(*) FROM operation_events WHERE plan_id='pln_retry'")
                    )
                    == 1
                )
            await admin.dispose()
            await runtime.dispose()

        asyncio.run(check())


class TestCancelScheduledExecution:
    def test_cancel_scheduled_marks_deleted(self, obligations_db):
        """cancel_scheduled changes ACTIVE→DELETED and emits one SCHEDULED_CANCEL op."""

        async def check():
            admin = engine(obligations_db.admin_url, poolclass=NullPool)
            runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
            await _install(admin)
            steps = _steps(step(tool="cancel_scheduled", instruction_id="si_zs_mom"))
            result, _, _ = await _prepare_execute(
                runtime, plan_id="pln_cancel", steps=steps, expected_versions={"cus_zs": 0}
            )
            assert result.status == "EXECUTED"
            async with admin.connect() as conn:
                assert (
                    await conn.scalar(
                        text("SELECT status FROM standing_instructions WHERE id='si_zs_mom'")
                    )
                    == "DELETED"
                )
                assert (
                    await conn.scalar(
                        text(
                            "SELECT count(*) FROM operation_events "
                            "WHERE plan_id='pln_cancel' AND action_kind='SCHEDULED_CANCEL'"
                        )
                    )
                    == 1
                )
            await admin.dispose()
            await runtime.dispose()

        asyncio.run(check())

    def test_cancel_scheduled_idempotent_no_op(self, obligations_db):
        """A second cancel (new plan) on a DELETED instruction is a no-op with no events."""

        async def check():
            admin = engine(obligations_db.admin_url, poolclass=NullPool)
            runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
            await _install(admin)
            steps_a = _steps(step(tool="cancel_scheduled", instruction_id="si_zs_mom"))
            r1, _, _ = await _prepare_execute(
                runtime, plan_id="pln_cancel_a", steps=steps_a, expected_versions={"cus_zs": 0}
            )
            assert r1.status == "EXECUTED"
            steps_b = _steps(step(tool="cancel_scheduled", instruction_id="si_zs_mom"))
            r2, _, _ = await _prepare_execute(
                runtime, plan_id="pln_cancel_b", steps=steps_b, expected_versions={"cus_zs": 1}
            )
            assert r2.status == "EXECUTED"
            assert r2.steps[0].result["no_op"] is True
            async with admin.connect() as conn:
                assert (
                    await conn.scalar(
                        text("SELECT status FROM standing_instructions WHERE id='si_zs_mom'")
                    )
                    == "DELETED"
                )
                assert (
                    await conn.scalar(
                        text(
                            "SELECT count(*) FROM operation_events "
                            "WHERE action_kind='SCHEDULED_CANCEL'"
                        )
                    )
                    == 1
                )
                assert (
                    await conn.scalar(
                        text("SELECT count(*) FROM operation_events WHERE plan_id='pln_cancel_b'")
                    )
                    == 0
                )
            await admin.dispose()
            await runtime.dispose()

        asyncio.run(check())


class TestRevokeMandateExecution:
    def test_revoke_mandate_changes_status(self, obligations_db):
        """cancel_subscription revokes the mandate but preserves the billing rows."""

        async def check():
            admin = engine(obligations_db.admin_url, poolclass=NullPool)
            runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
            await _install(admin)
            steps = _steps(step(tool="cancel_subscription", instruction_id="si_zs_video"))
            result, _, _ = await _prepare_execute(
                runtime, plan_id="pln_revoke", steps=steps, expected_versions={"cus_zs": 0}
            )
            assert result.status == "EXECUTED"
            assert result.steps[0].result["monthly_saving_cents"] == 3000
            assert result.steps[0].result["annual_saving_cents"] == 36000
            async with admin.connect() as conn:
                assert (
                    await conn.scalar(
                        text("SELECT status FROM standing_instructions WHERE id='si_zs_video'")
                    )
                    == "DELETED"
                )
                # Billing rows must be preserved (merchant may still issue billing events).
                assert (
                    await conn.scalar(
                        text("SELECT count(*) FROM merchant_billing WHERE id='bill_zs_video'")
                    )
                    == 1
                )
                assert (
                    await conn.scalar(
                        text(
                            "SELECT count(*) FROM operation_events "
                            "WHERE plan_id='pln_revoke' AND action_kind='MANDATE_REVOKE'"
                        )
                    )
                    == 1
                )
            await admin.dispose()
            await runtime.dispose()

        asyncio.run(check())


class TestEventJobUniqueness:
    def test_same_source_period_duplicate_job_rejected(self, obligations_db):
        """A second job for the same source_id + period_key violates the unique constraint."""

        async def check():
            admin = engine(obligations_db.admin_url, poolclass=NullPool)
            runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
            await _install(admin)
            steps = _steps(_schedule_step())
            result, _, _ = await _prepare_execute(
                runtime, plan_id="pln_job_uniq", steps=steps, expected_versions={"cus_zs": 0}
            )
            assert result.status == "EXECUTED"
            new_id = result.steps[0].result["instruction_id"]
            async with admin.connect() as conn:
                job = (
                    (
                        await conn.execute(
                            text(
                                "SELECT kind, due_at, source_id, period_key FROM event_jobs "
                                "WHERE source_id=:id"
                            ),
                            {"id": new_id},
                        )
                    )
                    .mappings()
                    .one()
                )
                with pytest.raises(DBAPIError):
                    await conn.execute(
                        text(
                            "INSERT INTO event_jobs "
                            "(id, kind, rank, due_at, immutable_payload, payload_hash, "
                            " source_id, period_key, status, attempt_count) "
                            "VALUES ('job_dup', :kind, 0, :due_at, '{}'::jsonb, 'dup-hash', "
                            "        :source_id, :period_key, 'PENDING', 0)"
                        ),
                        dict(
                            kind=job["kind"],
                            due_at=job["due_at"],
                            source_id=job["source_id"],
                            period_key=job["period_key"],
                        ),
                    )
            await admin.dispose()
            await runtime.dispose()

        asyncio.run(check())


class TestPrePostConsistency:
    def test_preflight_matches_persisted_state(self, obligations_db):
        """apply_plan preflight predicts the same instruction that real execution persists."""

        async def check():
            admin = engine(obligations_db.admin_url, poolclass=NullPool)
            runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
            await _install(admin)
            steps = _steps(_schedule_step(amount_cents=70_000))

            async def _snapshot(conn):
                await conn.execution_options(isolation_level="REPEATABLE READ")
                async with conn.begin():
                    await conn.execute(text("SET TRANSACTION READ ONLY"))
                    scope = await resolve_scope(conn, "cus_zs", steps, KEY)
                    return await load_bank_state(conn, scope, NOW, customer_id="cus_zs")

            async with runtime.connect() as conn:
                before = await _snapshot(conn)
            sim = apply_plan(before, steps, "cus_zs", "pln_preflight")
            assert sim.error is None
            predicted = sim.state.obligations.standing_instructions[
                sim.results[0].result["instruction_id"]
            ]

            result, _, _ = await _prepare_execute(
                runtime, plan_id="pln_preflight", steps=steps, expected_versions={"cus_zs": 0}
            )
            assert result.status == "EXECUTED"

            async with runtime.connect() as conn:
                after = await _snapshot(conn)
            persisted = after.obligations.standing_instructions[predicted.id]
            assert persisted.status == "ACTIVE"
            assert persisted.grant_scope_hash == predicted.grant_scope_hash
            assert persisted.next_run_at == predicted.next_run_at
            assert persisted.amount_cents == predicted.amount_cents
            assert persisted.amount_cents == 70_000

            await admin.dispose()
            await runtime.dispose()

        asyncio.run(check())
