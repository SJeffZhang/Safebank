"""S1 (W5): scheduler callback, the only internal business route.

``POST /internal/scheduled/authorize`` (PRD §9.5a). Called by the L3 scheduler on
bank-net :8201 with ``SCHEDULER_SERVICE_TOKEN`` (caller kind S, no JWT). S1 never
trusts callback amounts: it re-fetches the L3 event definition and re-evaluates
the transfer on a fresh snapshot before authorizing execution.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel
from safebank_contracts.codes import Code
from safebank_contracts.plan import PlanDraft, ScheduledEvidence
from sqlalchemy import select
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


class AuthorizeRequest(BaseModel):
    event_id: str
    request_id: str | None = None
    attempt: int = 1


def _retrying(event_id: str) -> dict[str, Any]:
    return {"event_id": event_id, "status": "RETRYING", "code": Code.DEPENDENCY_UNAVAILABLE.value}


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
    event: dict[str, Any],
    status: str,
    code: str,
    *,
    ctx: EvalContext | None,
) -> dict[str, Any]:
    """Record a refusal on L3 and persist the terminal event decision + audit."""
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
        return _retrying(event["event_id"])
    await _persist_decision(request, event["customer_id"], event["event_id"], status, code)
    return {"event_id": event["event_id"], "status": status, "code": code}


def _pinned(ctx: EvalContext | None) -> tuple[dict[str, Any], int]:
    if ctx is not None and ctx.snapshot is not None:
        return dict(ctx.snapshot.versions), ctx.snapshot.clock_version
    return {}, 0


async def _persist_decision(
    request: Request, customer_id: str, event_id: str, status: str, code: str | None
) -> None:
    """Persist event_decisions + audit in one write transaction (no HTTP)."""
    app = request.app.state
    now = app.wall_now()
    async with app.db.begin() as conn:
        await conn.execute(
            pg_insert(t.event_decisions)
            .values(event_id=event_id, status=status, code=code, decided_at_wall=now)
            .on_conflict_do_nothing()
        )
        try:
            await audit_append(
                conn,
                customer_id=customer_id,
                event_type="SCHEDULE_RESULT",
                payload={"event_id": event_id, "status": status, "code": code},
                event_id=new_id("evt"),
                trace_id=getattr(request.state, "trace_id", None),
                plan_id=None,
                now_wall=now,
            )
        except Exception as exc:
            raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503, "audit write failed") from exc


@router.post("/internal/scheduled/authorize")
async def scheduled_authorize(request: Request, body: AuthorizeRequest) -> dict[str, Any]:
    caller = getattr(request.state, "caller", None)
    if caller is None or caller.kind is not ServiceKind.S:
        raise GatewayError(Code.UNAUTHENTICATED, 401)

    app = request.app.state
    db = app.db
    l3 = app.l3

    # Step 1: a terminal decision for this event is idempotently replayed without
    # re-evaluation and without touching L3.
    async with db.connect() as conn:
        row = (
            await conn.execute(
                select(t.event_decisions).where(t.event_decisions.c.event_id == body.event_id)
            )
        ).first()
    if row is not None:
        return {"event_id": body.event_id, "status": row.status, "code": row.code}

    # Step 2: fetch the L3 event definition (never trust callback amounts). Any
    # L3 transport failure is retryable: report RETRYING and persist nothing.
    try:
        event = await l3.event(body.event_id)
    except L3Error:
        return _retrying(body.event_id)

    customer_id = event["customer_id"]

    # Step 3: load the grant by instruction id; backfill from the L3 committed
    # execution when missing.
    async with db.connect() as conn:
        grant = await _load_grant(conn, event["source_id"])
    if grant is None:
        try:
            grant = await _backfill_grant(db, l3, event, app.wall_now)
        except L3Error:
            return _retrying(body.event_id)
        if grant is None:
            # The source plan was never committed: no grant can exist, refuse.
            return await _block(
                request,
                event,
                "BLOCKED",
                Code.SCHEDULED_BYPASS.value,
                ctx=None,
            )

    # Revoked grants and fingerprint mismatches refuse without re-evaluation.
    if grant["status"] == "REVOKED" or grant["scope_hash"] != event["grant_scope_hash"]:
        return await _block(
            request,
            event,
            "BLOCKED",
            Code.SCHEDULED_BYPASS.value,
            ctx=None,
        )

    # Step 4: re-evaluate the transfer on a fresh snapshot.
    try:
        ctx = await _re_evaluate(request, event)
    except L3Error:
        return _retrying(body.event_id)
    if ctx.decision is None:
        raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503, "pipeline produced no decision")

    decision_value = ctx.decision.decision
    risk_value = ctx.decision.risk.value

    # Step 5: decision → block/execute mapping (PRD §7.11).
    approved_risk = grant["approved_risk"] or "GREEN"
    if _RISK_ORDER[risk_value] > _RISK_ORDER.get(approved_risk, 0):
        return await _block(
            request,
            event,
            "SKIPPED_REAPPROVAL",
            Code.SCHEDULED_REAPPROVAL_REQUIRED.value,
            ctx=ctx,
        )
    if decision_value in (DecisionValue.DENY, DecisionValue.REPLAN):
        if any(f.code is Code.INSUFFICIENT_FUNDS for f in ctx.decision.findings):
            return await _block(
                request, event, "FAILED_INSUFFICIENT", Code.INSUFFICIENT_FUNDS.value, ctx=ctx
            )
        return await _block(request, event, "BLOCKED", Code.SCHEDULED_BYPASS.value, ctx=ctx)

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
            body.event_id,
            {
                "expected_versions": versions,
                "clock_version": clock_version,
                "expected_payload_hash": event["payload_hash"],
                "authorization_evidence": evidence.model_dump(mode="json"),
            },
        )
    except L3Error:
        return _retrying(body.event_id)

    await _persist_decision(request, customer_id, body.event_id, "SUCCESS", None)
    return {
        "event_id": body.event_id,
        "status": "SUCCESS",
        "instruction_run_id": result.get("instruction_run_id"),
    }
