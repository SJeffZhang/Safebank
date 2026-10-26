"""W4c review-fix round 1: red tests for P1-1, P1-2, P2-1, P2-3.

All tests use real L3 via :func:`allow_plan_ctx` (ALLOW freeze_card plan).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from app import models as m
from sqlalchemy import func, select, text
from sqlalchemy.pool import NullPool

from services.gateway.app import tables as t
from services.gateway.app.db import engine
from tests.gateway.w4c.db.support import allow_plan_ctx

FENCE_AT = datetime(1970, 1, 1, tzinfo=UTC)

EXECUTE_PATH = "/plans/{plan_id}/execute"


# ── P1-1 + P1-2: resume STATE_CHANGED enters fence again ──────────────────────


def test_resume_state_changed_enters_fence_again(app_config, obligations_db):
    """Resume execute hits STATE_CHANGED → _handle_state_changed with correct attempt.

    G8(r1): bump clock → fence → 409 PAUSED.
    G8(r2): resume, bump clock again → execute STATE_CHANGED → fence again → 409.
           Assert L3 intent attempt==2, deadline_wall==FENCE_AT, job PAUSED l3_attempt==2.
    G8(r3): no bump → resume execute succeeds → 200 EXECUTED,
           intent attempt 3, plan_executions==1, execute called 3 times.
    """

    async def run():
        async with allow_plan_ctx(app_config, obligations_db) as ctx:
            l3 = ctx.test_app.client._transport.app.state.l3
            original_execute = l3.execute
            execute_count = 0

            async def bump_then_execute(body):
                nonlocal execute_count
                execute_count += 1
                if execute_count in (1, 2):
                    # Bump clock before execute so L3 rejects with STATE_CHANGED.
                    async with ctx.l3_admin.begin() as conn:
                        await conn.execute(text("UPDATE clock SET version = version + 1"))
                return await original_execute(body)

            l3.execute = bump_then_execute

            url = EXECUTE_PATH.format(plan_id=ctx.plan_id)

            # ── r1: normal path, bump → fence ────────────────────────────
            resp1 = await ctx.test_app.client.post(
                url, json={"request_id": "req_w4c_fix_r1"}, headers=ctx.headers
            )
            assert resp1.status_code == 409, f"r1 returned {resp1.status_code}: {resp1.text}"
            err1 = resp1.json()["error"]
            assert err1["code"] == "STATE_CHANGED", f"r1 code={err1.get('code')}"

            # ── r2: resume, bump again → STATE_CHANGED on attempt 2 ─────
            resp2 = await ctx.test_app.client.post(
                url, json={"request_id": "req_w4c_fix_r2"}, headers=ctx.headers
            )
            # After P1-1 fix: resume should enter _handle_state_changed, not _unknown.
            assert resp2.status_code == 409, f"r2 returned {resp2.status_code}: {resp2.text}"
            err2 = resp2.json()["error"]
            assert err2["code"] == "STATE_CHANGED", f"r2 code={err2.get('code')}"

            # L3 assertions after r2 (fence on resume).
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
                assert intent["attempt"] == 2, f"L3 intent attempt={intent['attempt']}, expected 2"
                # FENCE_AT is 1970-01-01 00:00:00+00:00 (epoch zero).
                assert intent["deadline_wall"] == FENCE_AT, (
                    f"L3 intent deadline={intent['deadline_wall']}, expected 1970-01-01Z"
                )
                executed = await conn.scalar(
                    select(func.count())
                    .select_from(m.plan_executions)
                    .where(m.plan_executions.c.plan_id == ctx.plan_id)
                )
                assert executed == 0, f"plan_executions={executed}, expected 0"

            # Gateway job assertions after r2.
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
                assert job["l3_attempt"] == 2, f"job l3_attempt={job['l3_attempt']}"
            await db.dispose()

            # ── r3: resume, no bump → execute succeeds ───────────────────
            resp3 = await ctx.test_app.client.post(
                url, json={"request_id": "req_w4c_fix_r3"}, headers=ctx.headers
            )
            assert resp3.status_code == 200, f"r3 returned {resp3.status_code}: {resp3.text}"
            result = resp3.json()
            assert result["status"] == "EXECUTED", f"r3 status={result.get('status')}"

            # L3 assertions after r3.
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
                assert intent["attempt"] == 3, f"L3 intent attempt={intent['attempt']}, expected 3"
                executed = await conn.scalar(
                    select(func.count())
                    .select_from(m.plan_executions)
                    .where(m.plan_executions.c.plan_id == ctx.plan_id)
                )
                assert executed == 1, f"plan_executions={executed}, expected 1"

            # execute was called exactly 3 times (r1 + r2 + r3).
            assert execute_count == 3, f"execute_count={execute_count}, expected 3"

    asyncio.run(run())


# ── P2-1: PAUSED + DENIED/REPLAN plan is not resumed ─────────────────────────


def test_paused_replan_plan_is_not_resumed(app_config, obligations_db):
    """PAUSED job with REPLAN plan status returns 409 without entering resume.

    1st G8: bump state_version → replay REPLAN → plan REPLAN, job PAUSED.
    2nd G8 → 409 STATE_CHANGED, details.decision.decision == "REPLAN".
    execute is called only once (the 1st G8).
    """

    async def run():
        async with allow_plan_ctx(app_config, obligations_db) as ctx:
            l3 = ctx.test_app.client._transport.app.state.l3
            original_execute = l3.execute
            execute_count = 0

            async def bump_then_execute(body):
                nonlocal execute_count
                execute_count += 1
                if execute_count == 1:
                    # Bump state_version to make reads stale → replay → REPLAN.
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

            # ── 1st G8: fence → REPLAN ───────────────────────────────────
            resp1 = await ctx.test_app.client.post(
                url, json={"request_id": "req_w4c_fix_rp1"}, headers=ctx.headers
            )
            assert resp1.status_code == 409, f"r1 returned {resp1.status_code}: {resp1.text}"
            err1 = resp1.json()["error"]
            assert err1["code"] == "STATE_CHANGED", f"r1 code={err1.get('code')}"
            assert err1["details"]["decision"]["decision"] == "REPLAN", (
                f"r1 decision={err1['details']['decision']['decision']}"
            )

            # ── 2nd G8: PAUSED + REPLAN → 409 without resume ────────────
            resp2 = await ctx.test_app.client.post(
                url, json={"request_id": "req_w4c_fix_rp2"}, headers=ctx.headers
            )
            assert resp2.status_code == 409, f"r2 returned {resp2.status_code}: {resp2.text}"
            err2 = resp2.json()["error"]
            assert err2["code"] == "STATE_CHANGED", f"r2 code={err2.get('code')}"
            assert err2["details"]["decision"]["decision"] == "REPLAN", (
                f"r2 decision={err2['details']['decision']['decision']}"
            )

            # execute was only called once (r1 only, r2 was rejected before resume).
            assert execute_count == 1, f"execute_count={execute_count}, expected 1"

    asyncio.run(run())


# ── P2-3: reconciliation writes terminal dedup for current request ────────────


def test_reconcile_writes_terminal_dedup_for_current_request(app_config, obligations_db):
    """Reconciliation writes a terminal request_dedup row for the reconciling request.

    G8(r1): execute commits at L3, reply lost → 202 UNKNOWN.
    G8(r2): reconciliation → 200 EXECUTED (replayed).
            Gateway request_dedup has r2 row with status_code==200.
    G8(r2) again: idempotent replay returns same response.
    """

    async def run():
        async with allow_plan_ctx(app_config, obligations_db) as ctx:
            l3 = ctx.test_app.client._transport.app.state.l3
            real_execute = l3.execute

            # Wrap: call real execute (L3 commits), then simulate lost reply.
            async def execute_then_lost(body):
                await real_execute(body)  # L3 commits
                from services.gateway.app.l3.client import L3OutcomeUnknown

                raise L3OutcomeUnknown("reply lost after delivery")

            l3.execute = execute_then_lost

            url = EXECUTE_PATH.format(plan_id=ctx.plan_id)

            # ── r1: execute commits but reply lost → 202 UNKNOWN ─────────
            resp1 = await ctx.test_app.client.post(
                url, json={"request_id": "req_w4c_fix_dedup_1"}, headers=ctx.headers
            )
            assert resp1.status_code == 202, resp1.text
            assert resp1.json() == {"plan_id": ctx.plan_id, "status": "UNKNOWN"}

            # Restore real execute for reconciliation.
            l3.execute = real_execute

            # ── r2: reconciliation → 200 EXECUTED ───────────────────────
            first_resp2 = await ctx.test_app.client.post(
                url, json={"request_id": "req_w4c_fix_dedup_2"}, headers=ctx.headers
            )
            assert first_resp2.status_code == 200, (
                f"r2 returned {first_resp2.status_code}: {first_resp2.text}"
            )
            r2_body = first_resp2.json()
            assert r2_body["status"] == "EXECUTED"

            # Gateway request_dedup has r2 row with status_code==200.
            db = engine(ctx.db_url, poolclass=NullPool)
            async with db.connect() as conn:
                dedup = (
                    (
                        await conn.execute(
                            select(t.request_dedup).where(
                                t.request_dedup.c.customer_id == "cus_zs",
                                t.request_dedup.c.request_id == "req_w4c_fix_dedup_2",
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
            await db.dispose()
            assert dedup is not None, "no request_dedup row for r2"
            assert dedup["status_code"] == 200, (
                f"r2 dedup status_code={dedup['status_code']}, expected 200"
            )

            # ── r2 again: idempotent replay returns same response ───────
            second_resp2 = await ctx.test_app.client.post(
                url, json={"request_id": "req_w4c_fix_dedup_2"}, headers=ctx.headers
            )
            assert second_resp2.status_code == 200, (
                f"r2 replay returned {second_resp2.status_code}: {second_resp2.text}"
            )
            second_body = second_resp2.json()
            assert second_body["status"] == r2_body["status"]
            assert second_body.get("plan_id") == r2_body.get("plan_id")

    asyncio.run(run())
