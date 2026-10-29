"""S1 (W5): scheduler callback, the only internal business route.

``POST /internal/scheduled/authorize`` (PRD §9.5a). Called by the L3 scheduler on
bank-net :8201 with ``SCHEDULER_SERVICE_TOKEN`` (caller kind S, no JWT). S1 never
trusts callback amounts: it re-fetches the L3 event definition and re-evaluates
the transfer on a fresh snapshot before authorizing execution.

Concurrency is handled with a two-phase stale-claim pattern (no long-held locks
on pooled connections):

1. **Phase A — claim** (short, with advisory lock): atomically try to become the
   exclusive handler for this event_id by inserting an ``IN_PROGRESS`` row into
   ``event_decisions``.  If a terminal row already exists the stored result is
   returned immediately.  If another callback's ``IN_PROGRESS`` row is fresh,
   respond ``RETRYING``.  If the row is stale (older than the claim lease),
   take it over.  The advisory lock is released and the connection returned
   to the pool *before* any L3 HTTP call.

2. **Phase B — evaluate** (no lock, no held connection): fetch the L3 event
   definition, load or backfill the grant, re-evaluate on a fresh snapshot,
   and call ``event_execute`` or ``event_block`` on L3.  All HTTP.

3. **Phase C — finalize** (short, with advisory lock): in a second short
   transaction, atomically promote the ``IN_PROGRESS`` row to the terminal
   status (SUCCESS / BLOCKED / …) together with its audit record.  When L3
   was unavailable during Phase B the claim row is **deleted** so the next
   callback can try immediately (the handler returns ``RETRYING``).
"""

from __future__ import annotations

import enum
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel
from safebank_contracts.codes import Code
from safebank_contracts.plan import PlanDraft, ScheduledEvidence
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .. import tables as t
from ..audit.chain import append as audit_append
from ..errors import GatewayError
from ..l3.client import L3Error
from ..pipeline import contract, decision, entity, preflight, snapshot, workflow
from ..pipeline import evaluate as run_pipeline
from ..pipeline.context import EvalContext, EvalMode
from ..schemas.decision import DecisionValue
from ..schemas.enums import ServiceKind
from ..utterance.service import new_id

router = APIRouter()

# Risk ordering (PRD §7.9): risk only ever rises; compare by order.
_RISK_ORDER = {"GREEN": 0, "YELLOW": 1, "RED": 2}

# Re-evaluation stages: ①contract ②entity ⑤snapshot ⑥preflight ⑦workflow ⑧decision.
# ③provenance / ④intent are excluded — the grant was already authorized, and the
# synthetic app_form draft has no session-scoped read records to taint against.
_STAGES = (
    contract.stage,
    entity.stage,
    snapshot.stage,
    preflight.stage,
    workflow.stage,
    decision.stage,
)

_TERMINAL_STATUSES = frozenset({"SUCCESS", "BLOCKED", "FAILED_INSUFFICIENT", "SKIPPED_REAPPROVAL"})

# Stale-claim lease: an IN_PROGRESS row older than this can be taken over by the
# next callback (the original handler is presumed dead).
_CLAIM_LEASE = timedelta(minutes=5)


class _ClaimResult(enum.StrEnum):
    CLAIMED = "claimed"
    TERMINAL = "terminal"
    RETRYING = "retrying"


class AuthorizeRequest(BaseModel):
    event_id: str
    request_id: str | None = None
    attempt: int = 1


def _retrying(event_id: str) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "status": "RETRYING",
        "code": Code.DEPENDENCY_UNAVAILABLE.value,
    }


def _execution_in_progress(event_id: str) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "status": "RETRYING",
        "code": Code.EXECUTION_IN_PROGRESS.value,
    }


def _build_plan_draft(event: dict[str, Any]) -> PlanDraft:
    """Synthesize the single-step transfer draft the grant was created for.

    ``origin="app_form"`` (avoids ENTITY_UNSEEN) and the excluded provenance /
    intent stages mean the synthetic session/utterance IDs are never resolved
    against a real session.
    """
    args: dict[str, Any] = {
        "from_account_id": event["account_id"],
        "payee_id": event.get("payee_id"),
        "to_account_id": event.get("to_account_id"),
        "amount_cents": event["amount_cents"],
        "memo": None,
    }
    return PlanDraft.model_validate(
        {
            "request_id": f"req_{event['event_id']}",
            "session_id": f"sid_{event['event_id']}",
            "utterance_ids": [f"utt_{event['event_id']}"],
            "intents": ["intent_scheduled_transfer"],
            "steps": [
                {
                    "tool": "transfer",
                    "step_id": "stp_scheduled",
                    "depends_on": [],
                    "arg_sources": [],
                    "args": args,
                }
            ],
            "rationale": "scheduled transfer grant execution",
        }
    )


# ── Phase A: claim ────────────────────────────────────────────────────────────


async def _claim_event(db, event_id: str, wall_now) -> tuple[_ClaimResult, dict[str, Any] | None]:
    """Atomically claim the event in a short locked transaction.

    Returns (CLAIMED, None) when this callback is the exclusive handler.
    Returns (TERMINAL, stored-dict) when a terminal decision already exists.
    Returns (RETRYING, dict) when another callback is already handling it.

    The advisory lock is released and the connection returned to the pool
    before the caller continues — no lock is held across L3 HTTP calls.
    """
    lock_key = f"s1_event:{event_id}"
    async with db.connect() as conn:
        acquired = (
            await conn.execute(
                text("SELECT pg_try_advisory_lock(hashtext(:key))"),
                {"key": lock_key},
            )
        ).scalar()
        if not acquired:
            return _ClaimResult.RETRYING, _execution_in_progress(event_id)

        try:
            row = (
                await conn.execute(
                    select(t.event_decisions).where(t.event_decisions.c.event_id == event_id)
                )
            ).first()

            if row is not None:
                if row.status in _TERMINAL_STATUSES:
                    return _ClaimResult.TERMINAL, {
                        "event_id": event_id,
                        "status": row.status,
                        "code": row.code,
                    }

                # IN_PROGRESS row exists — check staleness.
                age = wall_now() - row.decided_at_wall
                if age < _CLAIM_LEASE:
                    return _ClaimResult.RETRYING, _execution_in_progress(event_id)

                # Stale — take over.
                await conn.execute(
                    text(
                        "UPDATE event_decisions SET decided_at_wall = :now "
                        "WHERE event_id = :eid AND status = 'IN_PROGRESS'"
                    ),
                    {"now": wall_now(), "eid": event_id},
                )
            else:
                # No row — insert a fresh claim.
                await conn.execute(
                    text(
                        "INSERT INTO event_decisions (event_id, status, "
                        "decided_at_wall) VALUES (:eid, 'IN_PROGRESS', :now)"
                    ),
                    {"eid": event_id, "now": wall_now()},
                )

            await conn.commit()
            return _ClaimResult.CLAIMED, None

        finally:
            await conn.execute(
                text("SELECT pg_advisory_unlock(hashtext(:key))"),
                {"key": lock_key},
            )


# ── Phase C: finalize / release ───────────────────────────────────────────────


async def _finalize_claim(
    db,
    event_id: str,
    customer_id: str,
    status: str,
    code: str | None,
    wall_now,
    trace_id: str | None,
) -> None:
    """Promote the IN_PROGRESS row to a terminal status with audit in one
    transaction.  Only succeeds if the row is still IN_PROGRESS."""
    async with db.begin() as conn:
        result = await conn.execute(
            text(
                "UPDATE event_decisions SET status = :status, code = :code, "
                "decided_at_wall = :now "
                "WHERE event_id = :eid AND status = 'IN_PROGRESS'"
            ),
            {"status": status, "code": code, "now": wall_now, "eid": event_id},
        )
        if result.rowcount == 0:
            # Someone else already finalized or took over — nothing to do.
            return

        try:
            await audit_append(
                conn,
                customer_id=customer_id,
                event_type="SCHEDULE_RESULT",
                payload={"event_id": event_id, "status": status, "code": code},
                event_id=new_id("evt"),
                trace_id=trace_id,
                plan_id=None,
                now_wall=wall_now,
            )
        except Exception as exc:
            raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503, "audit write failed") from exc


async def _release_claim(db, event_id: str) -> None:
    """Delete the IN_PROGRESS claim row so the next callback can try."""
    async with db.begin() as conn:
        await conn.execute(
            text("DELETE FROM event_decisions WHERE event_id = :eid AND status = 'IN_PROGRESS'"),
            {"eid": event_id},
        )


# ── Phase B helpers (no lock, no held connection) ─────────────────────────────


async def _re_evaluate(request: Request, event: dict[str, Any]) -> EvalContext:
    """Run the grant re-evaluation stages on a fresh L3 snapshot."""
    app = request.app.state
    plan_draft = _build_plan_draft(event)
    ctx = EvalContext(
        plan_draft=plan_draft,
        customer_id=event["customer_id"],
        session_id=plan_draft.session_id,
        plan_id=new_id("pln"),
        origin="app_form",
        identity=getattr(request.state, "caller", None),
        profile="B2",
        mode=EvalMode.EXECUTE_REPLAY,
        l3=app.l3,
        wall_now=app.wall_now,
    )
    async with app.db.connect() as conn:
        ctx.conn = conn
        await run_pipeline(ctx, stages=_STAGES)
    return ctx


async def _load_grant(conn, instruction_id: str) -> dict[str, Any] | None:
    row = (
        (
            await conn.execute(
                select(t.scheduled_grants).where(
                    t.scheduled_grants.c.instruction_id == instruction_id
                )
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


async def _backfill_grant(db, l3, event: dict[str, Any], wall_now) -> dict[str, Any] | None:
    """Reconstruct a missing grant from the L3 committed execution of the
    source plan (PRD §7.11). Returns None when the grant cannot be established
    (caller distinguishes L3 unreachable via L3Error).

    ``approved_risk`` cannot be recovered from the immutable event definition;
    it is read from the committed execution result. A missing value fails closed
    to the transfer base risk (YELLOW), which re-evaluation still verifies.
    """
    execution = await l3.execution(event["customer_id"], event["source_plan_id"])
    if execution.get("status") != "COMMITTED":
        return None
    approved_risk = execution.get("approved_risk") or "YELLOW"
    grant = {
        "instruction_id": event["source_id"],
        "customer_id": event["customer_id"],
        "source_plan_id": event["source_plan_id"],
        "scope_json": {"transfers": []},
        "scope_hash": event["grant_scope_hash"],
        "approved_risk": approved_risk,
        "status": "ACTIVE",
        "created_at": wall_now(),
        "revoked_at": None,
    }
    async with db.begin() as conn:
        await conn.execute(
            pg_insert(t.scheduled_grants)
            .values(
                instruction_id=grant["instruction_id"],
                customer_id=grant["customer_id"],
                source_plan_id=grant["source_plan_id"],
                scope_json=grant["scope_json"],
                scope_hash=grant["scope_hash"],
                approved_risk=grant["approved_risk"],
                status=grant["status"],
                created_at=grant["created_at"],
                revoked_at=grant["revoked_at"],
            )
            .on_conflict_do_nothing()
        )
    return grant


async def _block(
    request: Request,
    db,
    event: dict[str, Any],
    status: str,
    code: str,
    *,
    ctx: EvalContext | None,
) -> dict[str, Any]:
    """Record a refusal on L3 and persist the terminal event decision + audit.

    When L3 is unreachable the IN_PROGRESS claim is released so the next
    callback can try immediately — no stale-claim wait.
    """
    app = request.app.state
    versions, clock_version = _pinned(ctx)
    try:
        await app.l3.event_block(
            event["event_id"],
            {
                "status": status,
                "code": code,
                "expected_versions": versions,
                "clock_version": clock_version,
                "expected_payload_hash": event["payload_hash"],
            },
        )
    except L3Error:
        await _release_claim(db, event["event_id"])
        return _retrying(event["event_id"])
    await _finalize_claim(
        app.db,
        event["event_id"],
        event["customer_id"],
        status,
        code,
        app.wall_now(),
        getattr(request.state, "trace_id", None),
    )
    return {"event_id": event["event_id"], "status": status, "code": code}


def _pinned(ctx: EvalContext | None) -> tuple[dict[str, Any], int]:
    if ctx is not None and ctx.snapshot is not None:
        return dict(ctx.snapshot.versions), ctx.snapshot.clock_version
    return {}, 0


# ── Route ─────────────────────────────────────────────────────────────────────


@router.post("/internal/scheduled/authorize")
async def scheduled_authorize(request: Request, body: AuthorizeRequest) -> dict[str, Any]:
    caller = getattr(request.state, "caller", None)
    if caller is None or caller.kind is not ServiceKind.S:
        raise GatewayError(Code.UNAUTHENTICATED, 401)

    app = request.app.state
    db = app.db
    l3 = app.l3
    event_id = body.event_id
    trace_id = getattr(request.state, "trace_id", None)

    # ── Phase A: claim the event ──────────────────────────────────────────
    claim_result, claim_payload = await _claim_event(db, event_id, app.wall_now)
    if claim_result is _ClaimResult.TERMINAL:
        return claim_payload  # type: ignore[return-value]
    if claim_result is _ClaimResult.RETRYING:
        return claim_payload  # type: ignore[return-value]
    # claim_result is CLAIMED — we are the exclusive handler.

    # ── Phase B: evaluate (all HTTP — no lock, no held connection) ────────
    try:
        # Step 1: fetch the L3 event definition (never trust callback amounts).
        try:
            event = await l3.event(event_id)
        except L3Error:
            await _release_claim(db, event_id)
            return _retrying(event_id)

        customer_id = event["customer_id"]

        # Step 2: load the grant by instruction id; backfill when missing.
        async with db.connect() as conn:
            grant = await _load_grant(conn, event["source_id"])
        if grant is None:
            try:
                grant = await _backfill_grant(db, l3, event, app.wall_now)
            except L3Error:
                await _release_claim(db, event_id)
                return _retrying(event_id)
            if grant is None:
                await _release_claim(db, event_id)
                return await _block(
                    request,
                    db,
                    event,
                    "BLOCKED",
                    Code.SCHEDULED_BYPASS.value,
                    ctx=None,
                )

        # Revoked grants and fingerprint mismatches refuse without re-evaluation.
        if grant["status"] == "REVOKED" or grant["scope_hash"] != event["grant_scope_hash"]:
            return await _block(
                request,
                db,
                event,
                "BLOCKED",
                Code.SCHEDULED_BYPASS.value,
                ctx=None,
            )

        # Step 3: re-evaluate the transfer on a fresh snapshot.
        try:
            ctx = await _re_evaluate(request, event)
        except L3Error:
            await _release_claim(db, event_id)
            return _retrying(event_id)
        if ctx.decision is None:
            await _release_claim(db, event_id)
            raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503, "pipeline produced no decision")

        decision_value = ctx.decision.decision
        risk_value = ctx.decision.risk.value

        # Step 4: decision → block/execute mapping (PRD §7.11).
        approved_risk = grant["approved_risk"] or "GREEN"
        if _RISK_ORDER[risk_value] > _RISK_ORDER.get(approved_risk, 0):
            return await _block(
                request,
                db,
                event,
                "SKIPPED_REAPPROVAL",
                Code.SCHEDULED_REAPPROVAL_REQUIRED.value,
                ctx=ctx,
            )
        if decision_value in (DecisionValue.DENY, DecisionValue.REPLAN):
            if any(f.code is Code.INSUFFICIENT_FUNDS for f in ctx.decision.findings):
                return await _block(
                    request,
                    db,
                    event,
                    "FAILED_INSUFFICIENT",
                    Code.INSUFFICIENT_FUNDS.value,
                    ctx=ctx,
                )
            return await _block(
                request,
                db,
                event,
                "BLOCKED",
                Code.SCHEDULED_BYPASS.value,
                ctx=ctx,
            )

        # Otherwise the grant still holds: authorize execution on L3.
        evidence = ScheduledEvidence(
            type="SCHEDULED_GRANT",
            grant_id=event["source_id"],
            scope_hash=grant["scope_hash"],
            source_plan_id=grant["source_plan_id"],
            approved_risk=ctx.decision.risk,
        )
        versions, clock_version = _pinned(ctx)
        try:
            result = await l3.event_execute(
                event_id,
                {
                    "expected_versions": versions,
                    "clock_version": clock_version,
                    "expected_payload_hash": event["payload_hash"],
                    "authorization_evidence": evidence.model_dump(mode="json"),
                },
            )
        except L3Error:
            await _release_claim(db, event_id)
            return _retrying(event_id)

        # ── Phase C: finalize ─────────────────────────────────────────────
        await _finalize_claim(
            db,
            event_id,
            customer_id,
            "SUCCESS",
            None,
            app.wall_now(),
            trace_id,
        )
        return {
            "event_id": event_id,
            "status": "SUCCESS",
            "instruction_run_id": result.get("instruction_run_id"),
        }

    except Exception:
        # On any unhandled error, release the claim so the event isn't
        # orphaned.  The caller (scheduler) can retry.
        await _release_claim(db, event_id)
        raise
