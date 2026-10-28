"""G4 (W3): plan evaluation handler — POST /plans/evaluate.

Replaces the W0 placeholder stub.  The A‑path (agent + assistant JWT) runs the
full 9‑stage pipeline in a read transaction, then persists atomically in a
single write transaction under unified lock order (session → plan → evaluation →
approval).  The F‑path (app‑form) returns 503 until a future component fills it.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from safebank_bankcore.ids import digest
from safebank_bankcore.references import request_digest
from safebank_contracts.codes import Code
from safebank_contracts.plan import PlanDraft
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from .. import tables as t
from ..approval.planview import build_view
from ..approval.service import create_for_evaluation
from ..audit.chain import append as audit_append
from ..errors import GatewayError
from ..evaluation.repo import append_evaluation, latest_revision
from ..idempotency import request_idempotent
from ..pipeline import evaluate as run_pipeline
from ..pipeline.context import EvalContext, EvalMode
from ..schemas.decision import Decision, DecisionValue
from ..schemas.enums import PlanStatus, ServiceKind, UtteranceStatus
from ..schemas.scope import AuthorizationInputs, UtteranceRef
from ..utterance.loader import as_ref
from ..utterance.service import new_id

router = APIRouter()
logger = logging.getLogger(__name__)

ROUTE = "/plans/evaluate"


# ── Decision → plan status mapping ──────────────────────────────────────────

_DECISION_STATUS: dict[DecisionValue, PlanStatus] = {
    DecisionValue.ALLOW: PlanStatus.EVALUATED,
    DecisionValue.REQUIRE_APPROVAL: PlanStatus.PENDING_APPROVAL,
    DecisionValue.DENY: PlanStatus.DENIED,
    DecisionValue.REPLAN: PlanStatus.REPLAN,
}


# ── Authorization loader ────────────────────────────────────────────────────


async def _load_authorization(
    conn: AsyncConnection,
    session_id: str,
    customer_id: str,
    now: datetime,
) -> AuthorizationInputs:
    """Load ACTIVE, unexpired utterances for *session_id* into AuthorizationInputs."""
    rows = (
        (
            await conn.execute(
                select(t.utterances)
                .where(
                    t.utterances.c.sid == session_id,
                    t.utterances.c.status == UtteranceStatus.ACTIVE.value,
                    t.utterances.c.expires_at_wall > now,
                )
                .order_by(t.utterances.c.created_at_wall, t.utterances.c.id)
            )
        )
        .mappings()
        .all()
    )
    utt_ids: tuple[str, ...] = ()
    utt_map: dict[str, UtteranceRef] = {}
    statuses: dict[str, UtteranceStatus] = {}
    scope_ids: tuple[str, ...] = ()
    epoch = 0
    for row in rows:
        ref = as_ref(row)
        utt_ids += (ref.id,)
        utt_map[ref.id] = ref
        statuses[ref.id] = UtteranceStatus.ACTIVE
        if ref.parse_status.value == "SCOPED":
            scope_ids += (ref.id,)
        epoch = max(epoch, row["authorization_epoch"])
    return AuthorizationInputs(
        sid=session_id,
        customer_id=customer_id,
        utterance_ids=utt_ids,
        utterances=utt_map,
        scope_ids=scope_ids,
        authorization_epoch=epoch,
        statuses=statuses,
    )


# ── Cooldown persistence (B1) ────────────────────────────────────────────────


async def _persist_cooldowns(
    conn: AsyncConnection,
    customer_id: str,
    pending: tuple[tuple[str, str], ...],
) -> None:
    """Write RISKY_SEQUENCE cooldowns atomically (B1: same write transaction)."""
    for fingerprint, until_at_iso in pending:
        await conn.execute(
            pg_insert(t.cooldowns)
            .values(
                customer_id=customer_id,
                payee_fingerprint=fingerprint,
                until_at=datetime.fromisoformat(until_at_iso),
                reason=Code.RISKY_SEQUENCE.value,
            )
            .on_conflict_do_update(
                index_elements=["customer_id", "payee_fingerprint"],
                set_={
                    "until_at": datetime.fromisoformat(until_at_iso),
                    "reason": Code.RISKY_SEQUENCE.value,
                },
            )
        )


# ── Persistence helper ──────────────────────────────────────────────────────


async def _persist_plan(
    conn: AsyncConnection,
    *,
    plan_id: str,
    customer_id: str,
    sid: str,
    request_id: str,
    request_hash: str,
    steps_hash: str,
    resolved_steps: list[dict],
    utterance_ids: list[str],
    scope_ids: list[str],
    status: str,
    now: datetime,
    plan_draft: dict | None = None,
) -> None:
    """Insert the plan row; raises 409 if *request_id* is already used."""
    existing = await conn.scalar(
        select(t.plans.c.id).where(
            t.plans.c.customer_id == customer_id,
            t.plans.c.request_id == request_id,
        )
    )
    if existing is not None:
        raise GatewayError(Code.STATE_CHANGED, 409, "request_id already used for this customer")
    plan_hash = digest(resolved_steps)
    await conn.execute(
        t.plans.insert().values(
            id=plan_id,
            customer_id=customer_id,
            sid=sid,
            origin="agent",
            request_id=request_id,
            request_hash=request_hash,
            plan_hash=plan_hash,
            steps_hash=steps_hash,
            resolved_steps=resolved_steps,
            utterance_ids=utterance_ids,
            scope_ids=scope_ids,
            status=status,
            created_at_wall=now,
            plan_draft=plan_draft,
        )
    )


# ── Handler ──────────────────────────────────────────────────────────────────


@router.post("/plans/evaluate")
async def evaluate_plan(request: Request) -> JSONResponse:
    """G4: run the 9‑stage pipeline, persist results atomically, return Decision."""

    # ── Caller identity ─────────────────────────────────────────────────
    caller = getattr(request.state, "caller", None)
    if caller is None:
        raise GatewayError(Code.UNAUTHENTICATED, 401)

    # F path skeleton: app‑form evaluation lands in a later component.
    if caller.kind is ServiceKind.F:
        raise GatewayError(
            Code.DEPENDENCY_UNAVAILABLE,
            503,
            "app-form evaluate lands in later component",
        )

    if caller.kind is not ServiceKind.A:
        raise GatewayError(Code.UNAUTHENTICATED, 401)
    if not caller.customer_id or not caller.sid:
        raise GatewayError(Code.UNAUTHENTICATED, 401, "assistant JWT requires sub and sid")

    customer_id: str = caller.customer_id
    sid: str = caller.sid

    # ── Parse body ──────────────────────────────────────────────────────
    try:
        raw_body: dict[str, Any] = await request.json()
    except Exception as exc:
        raise GatewayError(Code.CONTRACT_INVALID, 422) from exc

    # PRD §9.5 G4: an assistant (A) caller posts the PlanDraft itself as the
    # body; ``request_id`` lives inside it.  Keep the idempotent_body structure
    # below unchanged so already-persisted idempotency records stay valid.
    try:
        plan_draft = PlanDraft.model_validate(raw_body)
    except Exception as exc:
        raise GatewayError(Code.CONTRACT_INVALID, 422, "invalid plan_draft") from exc

    request_id: str = plan_draft.request_id
    if not request_id or not isinstance(request_id, str):
        raise GatewayError(Code.CONTRACT_INVALID, 422, "request_id is required")

    # Body digest for idempotency (plan_draft + request_id without JWT).
    idempotent_body: dict[str, Any] = {
        "request_id": request_id,
        "plan_draft": plan_draft.model_dump(mode="json"),
    }

    # ── Cross‑validate JWT ↔ body ──────────────────────────────────────
    if plan_draft.session_id != sid:
        raise GatewayError(Code.OWNERSHIP_MISMATCH, 404, "session does not match JWT")

    utts_set: set[str] = set(caller.utts)
    for uid in plan_draft.utterance_ids:
        if uid not in utts_set:
            raise GatewayError(Code.AUTHORIZATION_CONSUMED, 409, f"utterance {uid} not in JWT")

    # ── Runtime resources ──────────────────────────────────────────────
    db_engine = getattr(request.app.state, "db", None)
    l3_client = getattr(request.app.state, "l3", None)
    wall_now = getattr(request.app.state, "wall_now", None)
    settings = getattr(request.app.state, "settings", None)
    profile: str = getattr(settings, "profile", "B2") if settings else "B2"
    if db_engine is None or l3_client is None or wall_now is None:
        raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503)

    now = wall_now()
    plan_id = new_id("pln")

    # ── Phase 1: Pipeline execution (read transaction) ──────────────────
    ctx = EvalContext(
        plan_draft=plan_draft,
        customer_id=customer_id,
        session_id=sid,
        plan_id=plan_id,
        origin="agent",
        identity=caller,
        profile=profile,
        mode=EvalMode.EVALUATE,
        l3=l3_client,
        wall_now=wall_now,
    )

    async with db_engine.connect() as read_conn:
        ctx.conn = read_conn
        try:
            ctx.authorization = await _load_authorization(read_conn, sid, customer_id, now)
        except GatewayError:
            raise
        except Exception as exc:
            raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503) from exc

        try:
            await run_pipeline(ctx)
        except GatewayError:
            raise

    if ctx.decision is None:
        raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503, "pipeline produced no decision")

    decision: Decision = ctx.decision
    decision_value: DecisionValue = decision.decision

    # ── Pre‑compute values needed for persistence ───────────────────────
    steps_hash: str = request_digest(plan_draft.steps)
    request_hash: str = digest({"request_id": request_id, "steps_hash": steps_hash})
    resolved: list[dict] = [
        {"tool": s.tool, "args": s.args, "step_id": s.step_id} for s in ctx.steps
    ]
    scope_ids_list: list[str] = list(ctx.authorization.scope_ids) if ctx.authorization else []

    # ── Phase 2: Persistence (write transaction + idempotency) ──────────
    async def _persist(conn: AsyncConnection) -> tuple[int, dict[str, Any]]:
        """Run persistence inside the idempotency wrapper's transaction."""

        # Lock session first (unified lock order).
        session_row = (
            (await conn.execute(select(t.sessions).where(t.sessions.c.id == sid).with_for_update()))
            .mappings()
            .one_or_none()
        )
        if session_row is None or session_row["customer_id"] != customer_id:
            raise GatewayError(Code.OWNERSHIP_MISMATCH, 404)
        if session_row["revoked_at_wall"] is not None:
            raise GatewayError(Code.AUTHORIZATION_CANCELLED, 409)

        revision = (await latest_revision(conn, plan_id)) + 1
        evaluation_id = new_id("evl")
        plan_status = _DECISION_STATUS[decision_value]

        await _persist_plan(
            conn,
            plan_id=plan_id,
            customer_id=customer_id,
            sid=sid,
            request_id=request_id,
            request_hash=request_hash,
            steps_hash=steps_hash,
            resolved_steps=resolved,
            utterance_ids=list(plan_draft.utterance_ids),
            scope_ids=scope_ids_list,
            status=plan_status.value,
            now=now,
            plan_draft=plan_draft.model_dump(mode="json"),
        )

        risk_str: str = decision.risk.value
        findings_json = [f.model_dump(mode="json") for f in decision.findings]
        preflight_json = decision.preflight.model_dump(mode="json")
        versions_json = dict(ctx.snapshot.versions) if ctx.snapshot else {}
        clock_version_num = ctx.snapshot.clock_version if ctx.snapshot else 0
        policy_version_str: str = decision.policy_version

        # B1: persist cooldowns before the evaluation row.
        if ctx.workflow is not None and ctx.workflow.pending_cooldowns:
            await _persist_cooldowns(conn, customer_id, ctx.workflow.pending_cooldowns)

        await append_evaluation(
            conn,
            plan_id=plan_id,
            evaluation_id=evaluation_id,
            revision=revision,
            decision={"decision": decision_value.value},
            risk=risk_str,
            findings=findings_json,
            preflight=preflight_json,
            versions=versions_json,
            policy_version=policy_version_str,
            clock_version=clock_version_num,
            created_at_wall=now,
        )

        # ── Approval (REQUIRE_APPROVAL only) ──────────────────────
        approval_id: str | None = None
        view_hash: str | None = None
        if decision_value is DecisionValue.REQUIRE_APPROVAL:
            try:
                ctx.decision = ctx.decision.model_copy(update={"evaluation_id": evaluation_id})
                view, view_hash = build_view(ctx)
            except ValueError as exc:
                raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503) from exc

            plan_row = (
                (await conn.execute(select(t.plans).where(t.plans.c.id == plan_id)))
                .mappings()
                .one()
            )
            eval_row = (
                (
                    await conn.execute(
                        select(t.evaluations).where(t.evaluations.c.id == evaluation_id)
                    )
                )
                .mappings()
                .one()
            )
            approval_id = await create_for_evaluation(
                conn,
                plan=plan_row,
                evaluation=eval_row,
                view=view,
                key=getattr(request.app.state, "approval_hmac_key", None),
                profile=getattr(settings, "profile", "B2") if settings else "B2",
                wall_now=wall_now,
            )

        # ── Audit ─────────────────────────────────────────────────
        audit_payload: dict[str, Any] = {
            "plan_id": plan_id,
            "evaluation_id": evaluation_id,
            "revision": revision,
            "decision": decision_value.value,
            "risk": risk_str,
            "approval_id": approval_id,
            "findings": findings_json,
        }
        try:
            await audit_append(
                conn,
                customer_id=customer_id,
                event_type="EVALUATION_COMPLETED",
                payload=audit_payload,
                event_id=new_id("evt"),
                trace_id=getattr(request.state, "trace_id", None),
                plan_id=plan_id,
                now_wall=now,
            )
        except Exception as exc:
            raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503, "audit write failed") from exc

        response: dict[str, Any] = {
            "plan_id": plan_id,
            "evaluation_id": evaluation_id,
            "revision": revision,
            "decision": decision_value.value,
            "risk": risk_str,
            "approval_id": approval_id,
            "findings": findings_json,
            "replan_hints": [h.model_dump(mode="json") for h in decision.replan_hints],
            "preflight": preflight_json,
            "versions": versions_json,
            "clock_version": clock_version_num,
            "policy_version": policy_version_str,
            "shadow_b1": (
                decision.shadow_b1.model_dump(mode="json") if decision.shadow_b1 else None
            ),
        }
        return 200, response

    async with db_engine.begin() as write_conn:
        status, response_body, _replayed = await request_idempotent(
            write_conn,
            customer_id=customer_id,
            route=ROUTE,
            request_id=request_id,
            body=idempotent_body,
            produce=lambda: _persist(write_conn),
        )

    return JSONResponse(response_body, status_code=status)
