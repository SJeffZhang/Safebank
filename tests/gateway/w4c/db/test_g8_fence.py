"""W4c T4a: ADR-008 STATE_CHANGED fence — red tests.

G8 currently returns 202 UNKNOWN when L3 execute signals STATE_CHANGED.
The ADR-008 fence (PAUSED job, shortened L3 intent, persisted new evaluation)
is not yet implemented, so all three tests are expected to fail.
"""

from __future__ import annotations

import asyncio

from app import models as m
from sqlalchemy import func, select, text
from sqlalchemy.pool import NullPool

from services.gateway.app import tables as t
from services.gateway.app.db import engine
from tests.gateway.w4c.db.support import allow_plan_ctx

EXECUTE_PATH = "/plans/{plan_id}/execute"


def test_fence_pauses_job_and_keeps_claim(app_config, obligations_db):
    """G8 returns 409 STATE_CHANGED and pauses the job.

    When the real L3 state has changed since the snapshot was taken, G8 must:
    * Return 409 with ``error.code == "STATE_CHANGED"``.
    * Set ``execution_jobs.status`` to ``PAUSED`` with ``l3_attempt == 1``
      and ``deadline_wall`` set to the fence sentinel (1970-01-01T00:00:00Z).
    * Keep all ``authorization_claims`` as ``CLAIMED``.
    * Leave the utterance unconsumed.
    """

    async def run():
        async with allow_plan_ctx(app_config, obligations_db) as ctx:
            # Wrap L3 execute to bump state_version before the first call.
            l3 = ctx.test_app.client._transport.app.state.l3
            original_execute = l3.execute
            execute_count = 0

            async def bump_then_execute(body):
                nonlocal execute_count
                execute_count += 1
                if execute_count == 1:
                    async with ctx.l3_admin.begin() as conn:
                        await conn.execute(
                            text(
                                "UPDATE state_versions SET version = version + 1"
                                " WHERE customer_id = 'cus_zs'"
                            )
                        )
                return await original_execute(body)

            l3.execute = bump_then_execute

            url = EXECUTE_PATH.format(plan_id=ctx.plan_id)
            body = {"request_id": "req_w4c_fence_1"}

            resp = await ctx.test_app.client.post(url, json=body, headers=ctx.headers)

            # Currently G8 returns 202 UNKNOWN instead of 409 STATE_CHANGED;
            # this assertion is the expected ADR-008 behaviour.
            assert resp.status_code == 409, f"G8 returned {resp.status_code}: {resp.text}"
            error = resp.json()["error"]
            assert error["code"] == "STATE_CHANGED", f"error code={error.get('code')}"
            assert error.get("details", {}).get("decision") is not None, (
                f"details.decision missing: {error.get('details')}"
            )

            # Gateway: execution_jobs PAUSED with fence sentinel deadline.
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
                assert job["status"] == "PAUSED", f"job status={job['status']}"
                assert job["l3_attempt"] == 1, f"l3_attempt={job['l3_attempt']}"
                deadline = job["deadline_wall"]
                assert deadline is not None, "deadline_wall is None"
                assert str(deadline).startswith("1970-01-01"), (
                    f"deadline_wall={deadline}, expected 1970-01-01 fence sentinel"
                )

                # Claims still CLAIMED.
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
                assert claims and all(c == "CLAIMED" for c in claims), f"claim statuses={claims}"

                # Utterance not CONSUMED.
                utt_status = await conn.scalar(
                    select(t.utterances.c.status).where(t.utterances.c.id == ctx.utt_id)
                )
                assert utt_status != "CONSUMED", f"utt status={utt_status}"
            await db.dispose()

    asyncio.run(run())


def test_fence_shortens_l3_intent_deadline(app_config, obligations_db):
    """After fence, the real L3 execution intent carries the fence sentinel deadline.

    * ``execution_intents.deadline_wall`` is the fence sentinel
      (1970-01-01T00:00:00Z).
    * ``execution_intents.attempt == 1``.
    * ``execution_intents.status`` is still ``PREPARED``.
    * ``plan_executions`` count is 0 (nothing was committed).
    * ``l3.execute`` was called exactly once (the fence itself does not
      resubmit to L3).
    """

    async def run():
        async with allow_plan_ctx(app_config, obligations_db) as ctx:
            # Wrap L3 execute to bump state_version before the first call.
            l3 = ctx.test_app.client._transport.app.state.l3
            original_execute = l3.execute
            execute_count = 0

            async def bump_then_execute(body):
                nonlocal execute_count
                execute_count += 1
                if execute_count == 1:
                    async with ctx.l3_admin.begin() as conn:
                        await conn.execute(
                            text(
                                "UPDATE state_versions SET version = version + 1"
                                " WHERE customer_id = 'cus_zs'"
                            )
                        )
                return await original_execute(body)

            l3.execute = bump_then_execute

            url = EXECUTE_PATH.format(plan_id=ctx.plan_id)
            body = {"request_id": "req_w4c_fence_2"}

            await ctx.test_app.client.post(url, json=body, headers=ctx.headers)

            # execute was called exactly once (prepare + execute flow: prepare
            # succeeds, execute sees STATE_CHANGED and the barrier must not
            # re-attempt).
            assert execute_count == 1, f"execute called {execute_count} times, expected 1"

            # Real L3: execution_intent deadline is the fence sentinel.
            async with ctx.l3_admin.connect() as conn:
                intent = (
                    (
                        await conn.execute(
                            select(m.execution_intents).where(
                                m.execution_intents.c.plan_id == ctx.plan_id
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                assert intent["attempt"] == 1, f"attempt={intent['attempt']}"
                assert intent["status"] == "PREPARED", f"intent status={intent['status']}"
                deadline = intent["deadline_wall"]
                assert deadline is not None, "deadline_wall is None"
                assert str(deadline).startswith("1970-01-01"), (
                    f"deadline_wall={deadline}, expected 1970-01-01 fence sentinel"
                )

                # Nothing was committed.
                count = await conn.scalar(
                    select(func.count())
                    .select_from(m.plan_executions)
                    .where(m.plan_executions.c.plan_id == ctx.plan_id)
                )
                assert count == 0, f"plan_executions count={count}, expected 0"

    asyncio.run(run())


def test_fence_records_new_evaluation(app_config, obligations_db):
    """After fence, a new evaluation is persisted with the updated decision.

    * The latest evaluation revision for this plan is greater than before the
      G8 call.
    * The new evaluation's ``decision`` matches ``details.decision.decision``
      returned in the 409 error body.
    """

    async def run():
        async with allow_plan_ctx(app_config, obligations_db) as ctx:
            # Count existing evaluations before calling G8.
            db_before = engine(ctx.db_url, poolclass=NullPool)
            async with db_before.connect() as conn:
                rev_before = await conn.scalar(
                    select(func.max(t.evaluations.c.revision)).where(
                        t.evaluations.c.plan_id == ctx.plan_id
                    )
                )
            await db_before.dispose()

            # Wrap L3 execute to bump state_version before the first call.
            l3 = ctx.test_app.client._transport.app.state.l3
            original_execute = l3.execute
            execute_count = 0

            async def bump_then_execute(body):
                nonlocal execute_count
                execute_count += 1
                if execute_count == 1:
                    async with ctx.l3_admin.begin() as conn:
                        await conn.execute(
                            text(
                                "UPDATE state_versions SET version = version + 1"
                                " WHERE customer_id = 'cus_zs'"
                            )
                        )
                return await original_execute(body)

            l3.execute = bump_then_execute

            url = EXECUTE_PATH.format(plan_id=ctx.plan_id)
            body = {"request_id": "req_w4c_fence_3"}

            resp = await ctx.test_app.client.post(url, json=body, headers=ctx.headers)

            # Currently G8 returns 202 UNKNOWN; ADR-008 expects 409.
            assert resp.status_code == 409, f"G8 returned {resp.status_code}: {resp.text}"
            error = resp.json()["error"]
            decision_from_error = error["details"]["decision"]["decision"]

            # New evaluation was persisted.
            db_after = engine(ctx.db_url, poolclass=NullPool)
            async with db_after.connect() as conn:
                rev_after = await conn.scalar(
                    select(func.max(t.evaluations.c.revision)).where(
                        t.evaluations.c.plan_id == ctx.plan_id
                    )
                )

                assert rev_after is not None, "no evaluation found"
                assert rev_after > rev_before, (
                    f"revision after={rev_after} not > before={rev_before}"
                )

                # The new evaluation's decision matches what G8 returned.
                new_eval = (
                    (
                        await conn.execute(
                            select(t.evaluations.c.decision).where(
                                t.evaluations.c.plan_id == ctx.plan_id,
                                t.evaluations.c.revision == rev_after,
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                assert new_eval["decision"]["decision"] == decision_from_error, (
                    f"eval decision={new_eval['decision']['decision']},"
                    f" error decision={decision_from_error}"
                )
            await db_after.dispose()

    asyncio.run(run())
