"""Synthetic consent is installed explicitly and atomically, only into a new DB."""

import asyncio

import pytest
from safebank_bankcore.grants import instruction_scope, scope_hash, seed_consent
from sqlalchemy import func, select
from sqlalchemy.pool import NullPool

from services.gateway.app import tables as t
from services.gateway.app.db import engine
from services.gateway.app.seed_grants import install
from tests.gateway.s1.conftest import build_app, make_instruction


def grant():
    scope = instruction_scope(make_instruction("si_seed", source_plan_id="pln_demo_seed"))
    consent = seed_consent(scope, "2026-11-24T12:00:00+08:00")
    return dict(
        instruction_id="si_seed",
        customer_id="cus_a",
        source_plan_id="pln_demo_seed",
        scope_json={"seed_consent": consent},
        scope_hash=scope_hash(consent),
        approved_risk="YELLOW",
        status="ACTIVE",
    )


def test_explicit_install_audits_and_cannot_reinstall(app_config):
    async def run():
        app = await build_app(app_config)
        db = engine(app.db_url, poolclass=NullPool)
        async with db.begin() as conn:
            await install(conn, [grant()])
        async with db.connect() as conn:
            assert await conn.scalar(select(func.count()).select_from(t.scheduled_grants)) == 1
            assert await conn.scalar(select(func.count()).select_from(t.audit_events)) == 1
        with pytest.raises(ValueError, match="empty"):
            async with db.begin() as conn:
                await install(conn, [grant()])
        await db.dispose()

    asyncio.run(run())


def test_mismatched_manifest_has_no_partial_grant_or_audit(app_config):
    async def run():
        app = await build_app(app_config)
        db = engine(app.db_url, poolclass=NullPool)
        bad = {**grant(), "instruction_id": "si_different"}
        with pytest.raises(ValueError, match="binding"):
            async with db.begin() as conn:
                await install(conn, [grant(), bad])
        async with db.connect() as conn:
            assert await conn.scalar(select(func.count()).select_from(t.scheduled_grants)) == 0
            assert await conn.scalar(select(func.count()).select_from(t.audit_events)) == 0
        await db.dispose()

    asyncio.run(run())
