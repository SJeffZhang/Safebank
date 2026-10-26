"""T2d: plan_draft persistence through G4 and upgrade from 0002."""

import asyncio
import os
import subprocess
import sys

import pytest
from safebank_contracts.plan import PlanDraft
from sqlalchemy import text
from sqlalchemy.pool import NullPool

from services.gateway.app.db import GATEWAY_REVISION, engine, ready
from tests.gateway.db.conftest import ROOT, _provision


def test_g4_persists_plan_draft(app_config, obligations_db):
    """G4 evaluate writes plan_draft and the stored value round-trips through PlanDraft."""
    from tests.gateway.w4c.db.support import allow_plan_ctx

    async def run():
        async with allow_plan_ctx(app_config, obligations_db) as ctx:
            db = engine(ctx.db_url, poolclass=NullPool)
            async with db.connect() as conn:
                row = (
                    (
                        await conn.execute(
                            text("SELECT plan_draft, resolved_steps FROM plans WHERE id = :pid"),
                            {"pid": ctx.plan_id},
                        )
                    )
                    .mappings()
                    .one()
                )
            await db.dispose()

        assert row.plan_draft is not None, "G4 must persist plan_draft"
        restored = PlanDraft.model_validate(row.plan_draft)
        # The stored plan_draft must have the same step tools as the submitted plan.
        assert len(restored.steps) == 1
        assert restored.steps[0].tool == "freeze_card"
        assert len(restored.steps[0].arg_sources) == 1
        assert restored.steps[0].arg_sources[0].param == "card_id"

    asyncio.run(run())


def test_upgrade_0002_to_0003_keeps_rows():
    """Upgrade from 0002 to 0003 preserves existing plans rows with plan_draft=NULL."""
    if os.getenv("GATEWAY_DB_TESTS") != "1":
        pytest.skip("requires isolated PostgreSQL")
    old = _provision("0002_wave1")

    async def run():
        db = engine(old.admin_url, poolclass=NullPool)
        assert not await ready(db)
        async with db.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO sessions(id,customer_id,created_at_wall) "
                    "VALUES('old_s','old_c',now())"
                )
            )
            await conn.execute(
                text(
                    "INSERT INTO "
                    "plans(id,customer_id,sid,origin,request_id,request_hash,plan_hash,"
                    "steps_hash,resolved_steps,status,created_at_wall) "
                    "VALUES('old_p','old_c','old_s','agent','old_r','h','h','h','[]',"
                    "'EXECUTING',now())"
                )
            )
        await db.dispose()

        env = {
            **os.environ,
            "GATEWAY_MIGRATION_DATABASE_URL": old.admin_url,
            "PYTHONPATH": os.pathsep.join(
                str(ROOT / p) for p in (".", "packages/contracts/src", "packages/bankcore/src")
            ),
        }
        for _ in range(2):
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "alembic",
                    "-c",
                    "services/gateway/alembic.ini",
                    "upgrade",
                    "head",
                ],
                cwd=ROOT,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )

        db = engine(old.admin_url, poolclass=NullPool)
        assert await ready(db)
        async with db.connect() as conn:
            row = (
                (
                    await conn.execute(
                        text("SELECT id, customer_id, plan_draft FROM plans WHERE id = 'old_p'")
                    )
                )
                .mappings()
                .one()
            )
            assert row.customer_id == "old_c"
            assert row.plan_draft is None, (
                "existing row upgraded from 0002 must have NULL plan_draft"
            )
        await db.dispose()

    try:
        asyncio.run(run())
    finally:
        subprocess.run(["docker", "rm", "-f", old.name], check=True, capture_output=True)

    assert GATEWAY_REVISION == "0003_plan_draft"
