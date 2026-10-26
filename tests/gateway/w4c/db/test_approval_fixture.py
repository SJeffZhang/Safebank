"""T3a: ``approval_plan_ctx`` fixture — real L3 + real G4 YELLOW plan → G9/G6 confirm.

Produces a confirmed ``update_card_limits`` (TIGHTEN) plan through the full
G4→G9→G6 chain against a real bank-backend.
"""

from __future__ import annotations

import asyncio

from safebank_contracts.enums import Risk
from sqlalchemy import select
from sqlalchemy.pool import NullPool

from services.gateway.app import tables as t
from services.gateway.app.db import engine as gw_engine
from services.gateway.app.l3.client import L3Client
from services.gateway.app.schemas.enums import ApprovalStatus, PlanStatus
from tests.gateway.w4c.db.support import approval_plan_ctx


def test_real_l3_confirmed_plan(app_config, obligations_db):
    """G4 returns REQUIRE_APPROVAL (YELLOW), then G9/G6 confirm the plan.

    Asserts: decision == REQUIRE_APPROVAL, risk == YELLOW, G6 200,
    approvals.status == CONFIRMED, plans.status == CONFIRMED,
    and the L3 client is a real L3Client (not FakeL3Client).
    """

    async def run():
        async with approval_plan_ctx(app_config, obligations_db) as ctx:
            # G4 assertions.
            assert ctx.response["decision"] == "REQUIRE_APPROVAL"
            assert ctx.response.get("risk") == Risk.YELLOW.value

            # L3 must be real.
            app = ctx.test_app.client._transport.app
            assert isinstance(app.state.l3, L3Client)

            # Verify persisted DB state.
            db = gw_engine(ctx.db_url, poolclass=NullPool)
            async with db.connect() as conn:
                approval = (
                    (
                        await conn.execute(
                            select(t.approvals).where(t.approvals.c.id == ctx.approval_id)
                        )
                    )
                    .mappings()
                    .one()
                )
                plan = (
                    (await conn.execute(select(t.plans).where(t.plans.c.id == ctx.plan_id)))
                    .mappings()
                    .one()
                )

            assert approval["status"] == ApprovalStatus.CONFIRMED.value
            assert plan["status"] == PlanStatus.CONFIRMED.value
            await db.dispose()

    asyncio.run(run())
