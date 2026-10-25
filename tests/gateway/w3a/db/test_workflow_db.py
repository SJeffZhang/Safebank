"""Real gateway PG → workflow anti-retry windows and cooldown persistence."""

import asyncio
from datetime import UTC, datetime, timedelta

from safebank_bankcore.references import request_digest
from safebank_contracts.codes import Code
from safebank_contracts.plan import PlanDraft
from sqlalchemy import insert, select
from sqlalchemy.pool import NullPool

from services.gateway.app import tables as t
from services.gateway.app.db import engine as gateway_engine
from services.gateway.app.pipeline.entity import EntityStage
from services.gateway.app.pipeline.workflow import WorkflowHistory, WorkflowStage
from services.gateway.app.schemas.enums import FindingEffect
from services.gateway.app.workflow.sequence import CommittedOp
from tests.bankcore.fixtures import NOW
from tests.gateway.w3a.conftest import build_ctx, transfer, w3_state

WALL = datetime(2026, 10, 15, 9, 0, tzinfo=UTC)


def plan(steps):
    return PlanDraft.model_validate(
        {
            "request_id": "req_w3",
            "session_id": "sid_w3",
            "utterance_ids": ["utt_1"],
            "intents": [],
            "rationale": "",
            "steps": steps,
        }
    )


async def _insert_session(conn, sid="sid_w3", customer_id="cus_a"):
    await conn.execute(
        insert(t.sessions).values(id=sid, customer_id=customer_id, created_at_wall=WALL)
    )


async def _insert_executed_plan(
    conn,
    *,
    plan_id,
    steps_hash,
    created_wall,
    executed_wall,
    customer_id="cus_a",
    sid="sid_w3",
    status="EXECUTED",
    job_status="EXECUTED",
):
    """Insert a plan + (optionally) its execution job.

    ``created_wall`` is the plan/job creation (G8 start) time; ``executed_wall``
    is the terminal archive time (``execution_jobs.updated_at_wall``). They are
    independent so tests can cover slow executions and mismatched states.
    """
    await conn.execute(
        insert(t.plans).values(
            id=plan_id,
            customer_id=customer_id,
            sid=sid,
            origin="agent",
            request_id="req_" + plan_id,
            request_hash="0" * 64,
            plan_hash="0" * 64,
            steps_hash=steps_hash,
            resolved_steps=[],
            status=status,
            created_at_wall=created_wall,
        )
    )
    if status == "EXECUTED":
        await conn.execute(
            insert(t.execution_jobs).values(
                plan_id=plan_id,
                status=job_status,
                l3_attempt=1,
                created_at_wall=created_wall,
                updated_at_wall=executed_wall,
            )
        )


def _ctx(conn, steps, *, wall, plan_id="pln_w3"):
    ctx = build_ctx(steps, plan_id=plan_id)
    ctx.conn = conn
    ctx.wall_now = lambda: wall
    return ctx


def test_duplicate_plan_anchors_on_terminal_time_wall(gateway_db):
    async def run():
        engine = gateway_engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            steps = [transfer("pye_ext", 100000)]
            steps_hash = request_digest(plan(steps).steps)
            async with engine.begin() as conn:
                await _insert_session(conn)
                # Slow execution: started 30 minutes ago, terminal archive 2
                # minutes ago. The window must anchor on the terminal time.
                await _insert_executed_plan(
                    conn,
                    plan_id="pln_old",
                    steps_hash=steps_hash,
                    created_wall=WALL - timedelta(minutes=30),
                    executed_wall=WALL - timedelta(minutes=2),
                )

            # Within the 10-minute wall window anchored on the terminal time.
            async with engine.connect() as conn:
                ctx = _ctx(conn, steps, wall=WALL)
                await EntityStage().run(ctx)
                await WorkflowStage().run(ctx)
            assert any(
                f.code is Code.DUPLICATE_PLAN and f.effect is FindingEffect.RISK_YELLOW
                for f in ctx.findings
            )

            # Advancing the simulated clock does not expire the wall window.
            async with engine.connect() as conn:
                sim_ctx = _ctx(conn, steps, wall=WALL)
                base = w3_state()
                sim_ctx._lazy_state = base.model_copy(
                    update={"sim_now": base.sim_now + timedelta(days=3)}
                )
                await EntityStage().run(sim_ctx)
                await WorkflowStage().run(sim_ctx)
            assert any(f.code is Code.DUPLICATE_PLAN for f in sim_ctx.findings)

            # Advancing the wall clock past 10 minutes expires the window.
            async with engine.connect() as conn:
                later_ctx = _ctx(conn, steps, wall=WALL + timedelta(minutes=11))
                await EntityStage().run(later_ctx)
                await WorkflowStage().run(later_ctx)
            assert not any(f.code is Code.DUPLICATE_PLAN for f in later_ctx.findings)
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_duplicate_plan_excludes_self_and_boundaries(gateway_db):
    async def run():
        engine = gateway_engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            # Distinct amount keeps this test's steps_hash away from the other
            # tests that share the session-scoped gateway database.
            steps = [transfer("pye_ext", 200000)]
            steps_hash = request_digest(plan(steps).steps)
            async with engine.begin() as conn:
                await _insert_session(conn, sid="sid_self")
                await _insert_session(conn, sid="sid_other", customer_id="cus_b")
                # Self replay: same plan_id must not self-trigger.
                await _insert_executed_plan(
                    conn,
                    plan_id="pln_w3",
                    steps_hash=steps_hash,
                    created_wall=WALL - timedelta(minutes=30),
                    executed_wall=WALL - timedelta(minutes=2),
                    sid="sid_self",
                )
                # Cross-customer execution.
                await _insert_executed_plan(
                    conn,
                    plan_id="pln_other_customer",
                    steps_hash=steps_hash,
                    created_wall=WALL - timedelta(minutes=30),
                    executed_wall=WALL - timedelta(minutes=2),
                    customer_id="cus_b",
                    sid="sid_other",
                )
                # Invalid (non-executed) plan status.
                await _insert_executed_plan(
                    conn,
                    plan_id="pln_not_executed",
                    steps_hash=steps_hash,
                    created_wall=WALL - timedelta(minutes=30),
                    executed_wall=WALL - timedelta(minutes=2),
                    status="DENIED",
                    sid="sid_self",
                )
                # Plan EXECUTED but job still UNKNOWN: status mismatch must not hit.
                await _insert_executed_plan(
                    conn,
                    plan_id="pln_job_mismatch",
                    steps_hash=steps_hash,
                    created_wall=WALL - timedelta(minutes=30),
                    executed_wall=WALL - timedelta(minutes=2),
                    status="EXECUTED",
                    job_status="UNKNOWN",
                    sid="sid_self",
                )
                # Recent start but stale terminal time: must not hit (the window
                # keys on the terminal archive, not the G8 start).
                await _insert_executed_plan(
                    conn,
                    plan_id="pln_stale_terminal",
                    steps_hash=steps_hash,
                    created_wall=WALL - timedelta(minutes=2),
                    executed_wall=WALL - timedelta(minutes=30),
                    sid="sid_self",
                )
                # Expired terminal (11 minutes ago).
                await _insert_executed_plan(
                    conn,
                    plan_id="pln_expired",
                    steps_hash=steps_hash,
                    created_wall=WALL - timedelta(hours=2),
                    executed_wall=WALL - timedelta(minutes=11),
                    sid="sid_self",
                )
                # Future terminal timestamp is outside the wall window.
                await _insert_executed_plan(
                    conn,
                    plan_id="pln_future",
                    steps_hash=steps_hash,
                    created_wall=WALL - timedelta(hours=2),
                    executed_wall=WALL + timedelta(minutes=5),
                    sid="sid_self",
                )

            async with engine.connect() as conn:
                ctx = _ctx(conn, steps, wall=WALL, plan_id="pln_w3")
                await EntityStage().run(ctx)
                await WorkflowStage().run(ctx)
            assert not any(f.code is Code.DUPLICATE_PLAN for f in ctx.findings)
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_rejected_retry_uses_wall_window(gateway_db):
    async def run():
        engine = gateway_engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            async with engine.begin() as conn:
                await _insert_session(conn, sid="sid_rej")
                await conn.execute(
                    insert(t.plans).values(
                        id="pln_rejected",
                        customer_id="cus_a",
                        sid="sid_rej",
                        origin="agent",
                        request_id="req_rejected",
                        request_hash="0" * 64,
                        plan_hash="0" * 64,
                        steps_hash="0" * 64,
                        resolved_steps=[
                            {
                                "step_id": "s1",
                                "tool": "transfer",
                                "args": {
                                    "from_account_id": "acc_a",
                                    "payee_id": "pye_ext",
                                    "to_account_id": None,
                                    "amount_cents": 100000,
                                    "memo": None,
                                },
                                "depends_on": [],
                            }
                        ],
                        status="DENIED",
                        created_at_wall=WALL - timedelta(minutes=3),
                    )
                )

            steps = [transfer("pye_ext", 100000)]
            async with engine.connect() as conn:
                ctx = _ctx(conn, steps, wall=WALL)
                await EntityStage().run(ctx)
                await WorkflowStage().run(ctx)
            assert any(
                f.code is Code.REJECTED_RETRY and f.effect is FindingEffect.RISK_RED
                for f in ctx.findings
            )
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_risky_sequence_cooldown_is_persisted(gateway_db):
    async def run():
        engine = gateway_engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            history = WorkflowHistory(
                committed=(
                    CommittedOp(
                        "e1", "cus_a", "PAYEE_ADD", "pye_extfp", 0, NOW - timedelta(hours=2)
                    ),
                    CommittedOp(
                        "e2", "cus_a", "CARD_LIMIT_LOOSEN", None, 0, NOW - timedelta(hours=1)
                    ),
                )
            )

            async def load(_ctx):
                return history

            async with engine.begin() as conn:
                await _insert_session(conn, sid="sid_cool")
                ctx = _ctx(conn, [transfer("pye_ext", 100000)], wall=WALL)
                stage = WorkflowStage(load_history=load)
                await EntityStage().run(ctx)
                await stage.run(ctx)
            assert any(f.code is Code.RISKY_SEQUENCE for f in ctx.findings)

            # B1: cooldowns are collected in-memory; DB write happens in G4 handler
            assert ctx.workflow is not None
            assert len(ctx.workflow.pending_cooldowns) == 1
            fingerprint, until_iso = ctx.workflow.pending_cooldowns[0]
            assert fingerprint == "pye_extfp"
            assert until_iso.endswith("+08:00") or "Z" in until_iso
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_terminal_cas_persists_updated_at_wall_and_rejects_retry(gateway_db):
    """The existing terminal CAS is the trusted wall source for DUPLICATE_PLAN.

    ``state.archive_execution_terminal`` is the last ``_job_update`` for an
    EXECUTED job (EXECUTED has no outgoing edges), so ``updated_at_wall`` is the
    terminal observed wall time. A retry after the terminal state is rejected and
    must not refresh that timestamp.
    """
    import pytest
    from safebank_contracts.enums import Risk
    from safebank_contracts.plan import ExecutionResult, InteractiveEvidence, StepResult

    from services.gateway.app import state
    from services.gateway.app.errors import GatewayError
    from services.gateway.app.schemas.enums import ExecutionJobStatus

    async def run():
        engine = gateway_engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            plan_id = "pln_cas"
            evidence = InteractiveEvidence(
                type="INTERACTIVE",
                origin="agent",
                scope_ids=("utt_cas",),
                utterance_ids=("utt_cas",),
                evaluation_id="evl_cas",
                approved_evaluation_id=None,
                plan_hash="0" * 64,
                approval_id=None,
                confirmed_view_hash=None,
                final_view_hash="0" * 64,
                risk=Risk.GREEN,
                confirmed_at_wall=None,
                authorization_epoch=0,
            )
            async with engine.begin() as conn:
                await _insert_session(conn, sid="sid_cas")
                await conn.execute(
                    insert(t.plans).values(
                        id=plan_id,
                        customer_id="cus_a",
                        sid="sid_cas",
                        origin="agent",
                        request_id="req_cas",
                        request_hash="0" * 64,
                        plan_hash="0" * 64,
                        steps_hash="0" * 64,
                        resolved_steps=[],
                        scope_ids=["utt_cas"],
                        utterance_ids=["utt_cas"],
                        status="EXECUTING",
                        created_at_wall=WALL,
                    )
                )
                await conn.execute(
                    insert(t.execution_jobs).values(
                        plan_id=plan_id,
                        status="EXECUTING",
                        l3_attempt=1,
                        created_at_wall=WALL,
                        updated_at_wall=WALL,
                        deadline_wall=WALL + timedelta(minutes=10),
                        deadline_cap_wall=WALL + timedelta(minutes=10),
                        authorization_evidence=evidence.model_dump(mode="json"),
                    )
                )
                await conn.execute(
                    insert(t.evaluations).values(
                        id="evl_cas",
                        plan_id=plan_id,
                        revision=1,
                        decision={},
                        risk="GREEN",
                        policy_version="0" * 64,
                        created_at_wall=WALL,
                    )
                )
                await conn.execute(
                    insert(t.authorization_claims).values(
                        scope_id="utt_cas", plan_id=plan_id, status="CLAIMED"
                    )
                )

            result = ExecutionResult(
                plan_id=plan_id,
                status="EXECUTED",
                steps=(
                    StepResult(
                        step_id="s1",
                        tool="transfer",
                        status="EXECUTED",
                        created_ids={},
                        result={},
                        effects=(),
                    ),
                ),
                versions={"cus_a": 1},
                committed_at="2026-10-15T09:00:00+08:00",
                error_code=None,
                replayed=False,
            )
            identity = state.ExecutionIdentity(
                customer_id="cus_a", plan_id=plan_id, origin="agent", attempt=1, revision=1
            )

            async with engine.begin() as conn:
                await state.archive_execution_terminal(
                    conn,
                    expected=identity,
                    from_status=ExecutionJobStatus.EXECUTING,
                    l3_status="COMMITTED",
                    result=result,
                )

            async with engine.connect() as conn:
                job = (
                    (
                        await conn.execute(
                            select(t.execution_jobs).where(t.execution_jobs.c.plan_id == plan_id)
                        )
                    )
                    .mappings()
                    .one()
                )
            first_updated = job["updated_at_wall"]
            assert job["status"] == "EXECUTED"
            assert first_updated is not None

            # A terminal retry is rejected and leaves updated_at_wall untouched.
            async with engine.begin() as conn:
                with pytest.raises(GatewayError) as exc:
                    await state.archive_execution_terminal(
                        conn,
                        expected=identity,
                        from_status=ExecutionJobStatus.EXECUTING,
                        l3_status="COMMITTED",
                        result=result,
                    )
            assert exc.value.code is Code.STATE_CHANGED

            async with engine.connect() as conn:
                after = (
                    (
                        await conn.execute(
                            select(t.execution_jobs).where(t.execution_jobs.c.plan_id == plan_id)
                        )
                    )
                    .mappings()
                    .one()
                )
            assert after["updated_at_wall"] == first_updated
        finally:
            await engine.dispose()

    asyncio.run(run())
