"""W4c T2: an unclear L3 outcome after the job is committed never strands it.

The real L3 prepares the plan, then ``execute`` reports an unknown outcome.
G8 must answer 202 UNKNOWN, record the job and plan as UNKNOWN, keep the
authorization claim held and leave the utterance unconsumed, so only
reconciliation (never a resubmission) can settle it.
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


def test_g8_unknown_execute_outcome_keeps_claim(app_config, obligations_db):
    async def run():
        async with allow_plan_ctx(app_config, obligations_db) as ctx:
            l3 = ctx.test_app.client._transport.app.state.l3

            async def lost_reply(body):
                raise L3OutcomeUnknown("reply lost after delivery")

            l3.execute = lost_reply
            url = EXECUTE_PATH.format(plan_id=ctx.plan_id)
            resp = await ctx.test_app.client.post(
                url, json={"request_id": "req_w4c_unknown"}, headers=ctx.headers
            )
            assert resp.status_code == 202, resp.text
            assert resp.json() == {"plan_id": ctx.plan_id, "status": "UNKNOWN"}

            db = engine(ctx.db_url, poolclass=NullPool)
            async with db.connect() as conn:
                job = await conn.scalar(
                    select(t.execution_jobs.c.status).where(
                        t.execution_jobs.c.plan_id == ctx.plan_id
                    )
                )
                plan = await conn.scalar(
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
                utt = await conn.scalar(
                    select(t.utterances.c.status).where(t.utterances.c.id == ctx.utt_id)
                )
            await db.dispose()
            assert job == "UNKNOWN"
            assert plan == "UNKNOWN"
            assert claims and all(c == "CLAIMED" for c in claims)
            assert utt != "CONSUMED"

            # The prepared intent exists in L3, but nothing was executed.
            async with ctx.l3_admin.connect() as conn:
                executed = await conn.scalar(
                    select(func.count())
                    .select_from(m.plan_executions)
                    .where(m.plan_executions.c.plan_id == ctx.plan_id)
                )
            assert executed == 0

    asyncio.run(run())
