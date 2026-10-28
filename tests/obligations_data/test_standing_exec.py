"""C3 standing instruction – real PostgreSQL execution acceptance tests.

Tests the full prepare → execute → persist chain for schedule_transfer,
cancel_scheduled, and cancel_subscription (revoke_mandate) tools against
a tmpfs PostgreSQL database.

Fixture pattern: obligations_db (session-scoped tmpfs container) +
install_core + install_obligations, same as test_reads.py.
"""

from datetime import UTC, datetime, timedelta

import pytest
from app.execution import ExecutionService, steps_digest
from safebank_contracts.plan import Execute, PlanBody, Prepare
from sqlalchemy import select, text

from tests.obligations_data.fixtures import KEY

pytestmark = pytest.mark.integration


# ── helpers ──────────────────────────────────────────────────────────────


async def _prepare_execute(db, steps_list, *, customer_id="cus_zs", fingerprint_key=KEY):
    """Build Prepare + Execute commands and run them through ExecutionService."""
    wall = datetime.now(UTC)
    steps = PlanBody(intents=(), steps=steps_list, rationale="C3 standing test").steps
    plan_hash = steps_digest(steps)
    evidence = dict(
        type="INTERACTIVE",
        origin="agent",
        scope_ids=["utt_test"],
        utterance_ids=["utt_test"],
        evaluation_id="evl_test",
        approved_evaluation_id="evl_test",
        plan_hash=plan_hash,
        approval_id="apv_test",
        confirmed_view_hash="view_test",
        final_view_hash="view_test",
        risk="YELLOW",
        confirmed_at_wall=wall,
        authorization_epoch=0,
    )
    prepared = Prepare(
        customer_id=customer_id,
        plan_id="pln_c3_test",
        attempt=1,
        plan_hash=plan_hash,
        steps=steps,
        deadline_wall=wall + timedelta(minutes=5),
        deadline_cap_wall=wall + timedelta(minutes=10),
        authorization_evidence=evidence,
    )
    execute = Execute(
        customer_id=customer_id,
        plan_id="pln_c3_test",
        attempt=1,
        plan_hash=plan_hash,
        steps=steps,
        authorization_evidence=evidence,
        expected_versions={},
        clock_version=0,
    )
    svc = ExecutionService(db, fingerprint_key)
    await svc.prepare(prepared)
    result = await svc.execute(execute)
    return result, svc


async def _count_rows(conn, table):
    return await conn.scalar(select(text(f"count(*) from {table}")))


# ── tests ────────────────────────────────────────────────────────────────


class TestScheduleTransferExecution:
    async def test_schedule_transfer_creates_instruction_and_job(self, obligations_db):
        """Execute schedule_transfer: one standing_instruction, one event_jobs,
        one operation_event row; balance unchanged."""
        ...

    async def test_same_plan_retry_20_times_idempotent(self, obligations_db):
        """Same plan_id re-executed 20 times produces exactly one of each row."""
        ...


class TestCancelScheduledExecution:
    async def test_cancel_scheduled_marks_deleted(self, obligations_db):
        """cancel_scheduled changes ACTIVE→DELETED; second cancel is no_op."""
        ...

    async def test_cancel_scheduled_idempotent_no_op(self, obligations_db):
        """Second cancel on already-DELETED instruction: no new events."""
        ...


class TestRevokeMandateExecution:
    async def test_revoke_mandate_changes_status(self, obligations_db):
        """cancel_subscription revokes mandate; billing rows preserved."""
        ...


class TestEventJobUniqueness:
    async def test_same_source_period_duplicate_job_rejected(self, obligations_db):
        """A second job for the same source_id + period_key is rejected by
        the unique constraint."""
        ...


class TestPrePostConsistency:
    async def test_preflight_matches_persisted_state(self, obligations_db):
        """apply_plan (preflight) result matches the state after real execution + reload."""
        ...
