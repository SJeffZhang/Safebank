"""W4c T2: G8 ``POST /plans/{plan_id}/execute`` ALLOW path against a real L3.

Written red in T2b (G8 was a stub returning 503); green since T2e/T2f.
"""

from __future__ import annotations

import asyncio

from app import models as m
from sqlalchemy import func, select
from sqlalchemy.pool import NullPool

from services.gateway.app import tables as t
from services.gateway.app.db import engine
from tests.gateway.w4c.db.support import allow_plan_ctx

EXECUTE_PATH = "/plans/{plan_id}/execute"


def test_g8_allow_executes_once(app_config, obligations_db):
    """G8 executes the ALLOW plan and the real L3 card is frozen.

    Sending the same request_id a second time should return the same
    result (idempotency), provable by counting L3 operation rows — only
    one execution row is created.
    """

    async def run():
        async with allow_plan_ctx(app_config, obligations_db) as ctx:
            url = EXECUTE_PATH.format(plan_id=ctx.plan_id)
            body = {"request_id": "req_w4c_exec_1"}

            # 1. First execution — must succeed.
            resp = await ctx.test_app.client.post(url, json=body, headers=ctx.headers)
            assert resp.status_code == 200, f"G8 stub returned {resp.status_code}: {resp.text}"
            result = resp.json()
            assert result["status"] == "EXECUTED"

            # 2. Check real L3: card crd_zs_1 is now frozen.
            async with ctx.l3_admin.connect() as conn:
                row = (
                    await conn.execute(select(m.cards.c.status).where(m.cards.c.id == "crd_zs_1"))
                ).one()
            assert row[0] == "FROZEN", f"expected FROZEN, got {row[0]}"

            # 3. Same request_id again → 200, no double execution.
            #    Proved by checking real L3 plan_executions — only 1 row.
            resp2 = await ctx.test_app.client.post(url, json=body, headers=ctx.headers)
            assert resp2.status_code == 200

            # Strengthened idempotency: L3 plan_executions count == 1.
            async with ctx.l3_admin.connect() as conn:
                count = await conn.scalar(
                    select(func.count())
                    .select_from(m.plan_executions)
                    .where(m.plan_executions.c.plan_id == ctx.plan_id)
                )
            assert count == 1, f"L3 plan_executions count={count}, expected 1"

    asyncio.run(run())


def test_g8_allow_job_and_plan_terminal(app_config, obligations_db):
    """After G8 executes, gateway execution_jobs and plans are terminal.

    * ``execution_jobs`` has one row for this plan with ``l3_attempt == 1``
      and ``status == "EXECUTED"``.
    * ``plans.status == "EXECUTED"``.
    """

    async def run():
        async with allow_plan_ctx(app_config, obligations_db) as ctx:
            url = EXECUTE_PATH.format(plan_id=ctx.plan_id)
            body = {"request_id": "req_w4c_exec_2"}

            resp = await ctx.test_app.client.post(url, json=body, headers=ctx.headers)
            assert resp.status_code == 200, f"G8 stub returned {resp.status_code}: {resp.text}"

            db = engine(ctx.db_url, poolclass=NullPool)
            async with db.connect() as conn:
                job = (
                    (
                        await conn.execute(
                            select(t.execution_jobs).where(
                                t.execution_jobs.c.plan_id == ctx.plan_id
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                assert job["l3_attempt"] == 1, f"l3_attempt={job['l3_attempt']}"
                assert job["status"] == "EXECUTED", f"job status={job['status']}"

                plan = (
                    (
                        await conn.execute(
                            select(t.plans.c.status).where(t.plans.c.id == ctx.plan_id)
                        )
                    )
                    .mappings()
                    .one()
                )
                assert plan["status"] == "EXECUTED", f"plan status={plan['status']}"
            await db.dispose()

    asyncio.run(run())


def test_g8_allow_consumes_claims(app_config, obligations_db):
    """After G8 executes, the consumed utterance is marked CONSUMED."""

    async def run():
        async with allow_plan_ctx(app_config, obligations_db) as ctx:
            url = EXECUTE_PATH.format(plan_id=ctx.plan_id)
            body = {"request_id": "req_w4c_exec_3"}

            resp = await ctx.test_app.client.post(url, json=body, headers=ctx.headers)
            assert resp.status_code == 200, f"G8 stub returned {resp.status_code}: {resp.text}"

            db = engine(ctx.db_url, poolclass=NullPool)
            async with db.connect() as conn:
                row = (
                    (
                        await conn.execute(
                            select(t.utterances.c.status).where(t.utterances.c.id == ctx.utt_id)
                        )
                    )
                    .mappings()
                    .one()
                )
                assert row["status"] == "CONSUMED", f"utt status={row['status']}"
            await db.dispose()

    asyncio.run(run())
