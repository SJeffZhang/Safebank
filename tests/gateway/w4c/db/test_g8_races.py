"""W4c T6: G8 concurrency, revocation, late-arrival and origin-constraint.

F7: concurrent G8 (same plan, different request_id) → exactly one commits.
F8: revocation during execution aborts before L3.
F9: late G8 after terminal returns replayed result.
F10: F+app_form caller cannot execute an agent-origin plan.

Red-first notes per test in individual commit messages.
"""

from __future__ import annotations

import asyncio

from app import models as m
from sqlalchemy import func, select, text
from sqlalchemy.pool import NullPool

from services.gateway.app import tables as t
from services.gateway.app.auth.tokens import APP_AUDIENCE, sign_jwt
from services.gateway.app.db import engine as gw_engine
from tests.gateway.w3b.db.conftest import SERVICE_TOKEN, _seed_auth
from tests.gateway.w4c.db.support import allow_plan_ctx

EXECUTE_PATH = "/plans/{plan_id}/execute"


# ── F7: concurrent G8 executes only once ──────────────────────────────────────


def test_concurrent_g8_executes_once(app_config, obligations_db):
    """Two concurrent G8 calls with different request_ids.

    Exactly one results in a real L3 execution; the other either gets
    409 EXECUTION_IN_PROGRESS or 200 with replayed=True.
    L3 plan_executions == 1.
    """

    async def run():
        async with allow_plan_ctx(app_config, obligations_db) as ctx:
            url = EXECUTE_PATH.format(plan_id=ctx.plan_id)

            async def do_g8(request_id: str):
                body = {"request_id": request_id}
                return await ctx.test_app.client.post(url, json=body, headers=ctx.headers)

            results = await asyncio.gather(
                do_g8("req_w4c_race_a"),
                do_g8("req_w4c_race_b"),
            )

            statuses = [r.status_code for r in results]
            assert 200 in statuses, f"expected at least one 200, got {statuses}"

            # Exactly one non-replayed 200 expected (the first committer).
            non_replayed = [
                r for r in results if r.status_code == 200 and not r.json().get("replayed")
            ]
            assert len(non_replayed) == 1, (
                f"expected exactly one non-replayed 200, got {len(non_replayed)}: "
                f"{[r.json() for r in results]}"
            )

            # All successes agree on status.
            for r in results:
                if r.status_code == 200:
                    assert r.json()["status"] == "EXECUTED"

            # L3 committed only once.
            async with ctx.l3_admin.connect() as conn:
                count = await conn.scalar(
                    select(func.count())
                    .select_from(m.plan_executions)
                    .where(m.plan_executions.c.plan_id == ctx.plan_id)
                )
            assert count == 1, f"L3 plan_executions count={count}, expected 1"

    asyncio.run(run())


# ── F8: revocation during execution aborts ────────────────────────────────────


def test_revocation_during_execution_aborts(app_config, obligations_db):
    """Revoke the session inside l3.snapshot (mid-replay, before write tx).

    The gateway write transaction must re-check revoked_at_wall and abort.
    G8 must not reach L3 prepare; L3 has zero execution rows.
    """

    async def run():
        async with allow_plan_ctx(app_config, obligations_db) as ctx:
            fastapi_app = ctx.test_app.client._transport.app
            original_snapshot = fastapi_app.state.l3.snapshot

            async def revoking_snapshot(payload):
                # Revoke the session from a separate connection mid-replay.
                gw_db = gw_engine(ctx.db_url, poolclass=NullPool)
                async with gw_db.begin() as conn:
                    await conn.execute(
                        text("UPDATE sessions SET revoked_at_wall = now() WHERE id = :sid"),
                        {"sid": "ses_w4c_allow"},
                    )
                await gw_db.dispose()
                return await original_snapshot(payload)

            fastapi_app.state.l3.snapshot = revoking_snapshot

            try:
                url = EXECUTE_PATH.format(plan_id=ctx.plan_id)
                body = {"request_id": "req_w4c_f8_revoke"}
                resp = await ctx.test_app.client.post(url, json=body, headers=ctx.headers)

                assert resp.status_code in (401, 403, 409), (
                    f"expected 401/403/409, got {resp.status_code}: {resp.text}"
                )

                # L3 must have zero execution rows.
                async with ctx.l3_admin.connect() as conn:
                    intent_count = await conn.scalar(
                        select(func.count())
                        .select_from(m.execution_intents)
                        .where(m.execution_intents.c.plan_id == ctx.plan_id)
                    )
                    assert intent_count == 0, f"L3 intents={intent_count}, expected 0"

                    exec_count = await conn.scalar(
                        select(func.count())
                        .select_from(m.plan_executions)
                        .where(m.plan_executions.c.plan_id == ctx.plan_id)
                    )
                    assert exec_count == 0, f"L3 executions={exec_count}, expected 0"

                # Gateway must have no execution_jobs.
                gw_db = gw_engine(ctx.db_url, poolclass=NullPool)
                async with gw_db.connect() as conn:
                    job_count = await conn.scalar(
                        select(func.count())
                        .select_from(t.execution_jobs)
                        .where(t.execution_jobs.c.plan_id == ctx.plan_id)
                    )
                    assert job_count == 0, f"gateway jobs={job_count}, expected 0"
                await gw_db.dispose()
            finally:
                fastapi_app.state.l3.snapshot = original_snapshot

    asyncio.run(run())


# ── F9: late G8 returns replayed ─────────────────────────────────────────────


def test_late_g8_after_terminal_is_replayed(app_config, obligations_db):
    """After a successful first G8, a second G8 with a new request_id returns
    200 with replayed=True and the same step results.  L3 is only touched once.
    """

    async def run():
        async with allow_plan_ctx(app_config, obligations_db) as ctx:
            url = EXECUTE_PATH.format(plan_id=ctx.plan_id)

            # First execution — committed.
            body1 = {"request_id": "req_w4c_f9_first"}
            resp1 = await ctx.test_app.client.post(url, json=body1, headers=ctx.headers)
            assert resp1.status_code == 200, f"first G8: {resp1.status_code} {resp1.text}"
            result1 = resp1.json()
            assert result1["status"] == "EXECUTED"

            # Second execution, new request_id.
            body2 = {"request_id": "req_w4c_f9_second"}
            resp2 = await ctx.test_app.client.post(url, json=body2, headers=ctx.headers)
            assert resp2.status_code == 200, f"second G8: {resp2.status_code} {resp2.text}"
            result2 = resp2.json()
            assert result2["replayed"] is True, (
                f"expected replayed=True, got {result2.get('replayed')}"
            )
            assert result2["status"] == result1["status"]

            # L3 only committed once.
            async with ctx.l3_admin.connect() as conn:
                count = await conn.scalar(
                    select(func.count())
                    .select_from(m.plan_executions)
                    .where(m.plan_executions.c.plan_id == ctx.plan_id)
                )
            assert count == 1, f"L3 plan_executions count={count}, expected 1"

    asyncio.run(run())


# ── F10: F+app_form cannot execute agent-origin plan ─────────────────────────


def test_frontend_caller_cannot_execute_agent_plan(app_config, obligations_db):
    """An F+app_form caller trying to execute an agent-origin plan gets 403/404.

    L3 sees no execution at all.
    """

    async def run():
        async with allow_plan_ctx(app_config, obligations_db) as ctx:
            fastapi_app = ctx.test_app.client._transport.app

            # Build F headers with jti seeded into auth_sessions.
            f_jti = "jti-w4c-f10-f"
            gw_db = gw_engine(ctx.db_url, poolclass=NullPool)
            async with gw_db.begin() as conn:
                await _seed_auth(conn, jti=f_jti, customer="cus_zs")
            await gw_db.dispose()

            f_jwt = sign_jwt(
                fastapi_app.private_key_pem,
                sub="cus_zs",
                aud=APP_AUDIENCE,
                scope=["app", "approve"],
                sid=None,
                utts=None,
                jti=f_jti,
            )
            f_headers = {
                "X-Service-Token": SERVICE_TOKEN,
                "Authorization": f"Bearer {f_jwt}",
            }

            url = EXECUTE_PATH.format(plan_id=ctx.plan_id)
            body = {"request_id": "req_w4c_f10_f"}
            resp = await ctx.test_app.client.post(url, json=body, headers=f_headers)

            assert resp.status_code in (403, 404), (
                f"expected 403/404, got {resp.status_code}: {resp.text}"
            )

            # L3 has zero execution rows.
            async with ctx.l3_admin.connect() as conn:
                count = await conn.scalar(
                    select(func.count())
                    .select_from(m.plan_executions)
                    .where(m.plan_executions.c.plan_id == ctx.plan_id)
                )
            assert count == 0, f"L3 plan_executions count={count}, expected 0"

    asyncio.run(run())
