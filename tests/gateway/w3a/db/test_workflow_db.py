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


async def _insert_session(conn, sid="sid_dup", customer_id="cus_a"):
    await conn.execute(
        insert(t.sessions).values(id=sid, customer_id=customer_id, created_at_wall=WALL)
    )


def _ctx(conn, steps, *, wall):
    ctx = build_ctx(steps, plan_id="pln_w3")
    ctx.conn = conn
    ctx.wall_now = lambda: wall
    return ctx


def test_duplicate_plan_uses_wall_not_sim(gateway_db):
    async def run():
        engine = gateway_engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            steps = [transfer("pye_ext", 100000)]
            steps_hash = request_digest(plan(steps).steps)
            async with engine.begin() as conn:
                await _insert_session(conn)
                await conn.execute(
                    insert(t.plans).values(
                        id="pln_old",
                        customer_id="cus_a",
                        sid="sid_dup",
                        origin="agent",
                        request_id="req_old",
                        request_hash="0" * 64,
                        plan_hash="0" * 64,
                        steps_hash=steps_hash,
                        resolved_steps=[],
                        status="EXECUTED",
                        created_at_wall=WALL - timedelta(minutes=5),
                    )
                )

            # Within the 10-minute wall window.
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

            async with engine.connect() as conn:
                rows = (
                    (
                        await conn.execute(
                            select(t.cooldowns).where(t.cooldowns.c.customer_id == "cus_a")
                        )
                    )
                    .mappings()
                    .all()
                )
            assert {r["payee_fingerprint"] for r in rows} == {"pye_extfp"}
            assert all(r["reason"] == "RISKY_SEQUENCE" for r in rows)
        finally:
            await engine.dispose()

    asyncio.run(run())
