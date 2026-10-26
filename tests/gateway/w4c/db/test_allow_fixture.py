"""W4c T2a: prove the real-L3 ALLOW fixture yields decision=ALLOW."""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.pool import NullPool

from services.gateway.app import tables as t
from services.gateway.app.db import engine
from services.gateway.app.l3.client import L3Client
from tests.gateway.w3b.db.conftest import FakeL3Client
from tests.gateway.w4c.db.support import allow_plan


def test_real_l3_allow_plan(app_config, obligations_db):
    result = allow_plan(app_config, obligations_db)

    # 1. G4 decided ALLOW on the GREEN freeze_card plan.
    assert result.response["decision"] == "ALLOW", (
        f"{result.response.get('decision')}: {result.response.get('findings')}"
    )

    # 2. The app's L3 is a real client, not the fake.
    assert isinstance(result.app.state.l3, L3Client)
    assert not isinstance(result.app.state.l3, FakeL3Client)

    # 3. The gateway plans table holds the evaluated plan.
    plan_id = result.response["plan_id"]

    async def run():
        db = engine(result.db_url, poolclass=NullPool)
        async with db.connect() as conn:
            row = (
                (await conn.execute(select(t.plans).where(t.plans.c.id == plan_id)))
                .mappings()
                .one()
            )
        await db.dispose()
        return row["status"]

    assert asyncio.run(run()) == "EVALUATED"
