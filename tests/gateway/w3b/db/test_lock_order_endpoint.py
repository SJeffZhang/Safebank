"""W3b B5: endpoint-level lock-order regression tests via ASGI HTTP.

Unlike ``test_lock_order.py`` (which validates helper-function lock order with
direct function calls and includes RED verification that approval-first ordering
produces ABBA deadlock), these tests call the real endpoint handlers via ASGI
HTTP to prove the production session-first lock order serialises cleanly with
concurrent ``create_for_evaluation`` in the same process.

Strategy:
  1. Start ``create_for_evaluation`` in a background task — it locks the
     session row, does its work, and pauses briefly while holding the lock.
  2. Call the endpoint concurrently.  With session-first lock order the
     endpoint serialises cleanly behind cfe (waits for session lock, no
     deadlock).  With the old approval-first order the endpoint would lock
     approval then block on session → ABBA deadlock.

RED verification (that approval-first would deadlock) is done at the
helper-function level in ``test_lock_order.py`` because ASGI transport
cannot support mid-handler lock synchronisation.

Each GREEN test also asserts the endpoint *reached* its business-logic
path (not just "no deadlock").  After the concurrent race resolves, the
DB is queried to confirm the approval transitioned (CONFIRMED / REJECTED)
or the OTP challenge was created.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from safebank_contracts.enums import Risk
from sqlalchemy import func, select, text
from sqlalchemy.pool import NullPool

from services.gateway.app import tables
from services.gateway.app.approval.planview import content_hash
from services.gateway.app.approval.service import create_for_evaluation as _cfe
from services.gateway.app.auth.tokens import sign_jwt
from services.gateway.app.db import engine
from services.gateway.app.schemas.decision import DecisionValue, PreflightView
from services.gateway.app.schemas.planview import PlanView
from tests.gateway.w3b.db.conftest import HMAC_KEY, SERVICE_TOKEN, WALL, build_app
from tests.gateway.w4b2b.db.conftest import (
    _seed_evaluation,
    _seed_full,
    _seed_plan,
)


def _make_planview(
    *, approval_id: str, plan_id: str, evaluation_id: str, session_id: str
) -> PlanView:
    view = PlanView(
        approval_id=approval_id,
        plan_id=plan_id,
        evaluation_id=evaluation_id,
        session_id=session_id,
        status="PENDING_APPROVAL",
        origin="agent",
        risk=Risk.YELLOW,
        steps=(),
        preflight=PreflightView(executable=True),
        findings=[],
        disclosures=(),
        risk_ack_required=False,
        otp_required=False,
        expires_at_wall=(WALL + timedelta(minutes=5)).isoformat(),
        view_hash="",
    )
    object.__setattr__(view, "view_hash", content_hash(view))
    return view


async def _seed_two_plans(conn, *, sid: str, customer: str) -> dict:
    """Seed plan A (PENDING_APPROVAL) + plan B (EVALUATED) on same session.

    Uses the W3b conftest HMAC_KEY so the approval MAC matches what the
    app is configured with (line 40 in w3b conftest).
    """
    PLAN_A, EVAL_A, APV_A = "pln_blk_a", "evl_blk_a", "apv_blk_a"
    PLAN_B, EVAL_B, APV_B = "pln_blk_b", "evl_blk_b", "apv_blk_b"
    VIEW_HASH = "b" * 64

    await _seed_full(
        conn,
        sid=sid,
        customer=customer,
        plan_id=PLAN_A,
        evaluation_id=EVAL_A,
        approval_id=APV_A,
        plan_status="PENDING_APPROVAL",
        approval_status="PENDING",
        view_hash=VIEW_HASH,
        expires_at_wall=WALL + timedelta(minutes=5),
        hmac_key=HMAC_KEY,
    )
    # _seed_auth (from W4b2b conftest) uses W4b2b's WALL (Oct 15);
    # the W3b app uses W3b's WALL (Oct 24).  Update the auth_sessions
    # row so it isn't expired relative to the app's wall clock.  Keep
    # tzinfo so PostgreSQL interprets the value unambiguously.
    await conn.execute(
        tables.auth_sessions.update()
        .where(tables.auth_sessions.c.jti == "test-jti")
        .values(
            created_at_wall=WALL,
            expires_at_wall=WALL + timedelta(hours=2),
        )
    )
    await _seed_plan(
        conn,
        plan_id=PLAN_B,
        sid=sid,
        customer=customer,
        origin="agent",
        status="EVALUATED",
    )
    await _seed_evaluation(
        conn,
        plan_id=PLAN_B,
        evaluation_id=EVAL_B,
        decision=DecisionValue.REQUIRE_APPROVAL,
        risk=Risk.YELLOW,
        view_json={"plan_id": PLAN_B, "risk_ack_required": False},
    )
    return {
        "PLAN_A": PLAN_A,
        "APV_A": APV_A,
        "PLAN_B": PLAN_B,
        "EVAL_B": EVAL_B,
        "APV_B": APV_B,
        "VIEW_HASH": VIEW_HASH,
    }


async def _test_no_deadlock(
    app_config,
    *,
    sid: str,
    customer: str,
    endpoint_path: str,
    request_body: dict,
    verify_fn,
    token_kind: str = "A",
) -> None:
    """GREEN: cfe holds session lock → endpoint serialises, no deadlock.

    After the concurrent race resolves, *verify_fn(admin_engine, ids)* is
    called to assert the endpoint reached its business-logic path
    (CONFIRMED / REJECTED / OTP sent).

    *token_kind* controls the auth headers: ``"A"`` for assistant token
    (used by G3/G6/G7/G10 only via W1 defer-to-sessions indirect paths)
    and ``"F_APPROVE"`` for the browser-facing G6/G7/G10 endpoints that
    require ``F_APPROVE`` (F token + JWT with ``scope=["approve"]``).
    """
    tapp = await build_app(app_config, truncate=True)
    admin_engine = engine(tapp.admin_url, poolclass=NullPool)

    async with admin_engine.begin() as conn:
        ids = await _seed_two_plans(conn, sid=sid, customer=customer)

    APV_A = ids["APV_A"]
    PLAN_B, EVAL_B, APV_B = ids["PLAN_B"], ids["EVAL_B"], ids["APV_B"]

    async with admin_engine.connect() as conn:
        plan_row = await conn.execute(select(tables.plans).where(tables.plans.c.id == PLAN_B))
        plan_b = plan_row.mappings().one()
        eval_row = await conn.execute(
            select(tables.evaluations).where(tables.evaluations.c.id == EVAL_B)
        )
        eval_b = eval_row.mappings().one()

    view_b = _make_planview(approval_id=APV_B, plan_id=PLAN_B, evaluation_id=EVAL_B, session_id=sid)

    # Build auth headers for the endpoint under test.
    if token_kind == "F_APPROVE":
        _jwt = sign_jwt(
            tapp.private_key_pem,
            sub=customer,
            aud="gateway-app",
            scope=["approve"],
            jti="test-jti",
        )
        _headers = {
            "X-Service-Token": SERVICE_TOKEN,
            "Authorization": f"Bearer {_jwt}",
        }
    else:
        _headers = tapp.auth_headers(customer_id=customer, sid=sid)

    endpoint_deadlocked = False

    async def run_cfe_and_hold():
        """Acquire session lock via create_for_evaluation, hold briefly."""
        async with admin_engine.connect() as conn_b:
            async with conn_b.begin() as tx_b:
                await conn_b.execute(text("SET LOCAL lock_timeout = '5000ms'"))
                await _cfe(conn_b, plan=plan_b, evaluation=eval_b, view=view_b)
                # Hold transaction open so endpoint must serialise behind us.
                await asyncio.sleep(1.0)
                await tx_b.rollback()

    async def call_endpoint():
        nonlocal endpoint_deadlocked
        try:
            resp = await tapp.client.post(
                endpoint_path.format(approval_id=APV_A),
                json=request_body,
                headers=_headers,
            )
            # 5xx would indicate a deadlock or other server error.
            if resp.status_code >= 500:
                endpoint_deadlocked = True
        except Exception:
            endpoint_deadlocked = True

    # Start cfe first — it acquires session lock and holds it.
    cfe_task = asyncio.create_task(run_cfe_and_hold())
    await asyncio.sleep(0.3)  # cfe should have acquired session lock by now.

    # Call endpoint — it blocks on session lock until cfe releases.
    ep_task = asyncio.create_task(call_endpoint())

    done, pending = await asyncio.wait([ep_task, cfe_task], timeout=10.0)
    for t in pending:
        t.cancel()
    await asyncio.gather(*pending, return_exceptions=True)

    assert len(pending) == 0, f"TIMEOUT: deadlocked={endpoint_deadlocked}"
    assert not endpoint_deadlocked, "Endpoint returned 5xx or exception (possible deadlock)"

    # ── Verify the endpoint reached its business-logic path ──────────
    await verify_fn(admin_engine, ids)

    await tapp.client.aclose()


# ── Per-endpoint verification helpers ─────────────────────────────────────────


async def _verify_confirmed(admin_engine, ids: dict) -> None:
    """G6 should have transitioned the approval to CONFIRMED."""
    async with admin_engine.connect() as conn:
        row = (
            await conn.execute(
                select(tables.approvals.c.status).where(tables.approvals.c.id == ids["APV_A"])
            )
        ).fetchone()
    assert row is not None, "approval not found after confirm"
    assert row[0] == "CONFIRMED", f"expected CONFIRMED, got {row[0]}"


async def _verify_rejected(admin_engine, ids: dict) -> None:
    """G7 should have transitioned the approval to REJECTED."""
    async with admin_engine.connect() as conn:
        row = (
            await conn.execute(
                select(tables.approvals.c.status).where(tables.approvals.c.id == ids["APV_A"])
            )
        ).fetchone()
    assert row is not None, "approval not found after reject"
    assert row[0] == "REJECTED", f"expected REJECTED, got {row[0]}"


async def _verify_otp_sent(admin_engine, ids: dict) -> None:
    """G10 should have created exactly one OTP challenge for the approval."""
    async with admin_engine.connect() as conn:
        cnt = await conn.scalar(
            select(func.count())
            .select_from(tables.otp_challenges)
            .where(tables.otp_challenges.c.approval_id == ids["APV_A"])
        )
    assert cnt == 1, f"expected 1 OTP challenge, got {cnt}"


# ── GREEN tests ─────────────────────────────────────────────────────────────


def test_g6_confirm_serializes_no_deadlock(app_config):
    asyncio.run(
        _test_no_deadlock(
            app_config,
            sid="ses_b5e_cfm",
            customer="cus_b5e1",
            endpoint_path="/approvals/{approval_id}/confirm",
            request_body={"request_id": "req_cfm", "view_hash": "b" * 64},
            verify_fn=_verify_confirmed,
            token_kind="F_APPROVE",
        )
    )


def test_g7_reject_serializes_no_deadlock(app_config):
    asyncio.run(
        _test_no_deadlock(
            app_config,
            sid="ses_b5e_rej",
            customer="cus_b5e2",
            endpoint_path="/approvals/{approval_id}/reject",
            request_body={"request_id": "req_rej", "view_hash": "b" * 64},
            verify_fn=_verify_rejected,
            token_kind="F_APPROVE",
        )
    )


def test_g10_otp_send_serializes_no_deadlock(app_config):
    asyncio.run(
        _test_no_deadlock(
            app_config,
            sid="ses_b5e_otp",
            customer="cus_b5e3",
            endpoint_path="/approvals/{approval_id}/otp",
            request_body={"request_id": "req_otp", "view_hash": "b" * 64},
            verify_fn=_verify_otp_sent,
            token_kind="F_APPROVE",
        )
    )
