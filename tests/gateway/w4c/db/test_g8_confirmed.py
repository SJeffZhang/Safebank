"""W4c T3b: G8 ``POST /plans/{plan_id}/execute`` CONFIRMED path — red tests.

G8 currently returns 503 for CONFIRMED plans, so all three tests are expected
to fail.
"""

from __future__ import annotations

import asyncio

from app import models as m
from sqlalchemy import func, select, text
from sqlalchemy.pool import NullPool

from services.gateway.app import tables as t
from services.gateway.app.db import engine
from tests.gateway.w4c.db.support import approval_plan_ctx

EXECUTE_PATH = "/plans/{plan_id}/execute"


def test_g8_confirmed_executes(app_config, obligations_db):
    """G8 executes the CONFIRMED plan and the real L3 card limit is tightened.

    After execution:
    * G8 returns 200 with status ``EXECUTED``.
    * Real L3 ``crd_zs_1`` single_limit_cents == 100000.
    * Gateway ``approvals`` status == ``USED``.
    * Gateway ``plans`` status == ``EXECUTED``.
    * Real L3 ``plan_executions`` count == 1.
    """

    async def run():
        async with approval_plan_ctx(app_config, obligations_db) as ctx:
            url = EXECUTE_PATH.format(plan_id=ctx.plan_id)
            body = {"request_id": "req_w4c_conf_exec"}

            resp = await ctx.test_app.client.post(url, json=body, headers=ctx.headers)
            assert resp.status_code == 200, f"G8 returned {resp.status_code}: {resp.text}"
            result = resp.json()
            assert result["status"] == "EXECUTED", f"unexpected status: {result}"

            # Real L3: card single_limit_cents == 100000.
            async with ctx.l3_admin.connect() as conn:
                row = (
                    await conn.execute(
                        select(m.cards.c.single_limit_cents).where(m.cards.c.id == "crd_zs_1")
                    )
                ).one()
            assert row[0] == 100000, f"card single_limit_cents={row[0]}, expected 100000"

            # Gateway: approval status == USED.
            db = engine(ctx.db_url, poolclass=NullPool)
            async with db.connect() as conn:
                approval_status = await conn.scalar(
                    select(t.approvals.c.status).where(t.approvals.c.id == ctx.approval_id)
                )
            assert approval_status == "USED", f"approval status={approval_status}"
            await db.dispose()

            # Gateway: plan status == EXECUTED.
            db2 = engine(ctx.db_url, poolclass=NullPool)
            async with db2.connect() as conn:
                plan_status = await conn.scalar(
                    select(t.plans.c.status).where(t.plans.c.id == ctx.plan_id)
                )
            assert plan_status == "EXECUTED", f"plan status={plan_status}"
            await db2.dispose()

            # Real L3: plan_executions count == 1.
            async with ctx.l3_admin.connect() as conn:
                count = await conn.scalar(
                    select(func.count())
                    .select_from(m.plan_executions)
                    .where(m.plan_executions.c.plan_id == ctx.plan_id)
                )
            assert count == 1, f"L3 plan_executions count={count}, expected 1"

    asyncio.run(run())


def test_g8_confirmed_view_changed_is_not_executed(app_config, obligations_db):
    """G8 rejects execution when the real L3 state has changed since evaluation.

    Lowering ``crd_zs_1``'s current single limit from 500000 to 300000 before
    calling G8 keeps the plan a yellow tightening (still REQUIRE_APPROVAL) but
    changes what the panel shows (the "before" value), so the re-evaluated
    view_hash differs from the confirmed one: G8 must return 409
    ``STATE_CHANGED`` and must not execute. This isolates the view_hash guard;
    freezing the card would not, since the panel does not show card status.
    """

    async def run():
        async with approval_plan_ctx(app_config, obligations_db) as ctx:
            # Mutate real L3: same action and risk, different visible "before".
            async with ctx.l3_admin.begin() as conn:
                await conn.execute(
                    text("UPDATE cards SET single_limit_cents = 300000 WHERE id = 'crd_zs_1'")
                )

            url = EXECUTE_PATH.format(plan_id=ctx.plan_id)
            body = {"request_id": "req_w4c_conf_changed"}

            resp = await ctx.test_app.client.post(url, json=body, headers=ctx.headers)
            assert resp.status_code == 409, f"G8 returned {resp.status_code}: {resp.text}"
            error = resp.json()["error"]
            assert error["code"] == "STATE_CHANGED", f"error code={error.get('code')}"

            # Real L3: plan_executions count == 0.
            async with ctx.l3_admin.connect() as conn:
                count = await conn.scalar(
                    select(func.count())
                    .select_from(m.plan_executions)
                    .where(m.plan_executions.c.plan_id == ctx.plan_id)
                )
            assert count == 0, f"L3 plan_executions count={count}, expected 0"

            # Card limit untouched by G8 (still the externally set 300000).
            async with ctx.l3_admin.connect() as conn:
                row = (
                    await conn.execute(
                        select(m.cards.c.single_limit_cents).where(m.cards.c.id == "crd_zs_1")
                    )
                ).one()
            assert row[0] == 300000, f"card single_limit_cents={row[0]}, expected 300000"

    asyncio.run(run())


def test_g8_confirmed_rejects_approval_not_confirmed(app_config, obligations_db):
    """G8 rejects execution when the approval is no longer CONFIRMED.

    If the approval has been REJECTED before G8 is called, G8 must return 409
    and the plan must not reach L3.
    """

    async def run():
        async with approval_plan_ctx(app_config, obligations_db) as ctx:
            # Mutate gateway: mark the approval as REJECTED.
            db = engine(ctx.db_url, poolclass=NullPool)
            async with db.begin() as conn:
                await conn.execute(
                    text("UPDATE approvals SET status = 'REJECTED' WHERE id = :id"),
                    {"id": ctx.approval_id},
                )
            await db.dispose()

            url = EXECUTE_PATH.format(plan_id=ctx.plan_id)
            body = {"request_id": "req_w4c_conf_rejected"}

            resp = await ctx.test_app.client.post(url, json=body, headers=ctx.headers)
            assert resp.status_code == 409, f"G8 returned {resp.status_code}: {resp.text}"

            # Real L3: plan_executions count == 0.
            async with ctx.l3_admin.connect() as conn:
                count = await conn.scalar(
                    select(func.count())
                    .select_from(m.plan_executions)
                    .where(m.plan_executions.c.plan_id == ctx.plan_id)
                )
            assert count == 0, f"L3 plan_executions count={count}, expected 0"

    asyncio.run(run())
