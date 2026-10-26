"""W4c T7: G5 plan status and G8 request_id replay — red tests.

G5  is a stub (503) and G8 has no request_dedup, so all 6 tests start RED.
"""

from __future__ import annotations

import asyncio
import hashlib

from app import models as m
from sqlalchemy import func, select
from sqlalchemy.pool import NullPool

from services.gateway.app import tables as t
from services.gateway.app.db import engine
from tests.gateway.w3b.db.conftest import _seed_auth
from tests.gateway.w4c.db.support import (
    _anchor_to_real_time,
    _freeze_scope,
    _seed_session,
    _seed_utterance,
    allow_plan_ctx,
)

EXECUTE_PATH = "/plans/{plan_id}/execute"
G5_PATH = "/plans/{plan_id}"

ROUTE_EXECUTE = "POST /plans/{plan_id}/execute"


def test_g5_after_execute_shows_result(app_config, obligations_db):
    """G5 (A headers) after G8 success returns status, decision, and execution result."""

    async def run():
        async with allow_plan_ctx(app_config, obligations_db) as ctx:
            # First, execute the plan via G8.
            url = EXECUTE_PATH.format(plan_id=ctx.plan_id)
            resp = await ctx.test_app.client.post(
                url, json={"request_id": "req_w4c_g5_a_1"}, headers=ctx.headers
            )
            assert resp.status_code == 200, f"G8 failed: {resp.text}"
            exec_result = resp.json()
            assert exec_result["status"] == "EXECUTED"

            # Now call G5 with A headers.
            g5_url = G5_PATH.format(plan_id=ctx.plan_id)
            g5_resp = await ctx.test_app.client.get(g5_url, headers=ctx.headers)
            assert g5_resp.status_code == 200, f"G5 returned {g5_resp.status_code}: {g5_resp.text}"
            status_view = g5_resp.json()
            assert status_view["plan_id"] == ctx.plan_id
            assert status_view["status"] == "EXECUTED"
            assert status_view["decision"]["decision"] == "ALLOW"
            assert status_view["decision"]["evaluation_id"] is not None
            assert status_view["decision"]["revision"] is not None
            assert status_view["execution"] is not None
            assert status_view["execution"]["status"] == "EXECUTED"

    asyncio.run(run())


def test_g5_other_session_is_hidden(app_config, obligations_db):
    """A caller can only see plans from its own session.

    Two sessions for the same customer; A tokens to each.  The first
    session's A token must get 404 when querying the second session's plan.
    """

    async def run():
        async with allow_plan_ctx(app_config, obligations_db) as ctx:
            # Build a second session for the same customer.
            sid2 = "ses_w4c_g5_hide"
            utt2 = f"utt_{sid2}_1"
            jti2 = "jti-w4c-g5-hide"

            db = engine(ctx.db_url, poolclass=NullPool)
            async with db.begin() as conn:
                await _seed_session(conn, sid=sid2, customer="cus_zs")
                await _seed_auth(conn, jti=jti2, customer="cus_zs")
                await _seed_utterance(
                    conn, utt_id=utt2, sid=sid2, text="查余额", scope=_freeze_scope()
                )
                await _anchor_to_real_time(conn, sid=sid2)
            await db.dispose()

            # A token for sid2
            a_headers_2 = ctx.test_app.auth_headers(
                customer_id="cus_zs", sid=sid2, jti=jti2, utts=[utt2]
            )

            # sid1's A token queries sid1's plan → OK
            g5_url = G5_PATH.format(plan_id=ctx.plan_id)
            resp_ok = await ctx.test_app.client.get(g5_url, headers=ctx.headers)
            assert resp_ok.status_code == 200, f"own plan: {resp_ok.text}"

            # sid2's A token queries sid1's plan → 404
            resp_hidden = await ctx.test_app.client.get(g5_url, headers=a_headers_2)
            assert resp_hidden.status_code == 404, (
                f"expected 404 for other session, got {resp_hidden.status_code}: {resp_hidden.text}"
            )

    asyncio.run(run())


def test_g5_reconciles_unknown(app_config, obligations_db):
    """G5 inline-reconciles an UNKNOWN job without ever calling G8 a second time.

    Uses the same pattern as test_g8_reconcile: wrap L3 execute to raise
    L3OutcomeUnknown after committing, then call G5 (not G8).  G5 must
    reconcile to EXECUTED, and L3 must only have one execution.
    """

    async def run():
        async with allow_plan_ctx(app_config, obligations_db) as ctx:
            l3 = ctx.test_app.client._transport.app.state.l3
            real_execute = l3.execute

            async def execute_then_lost(body):
                await real_execute(body)
                from services.gateway.app.l3.client import L3OutcomeUnknown

                raise L3OutcomeUnknown("reply lost after delivery")

            l3.execute = execute_then_lost

            # G8 → 202 UNKNOWN.
            url = EXECUTE_PATH.format(plan_id=ctx.plan_id)
            resp1 = await ctx.test_app.client.post(
                url, json={"request_id": "req_w4c_g5_rec_1"}, headers=ctx.headers
            )
            assert resp1.status_code == 202, resp1.text

            # Gateway job is UNKNOWN.
            db = engine(ctx.db_url, poolclass=NullPool)
            async with db.connect() as conn:
                job_status = await conn.scalar(
                    select(t.execution_jobs.c.status).where(
                        t.execution_jobs.c.plan_id == ctx.plan_id
                    )
                )
            await db.dispose()
            assert job_status == "UNKNOWN", f"job status={job_status}"

            # Restore real execute.
            l3.execute = real_execute

            # G5 (not G8!) → should reconcile inline and return executed result.
            g5_url = G5_PATH.format(plan_id=ctx.plan_id)
            g5_resp = await ctx.test_app.client.get(g5_url, headers=ctx.headers)
            assert g5_resp.status_code == 200, f"G5 returned {g5_resp.status_code}: {g5_resp.text}"
            sv = g5_resp.json()
            assert sv["status"] == "EXECUTED"
            assert sv["execution"] is not None
            assert sv["execution"]["status"] == "EXECUTED"

            # L3 still only has exactly one execution.
            async with ctx.l3_admin.connect() as conn:
                executed = await conn.scalar(
                    select(func.count())
                    .select_from(m.plan_executions)
                    .where(m.plan_executions.c.plan_id == ctx.plan_id)
                )
            assert executed == 1, f"L3 plan_executions={executed}, expected 1"

            # Gateway job is now terminal.
            db2 = engine(ctx.db_url, poolclass=NullPool)
            async with db2.connect() as conn:
                job_status2 = await conn.scalar(
                    select(t.execution_jobs.c.status).where(
                        t.execution_jobs.c.plan_id == ctx.plan_id
                    )
                )
            await db2.dispose()
            assert job_status2 == "EXECUTED", f"job status after G5={job_status2}"

    asyncio.run(run())


def test_g8_same_request_id_replays_terminal(app_config, obligations_db):
    """Same request_id on G8 twice → identical responses, L3 executes only once."""

    async def run():
        async with allow_plan_ctx(app_config, obligations_db) as ctx:
            url = EXECUTE_PATH.format(plan_id=ctx.plan_id)
            body = {"request_id": "req_w4c_t7_replay_1"}

            # First G8.
            resp1 = await ctx.test_app.client.post(url, json=body, headers=ctx.headers)
            assert resp1.status_code == 200, f"1st G8: {resp1.text}"

            # Second G8 with same request_id.
            resp2 = await ctx.test_app.client.post(url, json=body, headers=ctx.headers)
            assert resp2.status_code == 200, f"2nd G8: {resp2.text}"

            # Responses must be identical.
            assert resp1.json() == resp2.json()

            # L3 executed only once.
            async with ctx.l3_admin.connect() as conn:
                count = await conn.scalar(
                    select(func.count())
                    .select_from(m.plan_executions)
                    .where(m.plan_executions.c.plan_id == ctx.plan_id)
                )
            assert count == 1, f"L3 plan_executions={count}, expected 1"

            # The dedup key is scoped to this plan's concrete path, so the same
            # request_id on another plan of this customer cannot replay this one.
            gw = engine(ctx.db_url, poolclass=NullPool)
            async with gw.connect() as conn:
                routes = (
                    (
                        await conn.execute(
                            select(t.request_dedup.c.route).where(
                                t.request_dedup.c.customer_id == "cus_zs",
                                t.request_dedup.c.route.like("POST /plans/%/execute"),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
            await gw.dispose()
            assert routes == [f"POST /plans/{ctx.plan_id}/execute"], routes

    asyncio.run(run())


def test_g8_request_id_body_mismatch_conflicts(app_config, obligations_db):
    """Same request_id with different body → 409 IDEMPOTENCY_CONFLICT.

    We simulate a body mismatch by manually inserting a request_dedup row
    with a wrong request_hash before the G8 call.
    """

    async def run():
        async with allow_plan_ctx(app_config, obligations_db) as ctx:
            url = EXECUTE_PATH.format(plan_id=ctx.plan_id)
            body = {"request_id": "req_w4c_t7_mismatch"}

            # Poison request_dedup: insert a row with a mismatched request_hash.
            wrong_hash = hashlib.sha256(b"different body").hexdigest()
            db = engine(ctx.db_url, poolclass=NullPool)
            async with db.begin() as conn:
                await conn.execute(
                    t.request_dedup.insert().values(
                        customer_id="cus_zs",
                        route=ROUTE_EXECUTE.format(plan_id=ctx.plan_id),
                        request_id="req_w4c_t7_mismatch",
                        request_hash=wrong_hash,
                        status_code=0,
                        response={},
                        created_at_wall=func.now(),
                    )
                )
            await db.dispose()

            # G8 with correct body but same request_id → 409 IDEMPOTENCY_CONFLICT.
            resp = await ctx.test_app.client.post(url, json=body, headers=ctx.headers)
            assert resp.status_code == 409, (
                f"expected 409 IDEMPOTENCY_CONFLICT, got {resp.status_code}: {resp.text}"
            )
            error = resp.json()["error"]
            assert error["code"] == "IDEMPOTENCY_CONFLICT"

            # L3 must not have executed.
            async with ctx.l3_admin.connect() as conn:
                count = await conn.scalar(
                    select(func.count())
                    .select_from(m.plan_executions)
                    .where(m.plan_executions.c.plan_id == ctx.plan_id)
                )
            assert count == 0, f"L3 must not execute, got {count}"

    asyncio.run(run())


def test_g8_old_request_id_cannot_resume_after_fence(app_config, obligations_db):
    """ADR-008: old request_id must not trigger a new attempt after a fence.

    1st G8 with r1 → 409 barrier (STATE_CHANGED, fence, PAUSED with attempt=1).
    2nd G8 with r1 again → 409 STATE_CHANGED (must not trigger attempt=2).
    3rd G8 with r2 → 200 EXECUTED (attempt=2, legit resume).
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
                    async with ctx.l3_admin.begin() as conn:
                        from sqlalchemy import text

                        await conn.execute(text("UPDATE clock SET version = version + 1"))
                return await original_execute(body)

            l3.execute = bump_then_execute

            url = EXECUTE_PATH.format(plan_id=ctx.plan_id)

            # ── 1st G8 (r1): triggers fence → 409 STATE_CHANGED ──
            resp1 = await ctx.test_app.client.post(
                url, json={"request_id": "req_w4c_t7_fence_1"}, headers=ctx.headers
            )
            assert resp1.status_code == 409, (
                f"1st G8 expected 409, got {resp1.status_code}: {resp1.text}"
            )
            err1 = resp1.json()["error"]
            assert err1["code"] == "STATE_CHANGED"

            # Job is PAUSED, l3_attempt=1.
            db = engine(ctx.db_url, poolclass=NullPool)
            async with db.connect() as conn:
                job = (
                    (
                        await conn.execute(
                            select(t.execution_jobs.c.status, t.execution_jobs.c.l3_attempt).where(
                                t.execution_jobs.c.plan_id == ctx.plan_id
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                assert job["status"] == "PAUSED", f"job status={job['status']}"
                assert job["l3_attempt"] == 1, f"l3_attempt={job['l3_attempt']}"
            await db.dispose()

            # ── 2nd G8 (r1 again): refuse with STATE_CHANGED ──
            resp2 = await ctx.test_app.client.post(
                url, json={"request_id": "req_w4c_t7_fence_1"}, headers=ctx.headers
            )
            assert resp2.status_code == 409, (
                f"2nd G8 (r1 again) expected 409, got {resp2.status_code}: {resp2.text}"
            )
            err2 = resp2.json()["error"]
            assert err2["code"] == "STATE_CHANGED"

            # L3 must NOT have executed (attempt still 1, no executions).
            async with ctx.l3_admin.connect() as conn:
                _intent = (
                    (
                        await conn.execute(
                            select(m.execution_intents.c.attempt).where(
                                m.execution_intents.c.plan_id == ctx.plan_id
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
                plan_execs = await conn.scalar(
                    select(func.count())
                    .select_from(m.plan_executions)
                    .where(m.plan_executions.c.plan_id == ctx.plan_id)
                )
            # The fence prepare may have set up attempt=1 at L3.
            assert plan_execs == 0, f"L3 must not have executed, got {plan_execs}"

            # ── 3rd G8 (r2): resume with attempt 2 → 200 ──
            resp3 = await ctx.test_app.client.post(
                url, json={"request_id": "req_w4c_t7_fence_2"}, headers=ctx.headers
            )
            assert resp3.status_code == 200, (
                f"3rd G8 (r2) expected 200, got {resp3.status_code}: {resp3.text}"
            )
            result = resp3.json()
            assert result["status"] == "EXECUTED"

            # Gateway job: EXECUTED, l3_attempt=2.
            db2 = engine(ctx.db_url, poolclass=NullPool)
            async with db2.connect() as conn:
                job2 = (
                    (
                        await conn.execute(
                            select(t.execution_jobs.c.status, t.execution_jobs.c.l3_attempt).where(
                                t.execution_jobs.c.plan_id == ctx.plan_id
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                assert job2["status"] == "EXECUTED", f"job status={job2['status']}"
                assert job2["l3_attempt"] == 2, f"l3_attempt={job2['l3_attempt']}"
            await db2.dispose()

            # L3: intent attempt=2, exactly 1 execution.
            async with ctx.l3_admin.connect() as conn:
                intent2 = (
                    (
                        await conn.execute(
                            select(m.execution_intents.c.attempt).where(
                                m.execution_intents.c.plan_id == ctx.plan_id
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
                plan_execs2 = await conn.scalar(
                    select(func.count())
                    .select_from(m.plan_executions)
                    .where(m.plan_executions.c.plan_id == ctx.plan_id)
                )
            assert intent2 == 2, f"L3 intent attempt={intent2}, expected 2"
            assert plan_execs2 == 1, f"L3 plan_executions={plan_execs2}, expected 1"

    asyncio.run(run())
