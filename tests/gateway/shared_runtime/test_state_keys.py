import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from safebank_contracts.codes import Code
from sqlalchemy import Column, MetaData, String, Table, select
from sqlalchemy.pool import NullPool

from services.gateway.app import tables as t
from services.gateway.app.db import engine
from services.gateway.app.errors import GatewayError
from services.gateway.app.state import legal, transition


def test_claimed_scope_can_be_consumed_but_never_reclaimed():
    assert legal("authorization_claims", "CLAIMED", "CONSUMED")
    assert not legal("authorization_claims", "CONSUMED", "CLAIMED")


@pytest.mark.parametrize("count", [0, 2])
def test_status_entry_rejects_non_single_primary_keys_before_dml(count):
    table = Table(
        "execution_jobs",
        MetaData(),
        *(Column(f"key_{i}", String, primary_key=True) for i in range(count)),
        Column("status", String),
    )

    async def run():
        with pytest.raises(ValueError, match="single-column primary key"):
            await transition(None, table, id_="value", from_statuses=["EXECUTING"], to="UNKNOWN")

    asyncio.run(run())


def test_execution_and_claim_statuses_address_actual_primary_keys(gateway_db):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        sid, plan, scope = (f"{prefix}_{uuid4().hex}" for prefix in ("ses", "pln", "scope"))
        now = datetime.now(UTC)
        try:
            async with db.begin() as conn:
                await conn.execute(
                    t.sessions.insert().values(id=sid, customer_id="cus_key", created_at_wall=now)
                )
                await conn.execute(
                    t.plans.insert().values(
                        id=plan,
                        customer_id="cus_key",
                        sid=sid,
                        origin="agent",
                        request_id=plan,
                        request_hash="h",
                        plan_hash="h",
                        steps_hash="h",
                        resolved_steps=[],
                        utterance_ids=[],
                        scope_ids=[],
                        status="EVALUATED",
                        created_at_wall=now,
                    )
                )
                await conn.execute(
                    t.execution_jobs.insert().values(
                        plan_id=plan,
                        status="EXECUTING",
                        l3_attempt=1,
                        created_at_wall=now,
                        updated_at_wall=now,
                    )
                )
                await conn.execute(
                    t.authorization_claims.insert().values(
                        scope_id=scope, plan_id=plan, status="CLAIMED"
                    )
                )
            async with db.begin() as conn:
                await transition(
                    conn, t.execution_jobs, id_=plan, from_statuses=["EXECUTING"], to="UNKNOWN"
                )
                await transition(
                    conn,
                    t.authorization_claims,
                    id_=scope,
                    from_statuses=["CLAIMED"],
                    to="CONSUMED",
                )
            async with db.begin() as conn:
                assert (
                    await conn.scalar(
                        select(t.execution_jobs.c.status).where(t.execution_jobs.c.plan_id == plan)
                    )
                    == "UNKNOWN"
                )
                assert (
                    await conn.scalar(
                        select(t.authorization_claims.c.status).where(
                            t.authorization_claims.c.scope_id == scope
                        )
                    )
                    == "CONSUMED"
                )
                with pytest.raises(GatewayError) as exc:
                    await transition(
                        conn,
                        t.authorization_claims,
                        id_=scope,
                        from_statuses=["CLAIMED"],
                        to="CONSUMED",
                    )
                assert exc.value.status == 409 and exc.value.code is Code.STATE_CHANGED
                with pytest.raises(ValueError):
                    await transition(
                        conn,
                        t.authorization_claims,
                        id_=scope,
                        from_statuses=["CONSUMED"],
                        to="CLAIMED",
                    )
        finally:
            await db.dispose()

    asyncio.run(run())
