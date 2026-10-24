"""W4b1 Task 2: real-PG approval creation, binding and supersede.

Uses the isolated temporary PostgreSQL fixture (``make test-w4b1``). Never
touches the long-lived business volumes, keys, the full stack or L3.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from safebank_contracts.codes import Code
from safebank_contracts.enums import Risk
from sqlalchemy import select
from sqlalchemy.pool import NullPool

from services.gateway.app import tables as t
from services.gateway.app.approval import service as approval_service
from services.gateway.app.approval.planview import build_view
from services.gateway.app.approval.service import ApprovalBinding, verify_binding
from services.gateway.app.db import engine
from services.gateway.app.errors import GatewayError
from services.gateway.app.pipeline.context import EvalContext
from services.gateway.app.schemas.decision import Decision, DecisionValue, PreflightView
from services.gateway.app.schemas.enums import ApprovalStatus
from tests.gateway.w4b1.conftest import (
    HMAC_KEY,
    WALL,
    configure_approval,
    state,
    transfer_step,
)


def _runtime(gateway_db):
    return engine(gateway_db.runtime_url, poolclass=NullPool)


# The evaluation row must carry the same decision facts the view was built from
# (not an empty ``{}`` placeholder): risk/decision/findings/preflight all come
# from ``ctx.decision`` and are persisted by append_evaluation (W3b).
_PREFLIGHT_JSON = {
    "executable": True,
    "steps": [],
    "balances": [],
    "holdings": [],
    "scheduled_reserve_cents": 0,
}
_FINDINGS_JSON = []


async def _insert_session(conn, *, sid, customer, revoked=False):
    await conn.execute(
        t.sessions.insert().values(
            id=sid,
            customer_id=customer,
            authorization_epoch=0,
            created_at_wall=WALL,
            revoked_at_wall=WALL if revoked else None,
        )
    )


async def _insert_plan(conn, *, plan_id, sid, customer="cus_zs", origin="agent"):
    await conn.execute(
        t.plans.insert().values(
            id=plan_id,
            customer_id=customer,
            sid=sid,
            origin=origin,
            request_id=f"req_{plan_id}",
            request_hash="r",
            plan_hash="a" * 64,
            steps_hash="s",
            resolved_steps=[],
            utterance_ids=["utt_1"],
            scope_ids=["utt_1"],
            status="EVALUATED",
            created_at_wall=WALL,
        )
    )


async def _seed(conn, *, sid="ses_1", customer="cus_zs", plan_id="pln_1", origin="agent"):
    await _insert_session(conn, sid=sid, customer=customer)
    await _insert_plan(conn, plan_id=plan_id, sid=sid, customer=customer, origin=origin)


async def _seed_evaluation(
    conn,
    *,
    plan_id="pln_1",
    evaluation_id="evl_1",
    decision=DecisionValue.REQUIRE_APPROVAL,
    risk=Risk.YELLOW,
    policy_version="sha256:test",
    clock_version=1,
    versions=None,
    revision=1,
    preflight=None,
    findings=None,
):
    await conn.execute(
        t.evaluations.insert().values(
            id=evaluation_id,
            plan_id=plan_id,
            revision=revision,
            decision={"decision": decision.value},
            risk=risk.value,
            findings=findings if findings is not None else _FINDINGS_JSON,
            preflight=preflight if preflight is not None else _PREFLIGHT_JSON,
            versions=versions if versions is not None else {"cus_zs": 1},
            policy_version=policy_version,
            clock_version=clock_version,
            created_at_wall=WALL,
        )
    )


def _view(
    plan_id="pln_1",
    session_id="ses_1",
    origin="agent",
    evaluation_id="evl_1",
    risk=Risk.YELLOW,
    decision=DecisionValue.REQUIRE_APPROVAL,
):
    ctx = EvalContext(plan_id=plan_id, session_id=session_id, origin=origin, customer_id="cus_zs")
    ctx.steps = (transfer_step(),)
    ctx.snapshot = state()
    ctx.decision = Decision(
        plan_id=plan_id,
        evaluation_id=evaluation_id,
        revision=1,
        decision=decision,
        risk=risk,
        preflight=PreflightView(executable=True),
        versions={"cus_zs": 1},
        clock_version=1,
        policy_version="sha256:test",
    )
    ctx.wall_now = lambda: WALL
    return build_view(ctx)[0]


async def _rows(conn, *, plan_id="pln_1", evaluation_id="evl_1"):
    plan = (await conn.execute(select(t.plans).where(t.plans.c.id == plan_id))).mappings().one()
    evaluation = (
        (await conn.execute(select(t.evaluations).where(t.evaluations.c.id == evaluation_id)))
        .mappings()
        .one()
    )
    return plan, evaluation


async def _insert_approval(
    conn, *, approval_id, plan_id, evaluation_id, sid="ses_1", status="PENDING"
):
    await conn.execute(
        t.approvals.insert().values(
            id=approval_id,
            plan_id=plan_id,
            evaluation_id=evaluation_id,
            customer_id="cus_zs",
            sid=sid,
            plan_hash="a" * 64,
            view_hash="b" * 64,
            binding_mac="c" * 64,
            expires_at_wall=WALL + timedelta(minutes=5),
            status=status,
            risk_ack=False,
        )
    )


def test_create_pending_approval_and_persist_view_json(gateway_db, approval_config):
    configure_approval()

    async def run():
        db = _runtime(gateway_db)
        view = _view()
        async with db.begin() as conn:
            await _seed(conn)
            await _seed_evaluation(conn)
            plan, evaluation = await _rows(conn)
            approval_id = await approval_service.create_for_evaluation(
                conn, plan=plan, evaluation=evaluation, view=view
            )
        assert approval_id.startswith("apv_")

        async with db.connect() as conn:
            approval = (
                (await conn.execute(select(t.approvals).where(t.approvals.c.id == approval_id)))
                .mappings()
                .one()
            )
            evaluation = (
                (await conn.execute(select(t.evaluations).where(t.evaluations.c.id == "evl_1")))
                .mappings()
                .one()
            )
        assert approval["status"] == ApprovalStatus.PENDING.value
        assert approval["plan_id"] == "pln_1"
        assert approval["evaluation_id"] == "evl_1"
        assert approval["customer_id"] == "cus_zs"
        assert approval["sid"] == "ses_1"
        assert approval["plan_hash"] == "a" * 64
        assert approval["view_hash"] == view.view_hash
        assert approval["expires_at_wall"] == WALL + timedelta(minutes=5)

        # The persisted view_json carries the real approval ID, not the placeholder.
        assert evaluation["view_hash"] == view.view_hash
        assert evaluation["view_json"]["approval_id"] == approval_id
        assert evaluation["view_json"]["view_hash"] == view.view_hash
        assert evaluation["view_json"]["status"] == "PENDING_APPROVAL"

        # The binding MAC recomputes from the stored rows and passes verification.
        binding = ApprovalBinding(
            customer_id="cus_zs",
            sid="ses_1",
            plan_id="pln_1",
            plan_hash="a" * 64,
            evaluation_id="evl_1",
            versions={"cus_zs": 1},
            clock_version=1,
            policy_version="sha256:test",
            profile="B2",
            view_hash=view.view_hash,
            expires_at_wall=approval["expires_at_wall"],
        )
        assert verify_binding(HMAC_KEY, binding, approval["binding_mac"]) is True
        await db.dispose()

    asyncio.run(run())


def test_allow_returns_none_and_creates_nothing(gateway_db, approval_config):
    configure_approval()

    async def run():
        db = _runtime(gateway_db)
        async with db.begin() as conn:
            await _seed(conn)
            await _seed_evaluation(conn, decision=DecisionValue.ALLOW, risk=Risk.GREEN)
            plan, evaluation = await _rows(conn)
            view = _view(risk=Risk.GREEN, decision=DecisionValue.ALLOW)
            result = await approval_service.create_for_evaluation(
                conn, plan=plan, evaluation=evaluation, view=view
            )
        assert result is None
        async with db.connect() as conn:
            count = await conn.scalar(select(t.approvals.c.id).limit(1))
        assert count is None
        await db.dispose()

    asyncio.run(run())


def test_deny_and_replan_refuse(gateway_db, approval_config):
    configure_approval()

    async def run():
        db = _runtime(gateway_db)
        async with db.begin() as conn:
            for decision in (DecisionValue.DENY, DecisionValue.REPLAN):
                tag = decision.value.lower()
                await _seed(conn, sid=f"ses_{tag}", plan_id=f"pln_{tag}")
                await _seed_evaluation(
                    conn, plan_id=f"pln_{tag}", evaluation_id=f"evl_{tag}", decision=decision
                )
                plan, evaluation = await _rows(
                    conn, plan_id=f"pln_{tag}", evaluation_id=f"evl_{tag}"
                )
                view = _view(
                    plan_id=f"pln_{tag}", session_id=f"ses_{tag}", evaluation_id=f"evl_{tag}"
                )
                with pytest.raises(ValueError):
                    await approval_service.create_for_evaluation(
                        conn, plan=plan, evaluation=evaluation, view=view
                    )
        async with db.connect() as conn:
            count = await conn.scalar(select(t.approvals.c.id).limit(1))
        assert count is None
        await db.dispose()

    asyncio.run(run())


def test_same_sid_new_approval_supersedes_old(gateway_db, approval_config):
    configure_approval()

    async def run():
        db = _runtime(gateway_db)
        async with db.begin() as conn:
            await _seed(conn, plan_id="pln_1")
            await _seed_evaluation(conn, plan_id="pln_1", evaluation_id="evl_1")
            plan, evaluation = await _rows(conn, plan_id="pln_1")
            first = await approval_service.create_for_evaluation(
                conn, plan=plan, evaluation=evaluation, view=_view()
            )
            await _insert_plan(conn, plan_id="pln_2", sid="ses_1")
            await _seed_evaluation(conn, plan_id="pln_2", evaluation_id="evl_2")
            plan2, evaluation2 = await _rows(conn, plan_id="pln_2", evaluation_id="evl_2")
            second = await approval_service.create_for_evaluation(
                conn,
                plan=plan2,
                evaluation=evaluation2,
                view=_view(plan_id="pln_2", evaluation_id="evl_2"),
            )
        async with db.connect() as conn:
            rows = (
                (await conn.execute(select(t.approvals).order_by(t.approvals.c.id)))
                .mappings()
                .all()
            )
        by_id = {r["id"]: r for r in rows}
        assert by_id[first]["status"] == ApprovalStatus.SUPERSEDED.value
        assert by_id[second]["status"] == ApprovalStatus.PENDING.value
        await db.dispose()

    asyncio.run(run())


def test_different_sid_not_affected(gateway_db, approval_config):
    configure_approval()

    async def run():
        db = _runtime(gateway_db)
        async with db.begin() as conn:
            await _seed(conn, sid="ses_1", plan_id="pln_1")
            await _seed_evaluation(conn, plan_id="pln_1", evaluation_id="evl_1")
            plan, evaluation = await _rows(conn, plan_id="pln_1")
            first = await approval_service.create_for_evaluation(
                conn, plan=plan, evaluation=evaluation, view=_view()
            )
            await _seed(conn, sid="ses_2", plan_id="pln_2")
            await _seed_evaluation(conn, plan_id="pln_2", evaluation_id="evl_2")
            plan2, evaluation2 = await _rows(conn, plan_id="pln_2", evaluation_id="evl_2")
            second = await approval_service.create_for_evaluation(
                conn,
                plan=plan2,
                evaluation=evaluation2,
                view=_view(plan_id="pln_2", session_id="ses_2", evaluation_id="evl_2"),
            )
        async with db.connect() as conn:
            first_row = (
                (await conn.execute(select(t.approvals).where(t.approvals.c.id == first)))
                .mappings()
                .one()
            )
            second_row = (
                (await conn.execute(select(t.approvals).where(t.approvals.c.id == second)))
                .mappings()
                .one()
            )
        assert first_row["status"] == ApprovalStatus.PENDING.value
        assert second_row["status"] == ApprovalStatus.PENDING.value
        await db.dispose()

    asyncio.run(run())


def test_same_evaluation_idempotent_no_extension(gateway_db, approval_config):
    configure_approval()

    async def run():
        db = _runtime(gateway_db)
        view = _view()
        async with db.begin() as conn:
            await _seed(conn)
            await _seed_evaluation(conn)
            plan, evaluation = await _rows(conn)
            first = await approval_service.create_for_evaluation(
                conn, plan=plan, evaluation=evaluation, view=view
            )
            second = await approval_service.create_for_evaluation(
                conn, plan=plan, evaluation=evaluation, view=view
            )
        assert first == second
        async with db.connect() as conn:
            rows = (await conn.execute(select(t.approvals))).mappings().all()
        assert len(rows) == 1
        assert rows[0]["expires_at_wall"] == WALL + timedelta(minutes=5)
        await db.dispose()

    asyncio.run(run())


def test_expiry_beyond_five_minutes_rejected(gateway_db, approval_config):
    configure_approval()

    async def run():
        db = _runtime(gateway_db)
        view = _view().model_copy(
            update={"expires_at_wall": (WALL + timedelta(minutes=10)).isoformat()}
        )
        async with db.begin() as conn:
            await _seed(conn)
            await _seed_evaluation(conn)
            plan, evaluation = await _rows(conn)
            with pytest.raises(ValueError):
                await approval_service.create_for_evaluation(
                    conn, plan=plan, evaluation=evaluation, view=view
                )
        await db.dispose()

    asyncio.run(run())


def test_expired_view_rejected(gateway_db, approval_config):
    configure_approval()

    async def run():
        db = _runtime(gateway_db)
        view = _view().model_copy(
            update={"expires_at_wall": (WALL - timedelta(minutes=1)).isoformat()}
        )
        async with db.begin() as conn:
            await _seed(conn)
            await _seed_evaluation(conn)
            plan, evaluation = await _rows(conn)
            with pytest.raises(ValueError):
                await approval_service.create_for_evaluation(
                    conn, plan=plan, evaluation=evaluation, view=view
                )
        await db.dispose()

    asyncio.run(run())


def test_revoked_session_rejected(gateway_db, approval_config):
    configure_approval()

    async def run():
        db = _runtime(gateway_db)
        async with db.begin() as conn:
            await _seed(conn)
            await conn.execute(
                t.sessions.update().where(t.sessions.c.id == "ses_1").values(revoked_at_wall=WALL)
            )
            await _seed_evaluation(conn)
            plan, evaluation = await _rows(conn)
            with pytest.raises(GatewayError) as exc:
                await approval_service.create_for_evaluation(
                    conn, plan=plan, evaluation=evaluation, view=_view()
                )
            assert exc.value.code is Code.AUTHORIZATION_CANCELLED
            assert exc.value.status == 409
        await db.dispose()

    asyncio.run(run())


def test_customer_mismatch_rejected(gateway_db, approval_config):
    configure_approval()

    async def run():
        db = _runtime(gateway_db)
        async with db.begin() as conn:
            await _insert_session(conn, sid="ses_1", customer="cus_other")
            await _insert_plan(conn, plan_id="pln_1", sid="ses_1", customer="cus_zs")
            await _seed_evaluation(conn)
            plan, evaluation = await _rows(conn)
            with pytest.raises(GatewayError) as exc:
                await approval_service.create_for_evaluation(
                    conn, plan=plan, evaluation=evaluation, view=_view()
                )
            assert exc.value.code is Code.OWNERSHIP_MISMATCH
            assert exc.value.status == 404
        await db.dispose()

    asyncio.run(run())


def test_live_job_blocks_new_approval(gateway_db, approval_config):
    configure_approval()

    async def run():
        db = _runtime(gateway_db)
        async with db.begin() as conn:
            await _seed(conn)
            await _seed_evaluation(conn)
            await conn.execute(
                t.execution_jobs.insert().values(
                    plan_id="pln_1",
                    status="EXECUTING",
                    l3_attempt=1,
                    retry_count=0,
                    created_at_wall=WALL,
                    updated_at_wall=WALL,
                )
            )
            plan, evaluation = await _rows(conn)
            with pytest.raises(GatewayError) as exc:
                await approval_service.create_for_evaluation(
                    conn, plan=plan, evaluation=evaluation, view=_view()
                )
            assert exc.value.code is Code.STATE_CHANGED
            assert exc.value.status == 409
        await db.dispose()

    asyncio.run(run())


def test_tampered_binding_fields_fail_verify(gateway_db, approval_config):
    configure_approval()

    async def run():
        db = _runtime(gateway_db)
        view = _view()
        async with db.begin() as conn:
            await _seed(conn)
            await _seed_evaluation(conn)
            plan, evaluation = await _rows(conn)
            approval_id = await approval_service.create_for_evaluation(
                conn, plan=plan, evaluation=evaluation, view=view
            )
        async with db.connect() as conn:
            approval = (
                (await conn.execute(select(t.approvals).where(t.approvals.c.id == approval_id)))
                .mappings()
                .one()
            )
        base = dict(
            customer_id="cus_zs",
            sid="ses_1",
            plan_id="pln_1",
            plan_hash="a" * 64,
            evaluation_id="evl_1",
            versions={"cus_zs": 1},
            clock_version=1,
            policy_version="sha256:test",
            profile="B2",
            view_hash=view.view_hash,
            expires_at_wall=approval["expires_at_wall"],
        )
        mac = approval["binding_mac"]
        assert verify_binding(HMAC_KEY, ApprovalBinding(**base), mac) is True
        assert verify_binding(HMAC_KEY, ApprovalBinding(**{**base, "sid": "ses_2"}), mac) is False
        assert (
            verify_binding(HMAC_KEY, ApprovalBinding(**{**base, "view_hash": "d" * 64}), mac)
            is False
        )
        assert (
            verify_binding(HMAC_KEY, ApprovalBinding(**{**base, "policy_version": "sha256:x"}), mac)
            is False
        )
        assert verify_binding(HMAC_KEY, ApprovalBinding(**{**base, "profile": "B1"}), mac) is False
        await db.dispose()

    asyncio.run(run())


def test_evaluation_of_another_plan_rejected_atomically(gateway_db, approval_config):
    configure_approval()

    async def run():
        db = _runtime(gateway_db)
        async with db.begin() as conn:
            await _seed(conn, plan_id="pln_1")
            await _seed(conn, sid="ses_2", plan_id="pln_other")
            # evl_b belongs to pln_other, but the caller pairs it with plan pln_1.
            await _seed_evaluation(conn, plan_id="pln_other", evaluation_id="evl_b")
            plan, evaluation = await _rows(conn, plan_id="pln_1", evaluation_id="evl_b")
            view = _view(plan_id="pln_1", evaluation_id="evl_b")
            with pytest.raises(ValueError):
                await approval_service.create_for_evaluation(
                    conn, plan=plan, evaluation=evaluation, view=view
                )
        async with db.connect() as conn:
            approvals = (await conn.execute(select(t.approvals))).mappings().all()
            eval_row = (
                (await conn.execute(select(t.evaluations).where(t.evaluations.c.id == "evl_b")))
                .mappings()
                .one()
            )
        assert approvals == []
        assert eval_row["view_hash"] is None
        assert eval_row["view_json"] is None
        await db.dispose()

    asyncio.run(run())


def test_stale_evaluation_revision_rejected(gateway_db, approval_config):
    configure_approval()

    async def run():
        db = _runtime(gateway_db)
        async with db.begin() as conn:
            await _seed(conn)
            await _seed_evaluation(conn, evaluation_id="evl_1", revision=1)
            await _seed_evaluation(conn, evaluation_id="evl_2", revision=2)
            plan, evaluation = await _rows(conn, evaluation_id="evl_1")
            with pytest.raises(GatewayError) as exc:
                await approval_service.create_for_evaluation(
                    conn, plan=plan, evaluation=evaluation, view=_view()
                )
            assert exc.value.code is Code.STATE_CHANGED
            assert exc.value.status == 409
        async with db.connect() as conn:
            approvals = (await conn.execute(select(t.approvals))).mappings().all()
        assert approvals == []
        await db.dispose()

    asyncio.run(run())


def test_terminal_plan_status_rejected(gateway_db, approval_config):
    configure_approval()

    async def run():
        db = _runtime(gateway_db)
        async with db.begin() as conn:
            await _seed(conn)
            await conn.execute(
                t.plans.update().where(t.plans.c.id == "pln_1").values(status="EXECUTED")
            )
            await _seed_evaluation(conn)
            plan, evaluation = await _rows(conn)
            with pytest.raises(GatewayError) as exc:
                await approval_service.create_for_evaluation(
                    conn, plan=plan, evaluation=evaluation, view=_view()
                )
            assert exc.value.code is Code.STATE_CHANGED
            assert exc.value.status == 409
        await db.dispose()

    asyncio.run(run())


def test_existing_superseded_approval_not_returned_as_success(gateway_db, approval_config):
    configure_approval()

    async def run():
        db = _runtime(gateway_db)
        view = _view()
        async with db.begin() as conn:
            await _seed(conn)
            await _seed_evaluation(conn)
            plan, evaluation = await _rows(conn)
            await _insert_approval(
                conn,
                approval_id="apv_old",
                plan_id="pln_1",
                evaluation_id="evl_1",
                status="SUPERSEDED",
            )
            with pytest.raises(GatewayError) as exc:
                await approval_service.create_for_evaluation(
                    conn, plan=plan, evaluation=evaluation, view=view
                )
            assert exc.value.code is Code.APPROVAL_SUPERSEDED
            assert exc.value.status == 409
        await db.dispose()

    asyncio.run(run())


def test_concurrent_same_evaluation_returns_same_id(gateway_db, approval_config):
    configure_approval()

    async def run():
        db = _runtime(gateway_db)
        async with db.begin() as conn:
            await _seed(conn)
            await _seed_evaluation(conn)
        view = _view()

        async def create():
            async with db.begin() as conn:
                plan, evaluation = await _rows(conn)
                return await approval_service.create_for_evaluation(
                    conn, plan=plan, evaluation=evaluation, view=view
                )

        # T1 holds its row locks open (approval inserted but not committed).
        conn1 = await db.connect()
        tx1 = await conn1.begin()
        plan, evaluation = await _rows(conn1)
        id1 = await approval_service.create_for_evaluation(
            conn1, plan=plan, evaluation=evaluation, view=view
        )

        # T2 starts independently and must block on the held plan/session lock.
        task2 = asyncio.create_task(create())
        await asyncio.sleep(0.2)
        assert not task2.done()

        await tx1.commit()
        id2 = await asyncio.wait_for(task2, timeout=10)
        assert id1 == id2

        async with db.connect() as conn:
            rows = (await conn.execute(select(t.approvals))).mappings().all()
        assert len(rows) == 1
        await conn1.close()
        await db.dispose()

    asyncio.run(run())


def test_live_job_of_other_plan_blocks_supersede(gateway_db, approval_config):
    configure_approval()

    async def run():
        db = _runtime(gateway_db)
        async with db.begin() as conn:
            await _seed(conn, plan_id="pln_a")
            await _seed_evaluation(conn, plan_id="pln_a", evaluation_id="evl_a")
            await _insert_approval(
                conn,
                approval_id="apv_a",
                plan_id="pln_a",
                evaluation_id="evl_a",
                status="CONFIRMED",
            )
            await conn.execute(
                t.execution_jobs.insert().values(
                    plan_id="pln_a",
                    status="EXECUTING",
                    l3_attempt=1,
                    retry_count=0,
                    created_at_wall=WALL,
                    updated_at_wall=WALL,
                )
            )
            await conn.execute(
                t.authorization_claims.insert().values(
                    scope_id="utt_1", plan_id="pln_a", status="CLAIMED"
                )
            )
            await _insert_plan(conn, plan_id="pln_b", sid="ses_1")
            await _seed_evaluation(conn, plan_id="pln_b", evaluation_id="evl_b")
            plan_b, evaluation_b = await _rows(conn, plan_id="pln_b", evaluation_id="evl_b")
            with pytest.raises(GatewayError) as exc:
                await approval_service.create_for_evaluation(
                    conn,
                    plan=plan_b,
                    evaluation=evaluation_b,
                    view=_view(plan_id="pln_b", evaluation_id="evl_b"),
                )
            assert exc.value.code is Code.STATE_CHANGED
            assert exc.value.status == 409
        async with db.connect() as conn:
            approval_a = (
                (await conn.execute(select(t.approvals).where(t.approvals.c.id == "apv_a")))
                .mappings()
                .one()
            )
            job = (
                (
                    await conn.execute(
                        select(t.execution_jobs).where(t.execution_jobs.c.plan_id == "pln_a")
                    )
                )
                .mappings()
                .one()
            )
            claim = (
                (
                    await conn.execute(
                        select(t.authorization_claims).where(
                            t.authorization_claims.c.plan_id == "pln_a"
                        )
                    )
                )
                .mappings()
                .one()
            )
            new_approvals = (
                (await conn.execute(select(t.approvals).where(t.approvals.c.plan_id == "pln_b")))
                .mappings()
                .all()
            )
        assert approval_a["status"] == "CONFIRMED"
        assert job["status"] == "EXECUTING"
        assert claim["status"] == "CLAIMED"
        assert new_approvals == []
        await db.dispose()

    asyncio.run(run())


def test_orphan_claim_without_job_blocks_supersede(gateway_db, approval_config):
    configure_approval()

    async def run():
        db = _runtime(gateway_db)
        async with db.begin() as conn:
            await _seed(conn, plan_id="pln_a")
            await _seed_evaluation(conn, plan_id="pln_a", evaluation_id="evl_a")
            await _insert_approval(
                conn,
                approval_id="apv_a",
                plan_id="pln_a",
                evaluation_id="evl_a",
                status="CONFIRMED",
            )
            # Legacy/malformed persistent state: CLAIMED claim without any job.
            await conn.execute(
                t.authorization_claims.insert().values(
                    scope_id="utt_1", plan_id="pln_a", status="CLAIMED"
                )
            )
            await _insert_plan(conn, plan_id="pln_b", sid="ses_1")
            await _seed_evaluation(conn, plan_id="pln_b", evaluation_id="evl_b")
            plan_b, evaluation_b = await _rows(conn, plan_id="pln_b", evaluation_id="evl_b")
            with pytest.raises(GatewayError) as exc:
                await approval_service.create_for_evaluation(
                    conn,
                    plan=plan_b,
                    evaluation=evaluation_b,
                    view=_view(plan_id="pln_b", evaluation_id="evl_b"),
                )
            assert exc.value.code is Code.STATE_CHANGED
            assert exc.value.status == 409
        async with db.connect() as conn:
            approval_a = (
                (await conn.execute(select(t.approvals).where(t.approvals.c.id == "apv_a")))
                .mappings()
                .one()
            )
            claim = (
                (
                    await conn.execute(
                        select(t.authorization_claims).where(
                            t.authorization_claims.c.plan_id == "pln_a"
                        )
                    )
                )
                .mappings()
                .one()
            )
            new_approvals = (
                (await conn.execute(select(t.approvals).where(t.approvals.c.plan_id == "pln_b")))
                .mappings()
                .all()
            )
        assert approval_a["status"] == "CONFIRMED"
        assert claim["status"] == "CLAIMED"
        assert new_approvals == []
        await db.dispose()

    asyncio.run(run())


def test_consumed_claim_does_not_block_supersede(gateway_db, approval_config):
    configure_approval()

    async def run():
        db = _runtime(gateway_db)
        async with db.begin() as conn:
            await _seed(conn, plan_id="pln_a")
            await _seed_evaluation(conn, plan_id="pln_a", evaluation_id="evl_a")
            await _insert_approval(
                conn,
                approval_id="apv_a",
                plan_id="pln_a",
                evaluation_id="evl_a",
                status="CONFIRMED",
            )
            await conn.execute(
                t.authorization_claims.insert().values(
                    scope_id="utt_1", plan_id="pln_a", status="CONSUMED"
                )
            )
            await _insert_plan(conn, plan_id="pln_b", sid="ses_1")
            await _seed_evaluation(conn, plan_id="pln_b", evaluation_id="evl_b")
            plan_b, evaluation_b = await _rows(conn, plan_id="pln_b", evaluation_id="evl_b")
            second = await approval_service.create_for_evaluation(
                conn,
                plan=plan_b,
                evaluation=evaluation_b,
                view=_view(plan_id="pln_b", evaluation_id="evl_b"),
            )
        async with db.connect() as conn:
            approval_a = (
                (await conn.execute(select(t.approvals).where(t.approvals.c.id == "apv_a")))
                .mappings()
                .one()
            )
        assert approval_a["status"] == "SUPERSEDED"
        assert second.startswith("apv_")
        await db.dispose()

    asyncio.run(run())


def test_existing_approval_with_tampered_mac_rejected(gateway_db, approval_config):
    configure_approval()

    async def run():
        db = _runtime(gateway_db)
        view = _view()
        async with db.begin() as conn:
            await _seed(conn)
            await _seed_evaluation(conn)
            plan, evaluation = await _rows(conn)
            approval_id = await approval_service.create_for_evaluation(
                conn, plan=plan, evaluation=evaluation, view=view
            )
            await conn.execute(
                t.approvals.update()
                .where(t.approvals.c.id == approval_id)
                .values(binding_mac="0" * 64)
            )
            with pytest.raises(GatewayError) as exc:
                await approval_service.create_for_evaluation(
                    conn, plan=plan, evaluation=evaluation, view=view
                )
            assert exc.value.code is Code.APPROVAL_BINDING_MISMATCH
            assert exc.value.status == 409
        await db.dispose()

    asyncio.run(run())


def test_existing_approval_binding_field_mismatch_rejected(gateway_db, approval_config):
    configure_approval()

    async def run():
        db = _runtime(gateway_db)
        view = _view()
        async with db.begin() as conn:
            await _seed(conn)
            await _seed_evaluation(conn)
            plan, evaluation = await _rows(conn)
            approval_id = await approval_service.create_for_evaluation(
                conn, plan=plan, evaluation=evaluation, view=view
            )
            await conn.execute(
                t.approvals.update()
                .where(t.approvals.c.id == approval_id)
                .values(plan_hash="f" * 64)
            )
            with pytest.raises(GatewayError) as exc:
                await approval_service.create_for_evaluation(
                    conn, plan=plan, evaluation=evaluation, view=view
                )
            assert exc.value.code is Code.APPROVAL_BINDING_MISMATCH
            assert exc.value.status == 409
        await db.dispose()

    asyncio.run(run())


def test_create_lock_order_is_session_first(gateway_db, approval_config):
    configure_approval()

    async def run():
        db = _runtime(gateway_db)
        async with db.begin() as conn:
            await _seed(conn)
            await _seed_evaluation(conn)
        view = _view()

        async def create():
            async with db.begin() as conn:
                plan, evaluation = await _rows(conn)
                return await approval_service.create_for_evaluation(
                    conn, plan=plan, evaluation=evaluation, view=view
                )

        # T1 (W1-style caller) holds the session row lock first, then locks the
        # plan row as a cancel_session does. create must hold no plan lock while
        # blocked on session, otherwise this is a deterministic ABBA deadlock.
        conn1 = await db.connect()
        tx1 = await conn1.begin()
        await conn1.execute(
            select(t.sessions.c.id).where(t.sessions.c.id == "ses_1").with_for_update()
        )

        task2 = asyncio.create_task(create())
        await asyncio.sleep(0.2)
        assert not task2.done()

        await conn1.execute(select(t.plans.c.id).where(t.plans.c.id == "pln_1").with_for_update())
        await tx1.commit()

        approval_id = await asyncio.wait_for(task2, timeout=10)
        assert approval_id.startswith("apv_")
        await conn1.close()
        await db.dispose()

    asyncio.run(run())
