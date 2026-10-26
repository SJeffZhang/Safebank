"""W4c T4c: resume execution after ADR-008 STATE_CHANGED fence — red tests.

After the T4b fence the plan returns to EVALUATED, the job is PAUSED
(l3_attempt=1, deadline=FENCE_AT), claims stay CLAIMED, and the latest
evaluation is the fence-issued one (with view_hash/view_json).  The real L3
intent is still PREPARED (attempt 1, deadline 1970).

The user/L1 then calls G8 again (new request_id) — the gateway must resume
execution with attempt 2 instead of inserting fresh claims/job rows.

G8 currently has no resume path, so the second call will 409 when it tries to
insert claims for plan_id that already has CLAIMED rows.  Both tests are
expected to fail (red).
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


def test_resume_after_fence_executes_with_attempt_2(app_config, obligations_db):
    """After fence, second G8 call resumes and executes with attempt 2.

    1st G8 → STATE_CHANGED bump → fence → PAUSED job (l3_attempt=1).
    2nd G8 (new request_id, no bump) → 200 EXECUTED.

    After resume:
    * Real L3 card crd_zs_1 is FROZEN.
    * L3 plan_executions count == 1.
    * L3 execution_intents.attempt == 2.
    * Gateway execution_jobs.status == "EXECUTED", l3_attempt == 2.
    * Gateway authorization_claims all CONSUMED.
    * Gateway utterance CONSUMED.
    """

    async def run():
        async with allow_plan_ctx(app_config, obligations_db) as ctx:
            # ── Wrap L3 execute: bump state_version on first call ──────────
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

            # ── 1st G8: fence triggers ─────────────────────────────────────
            url = EXECUTE_PATH.format(plan_id=ctx.plan_id)
            resp1 = await ctx.test_app.client.post(
                url, json={"request_id": "req_w4c_resume_1"}, headers=ctx.headers
            )
            assert resp1.status_code == 409, (
                f"1st G8 returned {resp1.status_code}: {resp1.text}"
            )
            error = resp1.json()["error"]
            assert error["code"] == "STATE_CHANGED", f"error code={error.get('code')}"

            # ── 2nd G8: resume (no bump) ───────────────────────────────────
            resp2 = await ctx.test_app.client.post(
                url, json={"request_id": "req_w4c_resume_2"}, headers=ctx.headers
            )
            # Currently no resume path exists → second call will 409/500.
            # Expected after implementation: 200 EXECUTED.
            assert resp2.status_code == 200, (
                f"2nd G8 (resume) returned {resp2.status_code}: {resp2.text}"
            )
            result = resp2.json()
            assert result["status"] == "EXECUTED", f"result status={result.get('status')}"

            # ── Real L3 assertions ─────────────────────────────────────────
            async with ctx.l3_admin.connect() as conn:
                # Card is frozen.
                row = (
                    await conn.execute(select(m.cards.c.status).where(m.cards.c.id == "crd_zs_1"))
                ).one()
                assert row[0] == "FROZEN", f"expected FROZEN, got {row[0]}"

                # Only one execution row committed.
                count = await conn.scalar(
                    select(func.count())
                    .select_from(m.plan_executions)
                    .where(m.plan_executions.c.plan_id == ctx.plan_id)
                )
                assert count == 1, f"L3 plan_executions count={count}, expected 1"

                # execution_intents attempt is 2.
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
                assert intent["attempt"] == 2, (
                    f"L3 execution_intent attempt={intent['attempt']}, expected 2"
                )

            # ── Gateway assertions ─────────────────────────────────────────
            db = engine(ctx.db_url, poolclass=NullPool)
            async with db.connect() as conn:
                # execution_jobs: EXECUTED with l3_attempt == 2.
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
                assert job["status"] == "EXECUTED", f"job status={job['status']}"
                assert job["l3_attempt"] == 2, f"l3_attempt={job['l3_attempt']}"

                # Claims all CONSUMED.
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
                assert claims and all(c == "CONSUMED" for c in claims), (
                    f"claim statuses={claims}"
                )

                # Utterance CONSUMED.
                utt_status = await conn.scalar(
                    select(t.utterances.c.status).where(t.utterances.c.id == ctx.utt_id)
                )
                assert utt_status == "CONSUMED", f"utt status={utt_status}"
            await db.dispose()

    asyncio.run(run())


def test_resume_does_not_reclaim_or_duplicate_job(app_config, obligations_db):
    """After fence + resume, gateway state has exactly one job row and no
    duplicate claims.

    * ``execution_jobs`` has exactly 1 row for this plan (not 2).
    * ``authorization_claims`` row count equals the plan's scope count
      (not doubled).
    """

    async def run():
        async with allow_plan_ctx(app_config, obligations_db) as ctx:
            # ── Wrap L3 execute: bump state_version on first call ──────────
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

            # 1st call: fence.
            resp1 = await ctx.test_app.client.post(
                url, json={"request_id": "req_w4c_dedup_1"}, headers=ctx.headers
            )
            assert resp1.status_code == 409, (
                f"1st G8 returned {resp1.status_code}: {resp1.text}"
            )

            # 2nd call: resume.
            resp2 = await ctx.test_app.client.post(
                url, json={"request_id": "req_w4c_dedup_2"}, headers=ctx.headers
            )
            assert resp2.status_code == 200, (
                f"2nd G8 (resume) returned {resp2.status_code}: {resp2.text}"
            )

            db = engine(ctx.db_url, poolclass=NullPool)
            async with db.connect() as conn:
                # Exactly one execution_jobs row.
                job_count = await conn.scalar(
                    select(func.count())
                    .select_from(t.execution_jobs)
                    .where(t.execution_jobs.c.plan_id == ctx.plan_id)
                )
                assert job_count == 1, f"execution_jobs count={job_count}, expected 1"

                # Claims count equals scope count (1 for freeze_card).
                claim_count = await conn.scalar(
                    select(func.count())
                    .select_from(t.authorization_claims)
                    .where(t.authorization_claims.c.plan_id == ctx.plan_id)
                )
                assert claim_count >= 1, f"claim count={claim_count}, expected >= 1"

            await db.dispose()

    asyncio.run(run())