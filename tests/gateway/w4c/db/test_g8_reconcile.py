"""W4c T5: UNKNOWN inline reconciliation (G8 trigger).

Red tests — reconciliation is not yet implemented, so the second G8 call
after an UNKNOWN-marked execute will either crash or return the wrong code.
"""

from __future__ import annotations

import asyncio

from app import models as m
from sqlalchemy import func, select
from sqlalchemy.pool import NullPool

from services.gateway.app import tables as t
from services.gateway.app.db import engine
from services.gateway.app.l3.client import L3OutcomeUnknown
from tests.gateway.w4c.db.support import allow_plan_ctx

EXECUTE_PATH = "/plans/{plan_id}/execute"


def test_reconcile_committed_after_lost_reply(app_config, obligations_db):
    """L3 committed but the execute reply was lost.

    First G8 → 202 UNKNOWN (execute succeeded at L3, wrapper raises
    L3OutcomeUnknown).  Second G8 → 200 EXECUTED with ``replayed=true``
    after querying L3 and finding the committed result.  L3 only records
    one execution.
    """

    async def run():
        async with allow_plan_ctx(app_config, obligations_db) as ctx:
            l3 = ctx.test_app.client._transport.app.state.l3
            real_execute = l3.execute

            # Wrap: call real execute (L3 commits), then simulate lost reply.
            async def execute_then_lost(body):
                await real_execute(body)  # L3 commits
                raise L3OutcomeUnknown("reply lost after delivery")

            l3.execute = execute_then_lost

            url = EXECUTE_PATH.format(plan_id=ctx.plan_id)

            # First G8 → 202 UNKNOWN
            resp1 = await ctx.test_app.client.post(
                url, json={"request_id": "req_w4c_rec_c_1"}, headers=ctx.headers
            )
            assert resp1.status_code == 202, resp1.text
            assert resp1.json() == {"plan_id": ctx.plan_id, "status": "UNKNOWN"}

            # L3 committed (execute was called)
            async with ctx.l3_admin.connect() as conn:
                executed = await conn.scalar(
                    select(func.count())
                    .select_from(m.plan_executions)
                    .where(m.plan_executions.c.plan_id == ctx.plan_id)
                )
            assert executed == 1, f"expected 1 execution, got {executed}"

            # Gateway job is UNKNOWN
            db = engine(ctx.db_url, poolclass=NullPool)
            async with db.connect() as conn:
                job_status = await conn.scalar(
                    select(t.execution_jobs.c.status).where(
                        t.execution_jobs.c.plan_id == ctx.plan_id
                    )
                )
            await db.dispose()
            assert job_status == "UNKNOWN"

            # Restore real execute for reconciliation
            l3.execute = real_execute

            # Second G8 (new request_id) → should reconcile and return 200
            resp2 = await ctx.test_app.client.post(
                url, json={"request_id": "req_w4c_rec_c_2"}, headers=ctx.headers
            )
            assert resp2.status_code == 200, (
                f"expected 200 after reconcile, got {resp2.status_code}: {resp2.text}"
            )
            result = resp2.json()
            assert result["status"] == "EXECUTED"
            assert result.get("replayed") is True

            # Terminal state: job EXECUTED, plan EXECUTED, claims CONSUMED
            db2 = engine(ctx.db_url, poolclass=NullPool)
            async with db2.connect() as conn:
                job = (
                    (
                        await conn.execute(
                            select(t.execution_jobs.c.status, t.execution_jobs.c.result).where(
                                t.execution_jobs.c.plan_id == ctx.plan_id
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                plan_status = await conn.scalar(
                    select(t.plans.c.status).where(t.plans.c.id == ctx.plan_id)
                )
                claims = (
                    (
                        await conn.execute(
                            select(t.authorization_claims.c.status).where(
                                t.authorization_claims.c.plan_id == ctx.plan_id
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            await db2.dispose()
            assert job["status"] == "EXECUTED", f"job: {job['status']}"
            assert plan_status == "EXECUTED", f"plan: {plan_status}"
            assert claims and all(c == "CONSUMED" for c in claims), f"claims: {claims}"

            # L3 still has exactly 1 execution
            async with ctx.l3_admin.connect() as conn:
                executed = await conn.scalar(
                    select(func.count())
                    .select_from(m.plan_executions)
                    .where(m.plan_executions.c.plan_id == ctx.plan_id)
                )
            assert executed == 1

    asyncio.run(run())


def test_reconcile_cancels_uncommitted(app_config, obligations_db):
    """Execute never reached L3 — only prepare succeeded.

    First G8 → 202 UNKNOWN.  Second G8 reconciles: L3 intent is PREPARED →
    L2 calls reconcile → CANCELLED tombstone → gateway archives FAILED.
    """

    async def run():
        async with allow_plan_ctx(app_config, obligations_db) as ctx:
            l3 = ctx.test_app.client._transport.app.state.l3
            real_execute = l3.execute

            # Wrap: raise before calling real execute (not delivered to L3).
            async def lost_before_execute(_body):
                raise L3OutcomeUnknown("execute request lost before delivery")

            l3.execute = lost_before_execute

            url = EXECUTE_PATH.format(plan_id=ctx.plan_id)

            # First G8 → 202 UNKNOWN
            resp1 = await ctx.test_app.client.post(
                url, json={"request_id": "req_w4c_rec_u_1"}, headers=ctx.headers
            )
            assert resp1.status_code == 202, resp1.text
            assert resp1.json() == {"plan_id": ctx.plan_id, "status": "UNKNOWN"}

            # L3: prepare succeeded, but no execution
            async with ctx.l3_admin.connect() as conn:
                intent = (
                    await conn.execute(
                        select(m.execution_intents.c.status).where(
                            m.execution_intents.c.plan_id == ctx.plan_id
                        )
                    )
                ).scalar()
                executed = await conn.scalar(
                    select(func.count())
                    .select_from(m.plan_executions)
                    .where(m.plan_executions.c.plan_id == ctx.plan_id)
                )
            assert intent == "PREPARED", f"L3 intent: {intent}"
            assert executed == 0

            # Gateway job is UNKNOWN
            db = engine(ctx.db_url, poolclass=NullPool)
            async with db.connect() as conn:
                job_status = await conn.scalar(
                    select(t.execution_jobs.c.status).where(
                        t.execution_jobs.c.plan_id == ctx.plan_id
                    )
                )
            await db.dispose()
            assert job_status == "UNKNOWN"

            # Restore real execute for second G8
            l3.execute = real_execute

            # Second G8 (new request_id) → reconcile → FAILED
            resp2 = await ctx.test_app.client.post(
                url, json={"request_id": "req_w4c_rec_u_2"}, headers=ctx.headers
            )
            assert resp2.status_code == 200, (
                f"expected 200 after reconcile, got {resp2.status_code}: {resp2.text}"
            )
            result = resp2.json()
            assert result["status"] == "FAILED"

            # Terminal state: job FAILED, plan FAILED, claims CONSUMED
            db2 = engine(ctx.db_url, poolclass=NullPool)
            async with db2.connect() as conn:
                job = (
                    (
                        await conn.execute(
                            select(t.execution_jobs.c.status, t.execution_jobs.c.result).where(
                                t.execution_jobs.c.plan_id == ctx.plan_id
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                plan_status = await conn.scalar(
                    select(t.plans.c.status).where(t.plans.c.id == ctx.plan_id)
                )
                claims = (
                    (
                        await conn.execute(
                            select(t.authorization_claims.c.status).where(
                                t.authorization_claims.c.plan_id == ctx.plan_id
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            await db2.dispose()
            assert job["status"] == "FAILED", f"job: {job['status']}"
            assert plan_status == "FAILED", f"plan: {plan_status}"
            assert claims and all(c == "CONSUMED" for c in claims), f"claims: {claims}"

            # L3: intent is CANCELLED, no execution
            async with ctx.l3_admin.connect() as conn:
                intent = (
                    await conn.execute(
                        select(m.execution_intents.c.status).where(
                            m.execution_intents.c.plan_id == ctx.plan_id
                        )
                    )
                ).scalar()
                executed = await conn.scalar(
                    select(func.count())
                    .select_from(m.plan_executions)
                    .where(m.plan_executions.c.plan_id == ctx.plan_id)
                )
            assert intent == "CANCELLED", f"L3 intent after reconcile: {intent}"
            assert executed == 0

            # One EXECUTION_RESULT notification, outcome FAILED.
            db3 = engine(ctx.db_url, poolclass=NullPool)
            async with db3.connect() as conn:
                notes = (
                    (
                        await conn.execute(
                            select(t.notifications.c.payload).where(
                                t.notifications.c.event_id == ctx.plan_id,
                                t.notifications.c.type == "EXECUTION_RESULT",
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            await db3.dispose()
            assert [n["outcome"] for n in notes] == ["FAILED"]

    asyncio.run(run())


def test_reconcile_unreachable_stays_unknown(app_config, obligations_db):
    """Even reconciliation cannot reach L3 — stays UNKNOWN.

    First G8 → 202 UNKNOWN (execute lost).  Before second G8, both
    l3.execution and l3.reconcile are wrapped to raise L3OutcomeUnknown.
    Second G8 → still 202 UNKNOWN, claims stay CLAIMED.
    """

    async def run():
        async with allow_plan_ctx(app_config, obligations_db) as ctx:
            l3 = ctx.test_app.client._transport.app.state.l3

            # First G8: execute raises without calling real.
            async def lost_before_execute(_body):
                raise L3OutcomeUnknown("execute request lost before delivery")

            l3.execute = lost_before_execute

            url = EXECUTE_PATH.format(plan_id=ctx.plan_id)

            resp1 = await ctx.test_app.client.post(
                url, json={"request_id": "req_w4c_rec_x_1"}, headers=ctx.headers
            )
            assert resp1.status_code == 202, resp1.text

            # Now wrap both execution and reconcile to be unreachable
            real_execution = l3.execution
            real_reconcile = l3.reconcile

            async def unreachable_execution(*_args, **_kwargs):
                raise L3OutcomeUnknown("execution query unreachable")

            async def unreachable_reconcile(*_args, **_kwargs):
                raise L3OutcomeUnknown("reconcile unreachable")

            l3.execution = unreachable_execution
            l3.reconcile = unreachable_reconcile

            # Second G8 → still 202, still UNKNOWN
            resp2 = await ctx.test_app.client.post(
                url, json={"request_id": "req_w4c_rec_x_2"}, headers=ctx.headers
            )
            assert resp2.status_code == 202, f"expected 202, got {resp2.status_code}: {resp2.text}"
            assert resp2.json() == {"plan_id": ctx.plan_id, "status": "UNKNOWN"}

            # Gateway state: job UNKNOWN, claims CLAIMED
            db = engine(ctx.db_url, poolclass=NullPool)
            async with db.connect() as conn:
                job_status = await conn.scalar(
                    select(t.execution_jobs.c.status).where(
                        t.execution_jobs.c.plan_id == ctx.plan_id
                    )
                )
                plan_status = await conn.scalar(
                    select(t.plans.c.status).where(t.plans.c.id == ctx.plan_id)
                )
                claims = (
                    (
                        await conn.execute(
                            select(t.authorization_claims.c.status).where(
                                t.authorization_claims.c.plan_id == ctx.plan_id
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            await db.dispose()
            assert job_status == "UNKNOWN", f"job: {job_status}"
            assert plan_status == "UNKNOWN", f"plan: {plan_status}"
            assert claims and all(c == "CLAIMED" for c in claims), f"claims: {claims}"

            # Restore real methods (cleanup)
            l3.execution = real_execution
            l3.reconcile = real_reconcile

    asyncio.run(run())
