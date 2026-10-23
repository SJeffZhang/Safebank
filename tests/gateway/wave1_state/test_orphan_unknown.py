"""Orphan cleanup is restrictive persistence, never new execution permission."""

import asyncio
from dataclasses import FrozenInstanceError, replace

import pytest
from safebank_contracts.codes import Code
from sqlalchemy import func, select
from sqlalchemy.pool import NullPool

from services.gateway.app import state
from services.gateway.app import tables as t
from services.gateway.app.db import engine
from services.gateway.app.errors import GatewayError
from tests.gateway.wave1_state.orphan_helpers import identity, orphan, snapshot


@pytest.mark.parametrize("status", ["EXECUTING", "FENCING", "PAUSED", "UNKNOWN"])
@pytest.mark.parametrize("legacy_null", [True, False])
def test_orphan_unknown_retains_claim_and_all_submission_fields(gateway_db, status, legacy_null):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            async with db.begin() as conn:
                ids, _ = await orphan(conn, job_status=status, legacy_null=legacy_null)
                before = await snapshot(conn, ids)
            async with db.begin() as conn:
                assert (
                    await state.mark_orphan_execution_unknown(
                        conn,
                        expected=identity(ids),
                        from_status=state.ExecutionJobStatus(status),
                        reason={"reason": "cancelled"},
                    )
                    is None
                )
            async with db.connect() as conn:
                after = await snapshot(conn, ids)
                assert after["plan"].pop("status") == "UNKNOWN"
                assert after["plan"] == {k: v for k, v in before["plan"].items() if k != "status"}
                assert after["job"].pop("status") == "UNKNOWN"
                assert after["job"].pop("last_error") == {"reason": "cancelled"}
                assert after["job"].pop("updated_at_wall") >= before["job"]["updated_at_wall"]
                assert after["job"] == {
                    k: v
                    for k, v in before["job"].items()
                    if k not in {"status", "last_error", "updated_at_wall"}
                }
                assert after["claims"] == before["claims"]
                assert after["utterance"] == before["utterance"]
                assert (
                    await conn.scalar(
                        select(func.count())
                        .select_from(t.evaluations)
                        .where(t.evaluations.c.plan_id == ids["pln"])
                    )
                    == 0
                )
        finally:
            await db.dispose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_customer",
        "wrong_plan",
        "wrong_origin",
        "wrong_attempt",
        "wrong_status",
        "terminal_plan",
        "missing_claim",
        "consumed_claim",
        "extra_claim",
        "wrong_scope",
        "missing_scopes",
        "evaluation_exists",
    ],
)
def test_invalid_orphan_unknown_has_no_partial_writes(gateway_db, mutation):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            async with db.begin() as conn:
                ids, now = await orphan(conn)
                expected = identity(ids)
                fields = {
                    "wrong_customer": ("customer_id", "other"),
                    "wrong_plan": ("plan_id", "other"),
                    "wrong_origin": ("origin", "app_form"),
                    "wrong_attempt": ("attempt", 4),
                }
                if mutation in fields:
                    key, value = fields[mutation]
                    expected = replace(expected, **{key: value})
                if mutation == "terminal_plan":
                    await conn.execute(
                        t.plans.update().where(t.plans.c.id == ids["pln"]).values(status="FAILED")
                    )
                if mutation == "missing_claim":
                    await conn.execute(
                        t.authorization_claims.delete().where(
                            t.authorization_claims.c.plan_id == ids["pln"]
                        )
                    )
                if mutation == "consumed_claim":
                    await conn.execute(
                        t.authorization_claims.update()
                        .where(t.authorization_claims.c.plan_id == ids["pln"])
                        .values(status="CONSUMED")
                    )
                if mutation == "extra_claim":
                    await conn.execute(
                        t.authorization_claims.insert().values(
                            scope_id="extra_" + ids["utt"], plan_id=ids["pln"], status="CLAIMED"
                        )
                    )
                if mutation in {"wrong_scope", "missing_scopes"}:
                    await conn.execute(
                        t.plans.update()
                        .where(t.plans.c.id == ids["pln"])
                        .values(scope_ids=["other"] if mutation == "wrong_scope" else [])
                    )
                if mutation == "evaluation_exists":
                    await conn.execute(
                        t.evaluations.insert().values(
                            id="evl_" + ids["pln"],
                            plan_id=ids["pln"],
                            revision=1,
                            decision={},
                            risk="GREEN",
                            policy_version="v1",
                            created_at_wall=now,
                        )
                    )
                before = await snapshot(conn, ids)
            async with db.begin() as conn:
                with pytest.raises(GatewayError) as err:
                    await state.mark_orphan_execution_unknown(
                        conn,
                        expected=expected,
                        from_status=state.ExecutionJobStatus.FENCING
                        if mutation == "wrong_status"
                        else state.ExecutionJobStatus.EXECUTING,
                        reason={},
                    )
                assert err.value.code is Code.STATE_CHANGED and err.value.status == 409
                assert await snapshot(conn, ids) == before
        finally:
            await db.dispose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "change",
    [
        {"attempt": True},
        {"attempt": 0},
        {"attempt": 1.0},
        {"origin": "other"},
        {"origin": []},
        {"customer_id": ""},
        {"plan_id": ""},
    ],
)
def test_orphan_identity_rejects_invalid_program_inputs(change):
    with pytest.raises(ValueError):
        state.OrphanExecutionIdentity(
            **{
                "customer_id": "cus",
                "plan_id": "pln",
                "origin": "agent",
                "attempt": 1,
                **change,
            }
        )


def test_orphan_identity_frozen_and_regular_revision_not_relaxed():
    expected = state.OrphanExecutionIdentity("cus", "pln", "agent", 1)
    with pytest.raises(FrozenInstanceError):
        expected.attempt = 2
    assert not hasattr(expected, "revision")
    for revision in (0, None):
        with pytest.raises(ValueError):
            state.ExecutionIdentity("cus", "pln", "agent", 1, revision)
