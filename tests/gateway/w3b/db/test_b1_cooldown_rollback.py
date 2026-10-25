"""W3b B1 RED: cooldown rollback — prove cooldowns vanish when the write transaction fails.

Proves B1 atomicity: if ANYTHING fails after ``_persist_cooldowns`` but before
the outer write transaction commits, NO cooldown residue remains in the database.

RED verification (temporary, not committed):
  Modify ``_persist_cooldowns`` to commit on a *separate* connection — the
  test MUST fail (cooldown survives despite the outer rollback).  Reverting
  restores the correct atomic behaviour and the test passes.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from sqlalchemy import select

from services.gateway.app import tables as t
from tests.gateway.w3b.db.conftest import WALL, _seed_full, _seed_workflow_event, build_app
from tests.gateway.w3b.db.test_g4_evaluate import (
    EVALUATE_URL,
    _body,
    _runtime_db,
    _seed_provenance_data,
    _step_with_sources,
)

# ── helpers ────────────────────────────────────────────────────────────────────


async def _count_cooldowns_for(conn, *, customer_id: str, fingerprint: str) -> int:
    rows = (
        await conn.execute(
            select(t.cooldowns).where(
                t.cooldowns.c.customer_id == customer_id,
                t.cooldowns.c.payee_fingerprint == fingerprint,
            )
        )
    ).fetchall()
    return len(rows)


async def _count_evaluations_for(conn, *, plan_id: str) -> int:
    rows = (
        await conn.execute(select(t.evaluations).where(t.evaluations.c.plan_id == plan_id))
    ).fetchall()
    return len(rows)


async def _count_plans_for(conn, *, session_id: str) -> int:
    rows = (await conn.execute(select(t.plans).where(t.plans.c.sid == session_id))).fetchall()
    return len(rows)


async def _count_audit_for(conn, *, plan_id: str) -> int:
    rows = (
        await conn.execute(select(t.audit_events).where(t.audit_events.c.plan_id == plan_id))
    ).fetchall()
    return len(rows)


# ── test ───────────────────────────────────────────────────────────────────────


def test_cooldown_rollback_on_transaction_failure(app_config):
    """B1 RED: cooldowns vanish when the write transaction rolls back.

    Seeds a RISKY_SEQUENCE scenario, then monkeypatches ``append_evaluation``
    right BEFORE it is called (after cooldowns have been persisted) to raise.
    The outer transaction must roll back, leaving ZERO residue.
    """
    TRACKING: dict = {}

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)
        sid = "ses_b1rb"
        jti = "jti-b1rb"

        # ── seed ──────────────────────────────────────────────────────────
        async with db.begin() as conn:
            await _seed_full(conn, sid=sid, jti_auth=jti)
            rid_acc, rid_pay = await _seed_provenance_data(conn, sid)
            await _seed_workflow_event(
                conn,
                event_id="wfe_b1rb_pay",
                action_kind="PAYEE_ADD",
                target_id="fp_ext",
                occurred_at=WALL - timedelta(hours=2),
            )
            await _seed_workflow_event(
                conn,
                event_id="wfe_b1rb_card",
                action_kind="CARD_LIMIT_LOOSEN",
                occurred_at=WALL - timedelta(hours=1),
            )

        # ── count before ──────────────────────────────────────────────────
        async with db.connect() as conn:
            cooldowns_before = await _count_cooldowns_for(
                conn, customer_id="cus_a", fingerprint="fp_ext"
            )

        # ── inject failure ────────────────────────────────────────────────
        # We patch the append_evaluation imported in plans.py to raise AFTER
        # _persist_cooldowns has already written (but within the same tx).
        original_append = None
        import services.gateway.app.api.plans as plans_mod

        async def failing_append(*args, **kwargs):
            TRACKING["append_called"] = True
            raise RuntimeError("simulated post-cooldown crash (B1 rollback test)")

        original_append = plans_mod.append_evaluation
        plans_mod.append_evaluation = failing_append

        try:
            step = _step_with_sources(sid, rid_acc, rid_pay, amount_cents=80000)
            body = _body("req_b1rb", sid, [f"utt_{sid}_1"], steps=[step])
            hdrs = app.auth_headers(sid=sid, jti=jti, utts=[f"utt_{sid}_1"])

            # The injected RuntimeError will propagate through the ASGI
            # transport as an unhandled exception because FastAPI's
            # default 500 handler may re-raise it.  Catch it and confirm
            # the DB was rolled back.
            resp = None
            plan_id_from_resp = None
            try:
                resp = await app.client.post(EVALUATE_URL, json=body, headers=hdrs)
            except RuntimeError:
                pass  # expected — the injected failure propagates
            except Exception:
                pass  # catch any other propagation

            if resp is not None and resp.status_code != 200:
                # If we got a proper error response, extract plan_id.
                try:
                    plan_id_from_resp = resp.json().get("plan_id")
                except Exception:
                    pass
        finally:
            plans_mod.append_evaluation = original_append

        # ── B1 assertion: zero cooldown residue ───────────────────────────
        async with db.connect() as conn:
            cooldowns_after = await _count_cooldowns_for(
                conn, customer_id="cus_a", fingerprint="fp_ext"
            )
        assert cooldowns_after == cooldowns_before, (
            f"B1 FAILED: cooldowns leaked after rollback "
            f"(before={cooldowns_before}, after={cooldowns_after})"
        )

        # ── no evaluation / plan / audit residue ──────────────────────────

        async with db.connect() as conn:
            if plan_id_from_resp:
                evals = await _count_evaluations_for(conn, plan_id=plan_id_from_resp)
                assert evals == 0, f"evaluation leaked for {plan_id_from_resp}: got {evals}"

            plans_count = (
                await conn.execute(
                    select(t.plans).where(
                        t.plans.c.sid == sid,
                        t.plans.c.status == "EVALUATED",
                    )
                )
            ).fetchall()
            assert len(plans_count) == 0, f"plan(s) leaked after rollback: {len(plans_count)}"

            # Check audit log — if a plan_id leaked into the response we look
            # for logged target; otherwise we just assert nothing with our sid.
            if plan_id_from_resp:
                audit_for_plan = await _count_audit_for(conn, plan_id=plan_id_from_resp)
                assert audit_for_plan == 0, (
                    f"audit log leaked for {plan_id_from_resp}: {audit_for_plan} rows"
                )

        # ── append_evaluation was actually called (test is valid) ─────────
        assert TRACKING.get("append_called"), (
            "append_evaluation was never called — test injection is invalid"
        )

        await db.dispose()

    asyncio.run(run())
