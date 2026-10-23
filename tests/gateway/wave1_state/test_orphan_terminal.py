import asyncio
from dataclasses import replace

import pytest
from safebank_contracts.plan import ExecutionResult, StepResult
from sqlalchemy.pool import NullPool

from services.gateway.app import state
from services.gateway.app import tables as t
from services.gateway.app.db import engine
from services.gateway.app.errors import GatewayError
from tests.gateway.wave1_state.orphan_helpers import identity, orphan, snapshot


def committed(ids, now):
    return ExecutionResult(
        plan_id=ids["pln"],
        status="EXECUTED",
        versions={},
        committed_at=now,
        error_code=None,
        replayed=False,
        steps=(
            StepResult(
                step_id="s1",
                tool="freeze_card",
                status="EXECUTED",
                created_ids={},
                result={},
                effects=(),
            ),
        ),
    )


@pytest.mark.parametrize(
    "l3_status,with_result", [("COMMITTED", True), ("CANCELLED", False), ("CANCELLED", True)]
)
@pytest.mark.parametrize("utterance_status", ["ACTIVE", "CANCELLED", "SUPERSEDED"])
@pytest.mark.parametrize("legacy_null", [True, False])
def test_orphan_terminal_consumes_claim_only_after_valid_terminal(
    gateway_db, l3_status, with_result, utterance_status, legacy_null
):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            async with db.begin() as conn:
                ids, now = await orphan(
                    conn, job_status="UNKNOWN", plan_status="UNKNOWN", legacy_null=legacy_null
                )
                await conn.execute(
                    t.utterances.update()
                    .where(t.utterances.c.id == ids["utt"])
                    .values(status=utterance_status)
                )
                before = await snapshot(conn, ids)
            result = committed(ids, now) if with_result else None
            if l3_status == "CANCELLED" and with_result:
                result = result.model_copy(
                    update={
                        "status": "FAILED",
                        "committed_at": None,
                        "steps": (result.steps[0].model_copy(update={"status": "FAILED"}),),
                    }
                )
            async with db.begin() as conn:
                assert (
                    await state.archive_orphan_execution_terminal(
                        conn,
                        expected=identity(ids),
                        l3_status=l3_status,
                        result=result,
                    )
                    is None
                )
            async with db.connect() as conn:
                after = await snapshot(conn, ids)
                terminal = "EXECUTED" if l3_status == "COMMITTED" else "FAILED"
                assert after["job"].pop("status") == after["plan"]["status"] == terminal
                assert after["job"].pop("result") == (
                    result.model_dump(mode="json") if result else None
                )
                after["job"].pop("updated_at_wall")
                assert after["job"] == {
                    k: v
                    for k, v in before["job"].items()
                    if k not in {"status", "result", "updated_at_wall"}
                }
                assert after["claims"] == [{**c, "status": "CONSUMED"} for c in before["claims"]]
                assert after["utterance"]["status"] == (
                    "CONSUMED" if utterance_status == "ACTIVE" else utterance_status
                )
                assert after["utterance"]["consumed_by"] == (
                    ids["pln"] if utterance_status == "ACTIVE" else None
                )
            async with db.begin() as conn:
                with pytest.raises(GatewayError):
                    await state.archive_orphan_execution_terminal(
                        conn, expected=identity(ids), l3_status=l3_status, result=result
                    )
        finally:
            await db.dispose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "mutation",
    [
        "no_committed_result",
        "wrong_result_plan",
        "failed_committed",
        "missing_commit_time",
        "failed_step_committed",
        "successful_cancelled",
        "successful_step_cancelled",
        "prepared",
        "not_found",
        "wrong_customer",
        "wrong_plan",
        "wrong_origin",
        "wrong_attempt",
        "job_not_unknown",
        "plan_not_unknown",
        "consumed_claim",
        "missing_claim",
        "extra_claim",
        "wrong_scope",
        "evaluation_exists",
    ],
)
def test_orphan_bad_terminal_never_consumes_or_partially_writes(gateway_db, mutation):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            async with db.begin() as conn:
                ids, now = await orphan(conn, job_status="UNKNOWN", plan_status="UNKNOWN")
                expected = identity(ids)
                changes = {
                    "wrong_customer": ("customer_id", "other"),
                    "wrong_plan": ("plan_id", "other"),
                    "wrong_origin": ("origin", "app_form"),
                    "wrong_attempt": ("attempt", 4),
                }
                if mutation in changes:
                    k, v = changes[mutation]
                    expected = replace(expected, **{k: v})
                if mutation == "job_not_unknown":
                    await conn.execute(
                        t.execution_jobs.update()
                        .where(t.execution_jobs.c.plan_id == ids["pln"])
                        .values(status="PAUSED")
                    )
                if mutation == "plan_not_unknown":
                    await conn.execute(
                        t.plans.update()
                        .where(t.plans.c.id == ids["pln"])
                        .values(status="EXECUTING")
                    )
                if mutation == "consumed_claim":
                    await conn.execute(
                        t.authorization_claims.update()
                        .where(t.authorization_claims.c.plan_id == ids["pln"])
                        .values(status="CONSUMED")
                    )
                if mutation == "missing_claim":
                    await conn.execute(
                        t.authorization_claims.delete().where(
                            t.authorization_claims.c.plan_id == ids["pln"]
                        )
                    )
                if mutation == "extra_claim":
                    await conn.execute(
                        t.authorization_claims.insert().values(
                            scope_id="extra_" + ids["utt"], plan_id=ids["pln"], status="CLAIMED"
                        )
                    )
                if mutation == "wrong_scope":
                    await conn.execute(
                        t.plans.update()
                        .where(t.plans.c.id == ids["pln"])
                        .values(scope_ids=["other"])
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
            result = committed(ids, now)
            l3_status = "COMMITTED"
            if mutation == "no_committed_result":
                result = None
            elif mutation == "wrong_result_plan":
                result = result.model_copy(update={"plan_id": "other"})
            elif mutation == "failed_committed":
                result = result.model_copy(update={"status": "FAILED"})
            elif mutation == "missing_commit_time":
                result = result.model_copy(update={"committed_at": None})
            elif mutation == "failed_step_committed":
                result = result.model_copy(
                    update={"steps": (result.steps[0].model_copy(update={"status": "FAILED"}),)}
                )
            elif mutation in {"successful_cancelled", "successful_step_cancelled"}:
                l3_status = "CANCELLED"
                if mutation == "successful_step_cancelled":
                    result = result.model_copy(update={"status": "FAILED"})
            elif mutation in {"prepared", "not_found"}:
                l3_status = "PREPARED" if mutation == "prepared" else "404"
            async with db.begin() as conn:
                with pytest.raises(GatewayError) as err:
                    await state.archive_orphan_execution_terminal(
                        conn, expected=expected, l3_status=l3_status, result=result
                    )
                assert err.value.status == 409
                assert await snapshot(conn, ids) == before
        finally:
            await db.dispose()

    asyncio.run(run())
