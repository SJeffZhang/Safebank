"""APPROVAL_PENDING and EXECUTION_RESULT notifications on the real G4/G6/G8 path."""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.pool import NullPool

from services.gateway.app import tables as t
from services.gateway.app.db import engine
from tests.gateway.w4c.db.support import allow_plan_ctx, approval_plan_ctx

EXECUTE_PATH = "/plans/{plan_id}/execute"


async def _rows(db_url):
    db = engine(db_url, poolclass=NullPool)
    async with db.connect() as conn:
        rows = (
            (
                await conn.execute(
                    select(
                        t.notifications.c.customer_id,
                        t.notifications.c.event_id,
                        t.notifications.c.type,
                        t.notifications.c.payload,
                    )
                )
            )
            .mappings()
            .all()
        )
    await db.dispose()
    return rows


def test_approval_then_execution_notify_once(app_config, obligations_db):
    async def run():
        async with approval_plan_ctx(app_config, obligations_db) as ctx:
            rows = await _rows(ctx.db_url)
            pending = [r for r in rows if r["type"] == "APPROVAL_PENDING"]
            assert [(r["customer_id"], r["event_id"]) for r in pending] == [
                ("cus_zs", ctx.approval_id)
            ]
            assert pending[0]["payload"]["related_plan_id"] == ctx.plan_id
            assert not [r for r in rows if r["type"] == "EXECUTION_RESULT"]

            url = EXECUTE_PATH.format(plan_id=ctx.plan_id)
            body = {"request_id": "req_w4c_notify"}
            for _ in range(2):
                resp = await ctx.test_app.client.post(url, json=body, headers=ctx.headers)
                assert resp.status_code == 200 and resp.json()["status"] == "EXECUTED"
            again = await ctx.test_app.client.post(
                url, json={"request_id": "req_w4c_notify_2"}, headers=ctx.headers
            )
            assert again.status_code == 200

            done = [r for r in await _rows(ctx.db_url) if r["type"] == "EXECUTION_RESULT"]
            assert [(r["customer_id"], r["event_id"]) for r in done] == [("cus_zs", ctx.plan_id)]
            assert done[0]["payload"]["outcome"] == "EXECUTED"
            assert done[0]["payload"]["title"] == "操作已完成"

    asyncio.run(run())


def test_allow_plan_notifies_execution_only(app_config, obligations_db):
    async def run():
        async with allow_plan_ctx(app_config, obligations_db) as ctx:
            url = EXECUTE_PATH.format(plan_id=ctx.plan_id)
            resp = await ctx.test_app.client.post(
                url, json={"request_id": "req_w4c_allow_notify"}, headers=ctx.headers
            )
            assert resp.status_code == 200 and resp.json()["status"] == "EXECUTED"
            rows = await _rows(ctx.db_url)
            assert [r["type"] for r in rows] == ["EXECUTION_RESULT"]
            assert rows[0]["event_id"] == ctx.plan_id

    asyncio.run(run())
