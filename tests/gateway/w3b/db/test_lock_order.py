"""W3b B5: global lock-order fix — deadlock regression tests.

ABBA scenario（旧锁序）:
  TX1（G6 confirm）：先锁 approval → 再锁 session
  TX2（W4b1 create_for_evaluation）：先锁 session → 后锁 approval

修复后统一锁序：session → plan → evaluation → approval → otp_challenge。

Each test uses raw SQL FOR UPDATE to directly reproduce lock ordering.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from sqlalchemy import select, text
from sqlalchemy.pool import NullPool

from services.gateway.app import tables as t
from services.gateway.app.db import engine
from tests.gateway.w4b2b.db.conftest import (
    HMAC_KEY,
    WALL,
    _seed_full,
    _seed_session,
)

NOW = WALL + timedelta(minutes=1)


def _lock_row(table, rid):
    """SELECT ... FOR UPDATE."""
    return select(table).where(table.c.id == rid).with_for_update()


# ── T1: ABBA deadlock with old order (approval→session vs session→approval) ──


def test_old_order_approval_then_session_forms_abba(gateway_db):
    """Red test: approval→session vs session→approval → ABBA deadlock.

    connA: lock approval → sleep 0.3s → lock session
    connB: lock session  → sleep 0.3s → lock approval

    These cross-lock in opposite order. Under lock_timeout, PG deadlock
    detection cancels one transaction.
    """

    SID = "ses_abba"
    PLAN_ID = "pln_abba"
    EVAL_ID = "evl_abba"
    APV_ID = "apv_abba"

    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)

        async with db.begin() as conn:
            await _seed_full(
                conn,
                sid=SID,
                plan_id=PLAN_ID,
                evaluation_id=EVAL_ID,
                approval_id=APV_ID,
                hmac_key=HMAC_KEY,
            )

        deadlock_seen = False

        async def conn_a():
            nonlocal deadlock_seen
            async with db.connect() as c:
                async with c.begin():
                    await c.execute(text("SET LOCAL lock_timeout = '3000ms'"))
                    await c.execute(_lock_row(t.approvals, APV_ID))
                    await asyncio.sleep(0.3)
                    try:
                        await c.execute(_lock_row(t.sessions, SID))
                    except Exception:
                        deadlock_seen = True
                    finally:
                        await c.rollback()

        async def conn_b():
            nonlocal deadlock_seen
            await asyncio.sleep(0.05)
            async with db.connect() as c:
                async with c.begin():
                    await c.execute(text("SET LOCAL lock_timeout = '3000ms'"))
                    await c.execute(_lock_row(t.sessions, SID))
                    await asyncio.sleep(0.4)
                    try:
                        await c.execute(_lock_row(t.approvals, APV_ID))
                    except Exception:
                        deadlock_seen = True
                    finally:
                        await c.rollback()

        await asyncio.gather(conn_a(), conn_b(), return_exceptions=True)

        await db.dispose()
        return deadlock_seen

    deadlock_seen = asyncio.run(run())

    assert deadlock_seen, (
        "OLD lock order did NOT trigger deadlock; ABBA should form between "
        "connA (approval→session) and connB (session→approval)"
    )


# ── T2: NEW lock order (both session-first) → no deadlock ────────────


def test_new_order_session_first_no_deadlock(gateway_db):
    """Green test: both txns lock session first → serialize, no ABBA."""

    SID = "ses_noabba"
    PLAN_ID = "pln_noabba"
    EVAL_ID = "evl_noabba"
    APV_ID = "apv_noabba"

    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)

        async with db.begin() as conn:
            await _seed_full(
                conn,
                sid=SID,
                plan_id=PLAN_ID,
                evaluation_id=EVAL_ID,
                approval_id=APV_ID,
                hmac_key=HMAC_KEY,
            )

        a_ok = False

        async def conn_a():
            nonlocal a_ok
            async with db.connect() as c:
                async with c.begin():
                    await c.execute(text("SET LOCAL lock_timeout = '2000ms'"))
                    await c.execute(_lock_row(t.sessions, SID))
                    await asyncio.sleep(0.3)
                    await c.execute(_lock_row(t.approvals, APV_ID))
                    a_ok = True
                    await c.rollback()

        async def conn_b():
            await asyncio.sleep(0.1)
            async with db.connect() as c:
                async with c.begin():
                    await c.execute(text("SET LOCAL lock_timeout = '2000ms'"))
                    try:
                        await c.execute(_lock_row(t.sessions, SID))
                    except Exception:
                        pass
                    await c.rollback()

        await asyncio.gather(conn_a(), conn_b(), return_exceptions=True)

        await db.dispose()
        return a_ok

    a_ok = asyncio.run(run())

    assert a_ok, "connA should complete without deadlock (session-first order)"


# ── T3: Unified lock order (session → plan → eval → approval) ────────


def test_unified_lock_order_session_plan_eval_approval_no_deadlock(gateway_db):
    """Full lock-order test: session→plan→eval→approval on different plans.

    TX1: session → plan_a → eval_a → approval_a
    TX2: session → plan_b → eval_b → approval_b (different plan/approval, same session)

    Both start with session FOR UPDATE. TX2 serializes behind TX1.
    No ABBA because all paths use the same order.
    """

    SID = "ses_unified"
    PLAN_A = "pln_u_a"
    EVAL_A = "evl_u_a"
    APV_A = "apv_u_a"
    PLAN_B = "pln_u_b"
    EVAL_B = "evl_u_b"
    APV_B = "apv_u_b"

    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)

        async with db.begin() as conn:
            await _seed_session(conn, sid=SID)
            await _seed_full(
                conn,
                sid=SID,
                plan_id=PLAN_A,
                evaluation_id=EVAL_A,
                approval_id=APV_A,
                hmac_key=HMAC_KEY,
            )

        # Seed plan B separately — SUPERSEDED to avoid one_pending_approval_per_session
        async with db.begin() as conn:
            await _seed_full(
                conn,
                sid=SID,
                plan_id=PLAN_B,
                evaluation_id=EVAL_B,
                approval_id=APV_B,
                approval_status="SUPERSEDED",
                plan_status="SUPERSEDED",
                hmac_key=HMAC_KEY,
            )

        tx1_ok = False
        tx2_ok = False
        errors = []

        async def tx1():
            nonlocal tx1_ok
            async with db.connect() as c:
                async with c.begin():
                    await c.execute(text("SET LOCAL lock_timeout = '3000ms'"))
                    try:
                        await c.execute(_lock_row(t.sessions, SID))
                        await asyncio.sleep(0.2)
                        await c.execute(_lock_row(t.plans, PLAN_A))
                        await c.execute(_lock_row(t.evaluations, EVAL_A))
                        await c.execute(_lock_row(t.approvals, APV_A))
                        tx1_ok = True
                    except Exception as exc:
                        errors.append(f"tx1: {exc}")
                    finally:
                        await c.rollback()

        async def tx2():
            nonlocal tx2_ok
            await asyncio.sleep(0.05)
            async with db.connect() as c:
                async with c.begin():
                    await c.execute(text("SET LOCAL lock_timeout = '3000ms'"))
                    try:
                        await c.execute(_lock_row(t.sessions, SID))
                        await c.execute(_lock_row(t.plans, PLAN_B))
                        await c.execute(_lock_row(t.evaluations, EVAL_B))
                        await c.execute(_lock_row(t.approvals, APV_B))
                        tx2_ok = True
                    except Exception as exc:
                        errors.append(f"tx2: {exc}")
                    finally:
                        await c.rollback()

        await asyncio.gather(tx1(), tx2(), return_exceptions=True)

        await db.dispose()
        return tx1_ok, tx2_ok, errors

    tx1_ok, tx2_ok, errors = asyncio.run(run())

    assert tx1_ok, f"TX1 failed with unified lock order: {errors}"
    assert tx2_ok, f"TX2 failed with unified lock order: {errors}"
