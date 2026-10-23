import asyncio

import pytest
from safebank_contracts.codes import Code
from sqlalchemy import text
from sqlalchemy.pool import NullPool

from services.gateway.app import tables as t
from services.gateway.app.db import engine
from services.gateway.app.errors import GatewayError
from services.gateway.app.state import (
    APPROVAL_TRANSITIONS,
    PLAN_TRANSITIONS,
    UTTERANCE_TRANSITIONS,
    legal,
    transition,
)


def _runtime(gateway_db):
    return engine(gateway_db.runtime_url, poolclass=NullPool)


def _insert(db, table, **values):
    async def run():
        async with db.begin() as conn:
            await conn.execute(table.insert().values(**values))

    asyncio.run(run())


def test_legal_transition_tables_are_acyclic():
    assert "EXECUTED" not in PLAN_TRANSITIONS["EVALUATED"]
    assert "PENDING" not in APPROVAL_TRANSITIONS["CONFIRMED"]
    assert "ACTIVE" not in UTTERANCE_TRANSITIONS["CONSUMED"]


def test_legal_helper():
    assert legal("plans", "EVALUATED", "PENDING_APPROVAL")
    assert not legal("plans", "EXECUTED", "FAILED")
    assert legal("approvals", "PENDING", "CONFIRMED")
    assert legal("utterances", "ACTIVE", "CONSUMED")


def test_transition_applies_and_rejects(gateway_db):
    async def run():
        db = _runtime(gateway_db)
        async with db.begin() as conn:
            await conn.execute(
                t.sessions.insert().values(
                    id="ses_1", customer_id="cus_1", created_at_wall=text("now()")
                )
            )
            await conn.execute(
                t.plans.insert().values(
                    id="pln_s1",
                    customer_id="cus_1",
                    sid="ses_1",
                    origin="agent",
                    request_id="req_s1",
                    request_hash="h",
                    plan_hash="h",
                    steps_hash="h",
                    resolved_steps=[],
                    utterance_ids=[],
                    scope_ids=[],
                    status="EVALUATED",
                    created_at_wall=text("now()"),
                )
            )
        async with db.begin() as conn:
            await transition(
                conn, t.plans, id_="pln_s1", from_statuses=["EVALUATED"], to="PENDING_APPROVAL"
            )
        async with db.connect() as conn:
            status = await conn.scalar(
                t.plans.select().where(t.plans.c.id == "pln_s1").with_only_columns(t.plans.c.status)
            )
            assert status == "PENDING_APPROVAL"
        # Re-transition from the wrong source must fail.
        async with db.begin() as conn:
            with pytest.raises(GatewayError) as exc:
                await transition(
                    conn, t.plans, id_="pln_s1", from_statuses=["EVALUATED"], to="CONFIRMED"
                )
            assert exc.value.code is Code.STATE_CHANGED
        await db.dispose()

    asyncio.run(run())
