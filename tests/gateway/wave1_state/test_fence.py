"""Tests of database conditions only; local PAUSED is no L3 ACK proof."""

import asyncio
from dataclasses import replace

import pytest
from sqlalchemy import select
from sqlalchemy.pool import NullPool

from services.gateway.app import state
from services.gateway.app import tables as t
from services.gateway.app.db import engine
from services.gateway.app.errors import GatewayError
from tests.gateway.wave1_state.helpers import seed


def test_enter_fence_ack_cas_and_claim_retention(gateway_db):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        async with db.begin() as conn:
            expected, evidence, _, cap = await seed(conn)
        async with db.begin() as conn:
            await state.begin_fencing(conn, expected=expected)
        async with db.begin() as conn:
            await state.acknowledge_fence(conn, expected=expected)
            await state.guard_paused_execution(conn, expected=expected)
        async with db.connect() as conn:
            job = (
                (
                    await conn.execute(
                        select(t.execution_jobs).where(
                            t.execution_jobs.c.plan_id == expected.plan_id
                        )
                    )
                )
                .mappings()
                .one()
            )
            assert job["status"] == "PAUSED" and job["deadline_wall"] == state.FENCE_AT
            assert job["l3_attempt"] == 1 and job["deadline_cap_wall"] == cap
            assert job["authorization_evidence"] == evidence.model_dump(mode="json")
            assert (
                await conn.scalar(
                    select(t.authorization_claims.c.status).where(
                        t.authorization_claims.c.plan_id == expected.plan_id
                    )
                )
                == "CLAIMED"
            )
        for method in (state.begin_fencing, state.acknowledge_fence):
            async with db.begin() as conn:
                with pytest.raises(GatewayError) as err:
                    await method(conn, expected=expected)
                assert err.value.status == 409
        await db.dispose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "field,value",
    [
        ("customer_id", "other"),
        ("plan_id", "other"),
        ("origin", "app_form"),
        ("attempt", 2),
        ("revision", 2),
    ],
)
def test_fence_rejects_wrong_identity_and_revision(gateway_db, field, value):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        async with db.begin() as conn:
            expected, *_ = await seed(conn)
        async with db.begin() as conn:
            with pytest.raises(GatewayError):
                await state.begin_fencing(conn, expected=replace(expected, **{field: value}))
        await db.dispose()

    asyncio.run(run())


def test_competing_fence_only_one_succeeds(gateway_db):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        async with db.begin() as conn:
            expected, *_ = await seed(conn)

        async def attempt():
            try:
                async with db.begin() as conn:
                    await state.begin_fencing(conn, expected=expected)
                return "success"
            except GatewayError:
                return "conflict"

        assert sorted(await asyncio.gather(attempt(), attempt())) == ["conflict", "success"]
        await db.dispose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "mutation", ["no_evidence", "no_cap", "claim_consumed", "not_paused", "wrong_deadline"]
)
def test_paused_guard_fails_closed(gateway_db, mutation):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        async with db.begin() as conn:
            expected, *_ = await seed(conn, job_status="PAUSED")
            values = {"deadline_wall": state.FENCE_AT}
            if mutation == "no_evidence":
                values["authorization_evidence"] = None
            elif mutation == "no_cap":
                values["deadline_cap_wall"] = None
            elif mutation == "not_paused":
                values["status"] = "FENCING"
            elif mutation == "wrong_deadline":
                values = {}
            await conn.execute(
                t.execution_jobs.update()
                .where(t.execution_jobs.c.plan_id == expected.plan_id)
                .values(**values)
            ) if values else None
            if mutation == "claim_consumed":
                await conn.execute(
                    t.authorization_claims.update()
                    .where(t.authorization_claims.c.plan_id == expected.plan_id)
                    .values(status="CONSUMED")
                )
        async with db.begin() as conn:
            with pytest.raises(GatewayError):
                await state.guard_paused_execution(conn, expected=expected)
        await db.dispose()

    asyncio.run(run())
