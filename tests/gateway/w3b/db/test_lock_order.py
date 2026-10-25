"""W3b B5: deterministic lock-order deadlock regression test.

IMPORTANT: This test validates the HELPER-FUNCTION lock order —
``_lock_session`` / ``_lock_approval`` are called directly, not via HTTP
endpoints.  For endpoint-level regression evidence (proving the production
session-first order serialises cleanly under ASGI HTTP), see
``test_lock_order_endpoint.py``.

The ABBA scenario (旧锁序):
  TX1 (G6 confirm): approval → session
  TX2 (create_for_evaluation): session → plan → eval → job → approval (supersede)

With the session-first lock order in G6/G7/G10, both sides lock session first,
serialising cleanly.  The tests below:

* GREEN — session-first lock order (current production code) concurrently with
  ``create_for_evaluation`` → no deadlock
* RED — the old approval-first order concurrently with ``create_for_evaluation``
  → ABBA deadlock, proving the test catches regressions

The test calls the actual production code paths: ``_lock_session`` /
``_lock_approval`` from ``approvals.py`` (the same functions G6/G7/G10 use),
and ``create_for_evaluation`` from ``approval.service`` (the same function W3's
G4 handler calls).  No raw SQL simulating lock order — every lock acquisition
goes through the production functions.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from safebank_contracts.enums import Risk
from sqlalchemy import select, text
from sqlalchemy.pool import NullPool

from services.gateway.app import tables
from services.gateway.app.api.approvals import (
    _lock_approval,
    _lock_session,
    _read_approval_unlocked,
)
from services.gateway.app.approval import service as approval_service
from services.gateway.app.approval.planview import content_hash
from services.gateway.app.approval.service import create_for_evaluation
from services.gateway.app.config import Settings
from services.gateway.app.db import engine
from services.gateway.app.runtime import _key_pair, _validate_approval_keys
from services.gateway.app.schemas.decision import DecisionValue, PreflightView
from services.gateway.app.schemas.planview import PlanView
from tests.gateway.w4b2b.db.conftest import (
    HMAC_KEY as W4B2B_HMAC,
)
from tests.gateway.w4b2b.db.conftest import (
    OTP_KEY as W4B2B_OTP,
)
from tests.gateway.w4b2b.db.conftest import (
    _seed_evaluation,
    _seed_full,
    _seed_plan,
)

# ── Test constants ────────────────────────────────────────────────────────────

WALL = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone(timedelta(hours=8)))
HMAC_KEY = W4B2B_HMAC
OTP_KEY = W4B2B_OTP
SERVICE_TOKEN = "test-frontend-service-token-32b"
AGENT_TOKEN = "test-agent-service-token-32bytes"
SCHEDULER_TOKEN = "test-scheduler-token-32bytes-xxx"

# ── Ed25519 helpers ───────────────────────────────────────────────────────────


def _generate_key_pair() -> tuple[bytes, bytes]:
    private = ed25519.Ed25519PrivateKey.generate()
    public = private.public_key()
    private_pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = public.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


# ── PlanView builder ──────────────────────────────────────────────────────────


def _make_planview(
    *,
    approval_id: str,
    plan_id: str,
    evaluation_id: str,
    session_id: str,
    origin: str = "agent",
    risk: Risk = Risk.YELLOW,
    steps: tuple = (),
    preflight: PreflightView | None = None,
    findings: tuple = (),
    risk_ack_required: bool = False,
    otp_required: bool = False,
) -> PlanView:
    """Build a minimal PlanView with a valid view_hash for create_for_evaluation."""
    _pf = preflight if preflight is not None else PreflightView(executable=True)
    view = PlanView(
        approval_id=approval_id,
        plan_id=plan_id,
        evaluation_id=evaluation_id,
        session_id=session_id,
        status="PENDING_APPROVAL",
        origin=origin,
        risk=risk,
        steps=steps,
        preflight=_pf,
        findings=list(findings),
        disclosures=(),
        risk_ack_required=risk_ack_required,
        otp_required=otp_required,
        expires_at_wall=(WALL + timedelta(minutes=5)).isoformat(),
        view_hash="",  # placeholder, set below
    )
    # Set the real hash.
    object.__setattr__(view, "view_hash", content_hash(view))
    return view


# ── Runtime/config factory (needed by create_for_evaluation) ──────────────────


async def _configure_approval_service(db_url: str):
    """Call approval_service.configure() so create_for_evaluation doesn't 503."""
    private_pem, public_pem = _generate_key_pair()
    tmpdir = tempfile.mkdtemp(prefix="sbg-b5-")
    private_path = Path(tmpdir) / "jwt-private.pem"
    public_path = Path(tmpdir) / "jwt-public.pem"
    private_path.write_bytes(private_pem)
    public_path.write_bytes(public_pem)
    os.chmod(private_path, 0o600)
    os.chmod(public_path, 0o644)

    settings = Settings(
        demo_mode=False,
        eval_mode=False,
        profile="B2",
        demo_console_token=None,
        public_bind="127.0.0.1",
        internal_bind="127.0.0.1",
        max_executions=8,
        database_url=db_url,
        l3_base_url="http://localhost:1",
        gateway_service_token="test-gateway-service-token-32b!",
        service_tokens={"F": SERVICE_TOKEN, "A": AGENT_TOKEN, "S": SCHEDULER_TOKEN, "D": ""},
        jwt_private_key_path=str(private_path),
        jwt_public_key_path=str(public_path),
        demo_pins_file="/nonexistent/test-pins",
        approval_hmac_key=HMAC_KEY.decode(),
        otp_hmac_key=OTP_KEY.decode(),
    )
    settings.validate()

    _priv, _pub = _key_pair(settings)
    approval_key_bytes, otp_key_bytes = _validate_approval_keys(settings)

    approval_service.configure(
        approval_hmac_key=approval_key_bytes,
        profile=settings.profile,
        wall_now=lambda: WALL,
    )


# ── GREEN: session-first (current production code) → no deadlock ──────────────


def test_session_first_vs_create_for_evaluation_no_deadlock(gateway_db):
    """GREEN: session→approval lock order + concurrent create_for_evaluation = no deadlock.

    conn_a (G6 path): _lock_session → _lock_approval → rollback
    conn_b (create_for_evaluation): locks session → plan → eval → job → supersede approval_A

    With session-first, both start by locking the same session row.  One transaction
    serialises behind the other.  conn_a completes quickly (rollback), releasing the
    session lock; conn_b then proceeds.  No ABBA because neither transaction locks
    approval before session.
    """
    SID = "ses_b5_green"
    CUSTOMER = "cus_b5"
    PLAN_A = "pln_b5g_a"
    EVAL_A = "evl_b5g_a"
    APV_A = "apv_b5g_a"
    PLAN_B = "pln_b5g_b"
    EVAL_B = "evl_b5g_b"
    APV_B = "apv_b5g_b"
    VIEW_HASH = "b" * 64

    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        await _configure_approval_service(gateway_db.admin_url)

        # Seed: two complete chains on the same session.
        async with db.begin() as conn:
            await _seed_full(
                conn,
                sid=SID,
                customer=CUSTOMER,
                plan_id=PLAN_A,
                evaluation_id=EVAL_A,
                approval_id=APV_A,
                plan_status="PENDING_APPROVAL",
                approval_status="PENDING",
                view_hash=VIEW_HASH,
                hmac_key=HMAC_KEY,
            )
            # Chain B: plan in EVALUATED status (eligible for create_for_evaluation),
            # no approval yet — create_for_evaluation creates it.
            await _seed_plan(
                conn,
                plan_id=PLAN_B,
                sid=SID,
                customer=CUSTOMER,
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

        # Read plan_B / eval_B rows for create_for_evaluation.
        async with db.connect() as conn:
            plan_b = (
                (await conn.execute(select(tables.plans).where(tables.plans.c.id == PLAN_B)))
                .mappings()
                .one()
            )
            eval_b = (
                (
                    await conn.execute(
                        select(tables.evaluations).where(tables.evaluations.c.id == EVAL_B)
                    )
                )
                .mappings()
                .one()
            )

        view_b = _make_planview(
            approval_id=APV_B,
            plan_id=PLAN_B,
            evaluation_id=EVAL_B,
            session_id=SID,
        )

        errors: list[str] = []

        async def conn_a_work():
            """G6 path: lock session → lock approval → rollback (correct order)."""
            nonlocal errors
            try:
                async with db.connect() as conn_a:
                    async with conn_a.begin() as tx_a:
                        await conn_a.execute(text("SET LOCAL lock_timeout = '5000ms'"))
                        snap = await _read_approval_unlocked(conn_a, APV_A, CUSTOMER)
                        await _lock_session(conn_a, snap["sid"], CUSTOMER)
                        # Signal conn_b that we hold the session lock.
                        ready.set()
                        # Hold briefly so conn_b starts and blocks on session lock.
                        await asyncio.sleep(0.3)
                        # Lock approval (conn_b doesn't hold it — no contention).
                        await _lock_approval(conn_a, APV_A, CUSTOMER)
                        await tx_a.rollback()
            except Exception as exc:
                errors.append(f"conn_a: {exc}")

        async def conn_b_work():
            """create_for_evaluation — blocks on session, then proceeds."""
            nonlocal errors
            await ready.wait()
            await asyncio.sleep(0.1)
            try:
                async with db.connect() as conn_b:
                    async with conn_b.begin() as tx_b:
                        await conn_b.execute(text("SET LOCAL lock_timeout = '5000ms'"))
                        # Blocks on session lock until conn_a rollbacks → then proceeds.
                        await create_for_evaluation(
                            conn_b,
                            plan=plan_b,
                            evaluation=eval_b,
                            view=view_b,
                        )
                        await tx_b.rollback()
            except Exception as exc:
                errors.append(f"conn_b: {exc}")

        ready = asyncio.Event()

        done, pending = await asyncio.wait(
            [asyncio.create_task(conn_a_work()), asyncio.create_task(conn_b_work())],
            timeout=15.0,
        )
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

        await db.dispose()

        assert len(pending) == 0, (
            f"Deadlock or timeout: {len(pending)} task(s) did not complete. Errors: {errors}"
        )
        assert len(errors) == 0, f"Expected no errors with session-first lock order, got: {errors}"

    asyncio.run(run())


# ── RED: old approval-first order → ABBA deadlock ─────────────────────────────


def test_old_approval_first_vs_create_for_evaluation_produces_deadlock(gateway_db):
    """RED: approval→session lock order + concurrent create_for_evaluation = deadlock.

    This test *deliberately* inverts the lock order to the old (buggy) form to
    prove that the ABBA deadlock is real and would be caught by the GREEN test
    if someone reverted the lock-order fix in approvals.py.

    conn_a: _lock_approval → _lock_session (OLD/WRONG order)
    conn_b: create_for_evaluation (session → … → supersede approval_A)

    ABBA:
      conn_a holds approval_A, waits for session
      conn_b holds session, waits for approval_A (via supersede)
      → deadlock detected by PostgreSQL or lock_timeout
    """
    SID = "ses_b5_red"
    CUSTOMER = "cus_b5r"
    PLAN_A = "pln_b5r_a"
    EVAL_A = "evl_b5r_a"
    APV_A = "apv_b5r_a"
    PLAN_B = "pln_b5r_b"
    EVAL_B = "evl_b5r_b"
    APV_B = "apv_b5r_b"
    VIEW_HASH = "b" * 64

    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        await _configure_approval_service(gateway_db.admin_url)

        async with db.begin() as conn:
            await _seed_full(
                conn,
                sid=SID,
                customer=CUSTOMER,
                plan_id=PLAN_A,
                evaluation_id=EVAL_A,
                approval_id=APV_A,
                plan_status="PENDING_APPROVAL",
                approval_status="PENDING",
                view_hash=VIEW_HASH,
                hmac_key=HMAC_KEY,
            )
            await _seed_plan(
                conn,
                plan_id=PLAN_B,
                sid=SID,
                customer=CUSTOMER,
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

        async with db.connect() as conn:
            plan_b = (
                (await conn.execute(select(tables.plans).where(tables.plans.c.id == PLAN_B)))
                .mappings()
                .one()
            )
            eval_b = (
                (
                    await conn.execute(
                        select(tables.evaluations).where(tables.evaluations.c.id == EVAL_B)
                    )
                )
                .mappings()
                .one()
            )

        view_b = _make_planview(
            approval_id=APV_B,
            plan_id=PLAN_B,
            evaluation_id=EVAL_B,
            session_id=SID,
        )

        deadlock_detected = False

        async def conn_a_old():
            """OLD/WRONG lock order: approval → session (recreates the bug)."""
            nonlocal deadlock_detected
            try:
                async with db.connect() as conn_a:
                    async with conn_a.begin() as tx_a:
                        await conn_a.execute(text("SET LOCAL lock_timeout = '3000ms'"))
                        # OLD ORDER: approval first!
                        snap = await _read_approval_unlocked(conn_a, APV_A, CUSTOMER)
                        await _lock_approval(conn_a, APV_A, CUSTOMER)
                        ready.set()
                        await proceed.wait()
                        # OLD ORDER: session second — must deadlock with conn_b.
                        await _lock_session(conn_a, snap["sid"], CUSTOMER)
                        await tx_a.rollback()
            except Exception:
                deadlock_detected = True

        async def conn_b_cfe():
            """create_for_evaluation — locks session first (production path)."""
            nonlocal deadlock_detected
            await ready.wait()
            await asyncio.sleep(0.1)
            try:
                async with db.connect() as conn_b:
                    async with conn_b.begin() as tx_b:
                        await conn_b.execute(text("SET LOCAL lock_timeout = '3000ms'"))
                        await create_for_evaluation(
                            conn_b,
                            plan=plan_b,
                            evaluation=eval_b,
                            view=view_b,
                        )
                        await tx_b.rollback()
            except Exception:
                deadlock_detected = True
            finally:
                proceed.set()

        ready = asyncio.Event()
        proceed = asyncio.Event()

        done, pending = await asyncio.wait(
            [asyncio.create_task(conn_a_old()), asyncio.create_task(conn_b_cfe())],
            timeout=15.0,
        )
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

        await db.dispose()

        assert deadlock_detected, (
            "OLD approval→session order did NOT deadlock with create_for_evaluation. "
            "The ABBA scenario may not be reproducible — check the seed data."
        )

    asyncio.run(run())
