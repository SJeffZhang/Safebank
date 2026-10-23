import asyncio

import pytest
from safebank_contracts.codes import Code
from safebank_contracts.plan import InteractiveEvidence
from sqlalchemy.pool import NullPool

from services.gateway.app import state
from services.gateway.app import tables as t
from services.gateway.app.db import engine
from services.gateway.app.errors import GatewayError
from tests.gateway.wave1_state.orphan_helpers import identity, orphan, snapshot
from tests.gateway.wave1_state.test_orphan_races import new_evaluation


@pytest.mark.parametrize(
    "method,status",
    [
        ("begin_fencing", "EXECUTING"),
        ("acknowledge_fence", "FENCING"),
        ("guard_paused_execution", "PAUSED"),
        ("publish_paused_decision", "PAUSED"),
        ("resume_paused_execution", "PAUSED"),
    ],
)
@pytest.mark.parametrize("has_evaluation", [False, True])
def test_orphan_identity_cannot_bypass_regular_revision_guards(
    gateway_db, method, status, has_evaluation
):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            async with db.begin() as conn:
                ids, now = await orphan(conn, job_status=status)
                if has_evaluation:
                    await conn.execute(new_evaluation(ids, now))
                before = await snapshot(conn, ids)
            args = {"expected": identity(ids)}
            if method == "publish_paused_decision":
                args.update(to=state.PlanStatus.EVALUATED, now=now)
            elif method == "resume_paused_execution":
                evidence = InteractiveEvidence(
                    type="INTERACTIVE",
                    origin="agent",
                    scope_ids=(ids["utt"],),
                    utterance_ids=(ids["utt"],),
                    evaluation_id="evl_" + ids["pln"],
                    approved_evaluation_id=None,
                    plan_hash="hash",
                    approval_id=None,
                    confirmed_view_hash=None,
                    final_view_hash="view",
                    risk="GREEN",
                    confirmed_at_wall=None,
                    authorization_epoch=0,
                )
                args.update(
                    authorization_evidence=evidence,
                    deadline_wall=now,
                    deadline_cap_wall=now,
                    approval_id=None,
                    now=now,
                )
            async with db.begin() as conn:
                with pytest.raises((GatewayError, ValueError)):
                    await getattr(state, method)(conn, **args)
                assert await snapshot(conn, ids) == before
        finally:
            await db.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("terminal", [False, True])
def test_orphan_interfaces_require_callers_existing_transaction(gateway_db, terminal):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            async with db.begin() as conn:
                ids, _ = await orphan(conn)
            async with db.connect() as conn:
                with pytest.raises(ValueError, match="active short transaction"):
                    if terminal:
                        await state.archive_orphan_execution_terminal(
                            conn, expected=identity(ids), l3_status="CANCELLED", result=None
                        )
                    else:
                        await state.mark_orphan_execution_unknown(
                            conn,
                            expected=identity(ids),
                            from_status=state.ExecutionJobStatus.EXECUTING,
                            reason={},
                        )
                assert not conn.in_transaction()
        finally:
            await db.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("terminal", [False, True])
def test_orphan_rejects_duplicate_scopes_without_repair(gateway_db, terminal):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            async with db.begin() as conn:
                ids, _ = await orphan(
                    conn,
                    job_status="UNKNOWN" if terminal else "EXECUTING",
                    plan_status="UNKNOWN" if terminal else "EXECUTING",
                )
                await conn.execute(
                    t.plans.update()
                    .where(t.plans.c.id == ids["pln"])
                    .values(scope_ids=[ids["utt"], ids["utt"]])
                )
                before = await snapshot(conn, ids)
            async with db.begin() as conn:
                with pytest.raises(GatewayError):
                    if terminal:
                        await state.archive_orphan_execution_terminal(
                            conn, expected=identity(ids), l3_status="CANCELLED", result=None
                        )
                    else:
                        await state.mark_orphan_execution_unknown(
                            conn,
                            expected=identity(ids),
                            from_status=state.ExecutionJobStatus.EXECUTING,
                            reason={},
                        )
                assert await snapshot(conn, ids) == before
        finally:
            await db.dispose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "plan_status",
    [
        "EVALUATED",
        "REPLAN",
        "DENIED",
        "PENDING_APPROVAL",
        "CONFIRMED",
        "UNKNOWN",
        "REJECTED",
        "EXPIRED",
        "SUPERSEDED",
    ],
)
def test_nonarchived_orphan_plan_labels_do_not_block_cleanup(gateway_db, plan_status):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            async with db.begin() as conn:
                ids, _ = await orphan(conn, plan_status=plan_status)
                await state.mark_orphan_execution_unknown(
                    conn,
                    expected=identity(ids),
                    from_status=state.ExecutionJobStatus.EXECUTING,
                    reason={},
                )
                saved = await snapshot(conn, ids)
                assert saved["plan"]["status"] == saved["job"]["status"] == "UNKNOWN"
                assert saved["claims"][0]["status"] == "CLAIMED"
        finally:
            await db.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("status", ["EXECUTED", "FAILED", "CANCELLED"])
def test_archived_orphan_job_cannot_be_marked_unknown(gateway_db, status):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            async with db.begin() as conn:
                ids, _ = await orphan(conn, job_status=status)
                before = await snapshot(conn, ids)
            async with db.begin() as conn:
                with pytest.raises(GatewayError):
                    await state.mark_orphan_execution_unknown(
                        conn,
                        expected=identity(ids),
                        from_status=state.ExecutionJobStatus(status),
                        reason={},
                    )
                assert await snapshot(conn, ids) == before
        finally:
            await db.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("terminal", [False, True])
def test_revision_identity_is_not_an_expected_absence_identity(gateway_db, terminal):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            async with db.begin() as conn:
                ids, _ = await orphan(conn)
            regular = state.ExecutionIdentity("cus_orphan", ids["pln"], "agent", 3, 1)
            async with db.begin() as conn:
                with pytest.raises(ValueError, match="OrphanExecutionIdentity"):
                    if terminal:
                        await state.archive_orphan_execution_terminal(
                            conn, expected=regular, l3_status="CANCELLED", result=None
                        )
                    else:
                        await state.mark_orphan_execution_unknown(
                            conn,
                            expected=regular,
                            from_status=state.ExecutionJobStatus.EXECUTING,
                            reason={},
                        )
        finally:
            await db.dispose()

    asyncio.run(run())


@pytest.mark.parametrize(
    "bad_scopes",
    [
        None,
        True,
        7,
        "not-an-array",
        {"nested": "value"},
        [None],
        [7],
        [""],
        [[]],
        [{"nested": "value"}],
    ],
)
@pytest.mark.parametrize("terminal", [False, True])
def test_malformed_persisted_scope_json_is_controlled_conflict(gateway_db, bad_scopes, terminal):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            async with db.begin() as conn:
                ids, _ = await orphan(
                    conn,
                    job_status="UNKNOWN" if terminal else "EXECUTING",
                    plan_status="UNKNOWN" if terminal else "EXECUTING",
                )
                await conn.execute(
                    t.plans.update().where(t.plans.c.id == ids["pln"]).values(scope_ids=bad_scopes)
                )
                before = await snapshot(conn, ids)
            async with db.begin() as conn:
                with pytest.raises(GatewayError) as err:
                    if terminal:
                        await state.archive_orphan_execution_terminal(
                            conn, expected=identity(ids), l3_status="CANCELLED", result=None
                        )
                    else:
                        await state.mark_orphan_execution_unknown(
                            conn,
                            expected=identity(ids),
                            from_status=state.ExecutionJobStatus.EXECUTING,
                            reason={},
                        )
                assert err.value.status == 409
                assert await snapshot(conn, ids) == before
        finally:
            await db.dispose()

    asyncio.run(run())


@pytest.mark.parametrize("terminal", [False, True])
def test_empty_scopes_and_zero_claims_are_rejected_before_any_write(gateway_db, terminal):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        try:
            async with db.begin() as conn:
                ids, _ = await orphan(
                    conn,
                    job_status="UNKNOWN" if terminal else "EXECUTING",
                    plan_status="UNKNOWN" if terminal else "EXECUTING",
                )
                await conn.execute(
                    t.plans.update().where(t.plans.c.id == ids["pln"]).values(scope_ids=[])
                )
                await conn.execute(
                    t.authorization_claims.delete().where(
                        t.authorization_claims.c.plan_id == ids["pln"]
                    )
                )
                before = await snapshot(conn, ids)
                assert before["plan"]["scope_ids"] == before["claims"] == []
                assert before["utterance"]["status"] == "ACTIVE"
            async with db.begin() as conn:
                with pytest.raises(GatewayError) as err:
                    if terminal:
                        await state.archive_orphan_execution_terminal(
                            conn, expected=identity(ids), l3_status="CANCELLED", result=None
                        )
                    else:
                        await state.mark_orphan_execution_unknown(
                            conn,
                            expected=identity(ids),
                            from_status=state.ExecutionJobStatus.EXECUTING,
                            reason={},
                        )
                assert err.value.status == 409 and err.value.code is Code.STATE_CHANGED
                assert await snapshot(conn, ids) == before
        finally:
            await db.dispose()

    asyncio.run(run())
