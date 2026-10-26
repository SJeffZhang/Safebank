"""G5 / G8 (W4): plan status and execution.

G8 ALLOW path (T2e): implements the EVALUATED/ALLOW → EXECUTED flow.
CONFIRMED, STATE_CHANGED, UNKNOWN, G5, and request_id replay are later tasks.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from safebank_bankcore.ids import digest
from safebank_contracts.codes import Code
from safebank_contracts.plan import (
    Execute,
    ExecutionResult,
    InteractiveEvidence,
    PlanDraft,
    Prepare,
)
from safebank_contracts.protocol import PrepareResult
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection

from .. import state
from .. import tables as t
from ..approval.planview import build_view
from ..approval.service import create_for_evaluation
from ..audit.chain import append as audit_append
from ..errors import GatewayError
from ..evaluation.repo import append_evaluation, latest_revision
from ..execution.replay import replay_with_context
from ..l3.client import L3BusinessError, L3NotDelivered, L3OutcomeUnknown
from ..locks import customer_lock
from ..middleware import digest_hex
from ..pipeline.context import EvalContext
from ..schemas.enums import ApprovalStatus, ExecutionJobStatus, PlanStatus, ServiceKind
from ..schemas.execution import ExecutionAccepted, PlanStatusView
from ..utterance.service import new_id

router = APIRouter()

ROUTE_EXECUTE = "POST /plans/{plan_id}/execute"

router = APIRouter()


class ExecuteRequest(BaseModel):
    request_id: str


# ── helpers ──────────────────────────────────────────────────────────────────


def _exec_now() -> datetime:
    from datetime import UTC

    return datetime.now(UTC)


# ── request_dedup helpers ──────────────────────────────────────────────────────


async def _write_request_dedup_row(
    conn: AsyncConnection,
    *,
    customer_id: str,
    request_id: str,
    request_hash: str,
    status_code: int,
    response: dict[str, Any],
) -> None:
    """Persist a request_dedup row (C4).  Caller owns the customer_lock conn."""
    await conn.rollback()
    async with conn.begin():
        await conn.execute(
            t.request_dedup.insert().values(
                customer_id=customer_id,
                route=ROUTE_EXECUTE,
                request_id=request_id,
                request_hash=request_hash,
                status_code=status_code,
                response=response,
                created_at_wall=text("now()"),
            )
        )


async def _check_request_dedup(
    conn: AsyncConnection,
    *,
    customer_id: str,
    request_id: str,
    request_hash: str,
) -> tuple[dict[str, Any] | None, bool]:
    """Check request_dedup. Returns (replay_response, is_non_terminal_dedup).

    - replay_response is not None → return this JSON body as-is (terminal)
    - is_non_terminal_dedup → old request_id had a non-terminal response;
      PAUSED-resume path must be blocked
    """
    existing = (
        (
            await conn.execute(
                select(
                    t.request_dedup.c.request_hash,
                    t.request_dedup.c.status_code,
                    t.request_dedup.c.response,
                ).where(
                    t.request_dedup.c.customer_id == customer_id,
                    t.request_dedup.c.route == ROUTE_EXECUTE,
                    t.request_dedup.c.request_id == request_id,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if existing is None:
        return None, False
    if existing["request_hash"] != request_hash:
        raise GatewayError(Code.IDEMPOTENCY_CONFLICT, 409, "request_id reused with different body")
    if existing["status_code"] == 200:
        return dict(existing["response"]), False
    return None, True  # non-terminal → flag, don't replay


# ── G5 GET /plans/{plan_id} ───────────────────────────────────────────────────


@router.get("/plans/{plan_id}")
async def get_plan_status(plan_id: str, request: Request):
    caller = getattr(request.state, "caller", None)
    if caller is None or caller.customer_id is None:
        raise GatewayError(Code.UNAUTHENTICATED, 401)

    app_state = request.app.state
    l3 = app_state.l3
    wall_now = getattr(app_state, "wall_now", None)
    if wall_now is None:
        raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503)
    trace_id = getattr(request.state, "trace_id", "")

    async with customer_lock([caller.customer_id]) as conn:
        plan = await _load_plan(conn, plan_id, caller.customer_id)

        # A caller restricted to own session
        if caller.kind is ServiceKind.A and plan["sid"] != caller.sid:
            raise GatewayError(Code.OWNERSHIP_MISMATCH, 404, "plan not found for this session")

        decision: dict[str, Any] | None = None
        eval_row = (
            (
                await conn.execute(
                    select(t.evaluations)
                    .where(t.evaluations.c.plan_id == plan_id)
                    .order_by(t.evaluations.c.revision.desc())
                    .limit(1)
                )
            )
            .mappings()
            .one_or_none()
        )
        if eval_row is not None:
            decision = {
                "decision": eval_row["decision"].get("decision"),
                "risk": eval_row["risk"],
                "evaluation_id": eval_row["id"],
                "revision": eval_row["revision"],
                "approval_id": None,
            }
            # Find the approval for this evaluation
            approval_row = (
                (
                    await conn.execute(
                        select(t.approvals.c.id).where(
                            t.approvals.c.evaluation_id == eval_row["id"]
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if approval_row is not None:
                decision["approval_id"] = approval_row["id"]

        execution: dict[str, Any] | None = None
        job = (
            (
                await conn.execute(
                    select(t.execution_jobs).where(t.execution_jobs.c.plan_id == plan_id)
                )
            )
            .mappings()
            .one_or_none()
        )
        if job is not None:
            if job["status"] in ("EXECUTED", "FAILED"):
                if job["result"] is not None:
                    execution = dict(job["result"])
            elif job["status"] == "UNKNOWN":
                reconciled = await _reconcile_job(
                    conn=conn,
                    plan_id=plan_id,
                    caller_customer_id=caller.customer_id,
                    plan=plan,
                    job=job,
                    l3=l3,
                    trace_id=trace_id,
                )
                if reconciled is not None:
                    execution = reconciled
                    # Re-read plan status — reconciliation may have archived the job.
                    refreshed_status = await conn.scalar(
                        select(t.plans.c.status).where(t.plans.c.id == plan_id)
                    )
                    plan = dict(plan)  # don't mutate the original mapping
                    plan["status"] = refreshed_status
                else:
                    execution = {"status": "UNKNOWN"}
            # live job states (EXECUTING, FENCING, PAUSED) → execution = None

        return PlanStatusView(
            plan_id=plan_id,
            status=plan["status"],
            decision=decision,
            execution=execution,
        ).model_dump(mode="json")


async def _load_plan(conn: AsyncConnection, plan_id: str, customer_id: str) -> dict[str, Any]:
    plan = (
        (await conn.execute(select(t.plans).where(t.plans.c.id == plan_id).with_for_update()))
        .mappings()
        .one_or_none()
    )
    if plan is None or plan["customer_id"] != customer_id:
        raise GatewayError(Code.OWNERSHIP_MISMATCH, 404, "plan not found")
    return plan


async def _load_session(conn: AsyncConnection, sid: str) -> dict[str, Any]:
    """Load session row with FOR UPDATE; raise 404/409 as appropriate."""
    row = (
        (
            await conn.execute(
                select(t.sessions.c.revoked_at_wall, t.sessions.c.authorization_epoch)
                .where(t.sessions.c.id == sid)
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise GatewayError(Code.OWNERSHIP_MISMATCH, 404, "session not found")
    if row["revoked_at_wall"] is not None:
        raise GatewayError(Code.AUTHORIZATION_CANCELLED, 409, "session has been revoked")
    return row


async def _compute_deadline_cap(conn: AsyncConnection, utterance_ids: list[str]) -> datetime:
    """Return the earliest expires_at_wall across all utterance_ids.

    The seed data may use historical timestamps; L3 owns the actual
    expiration enforcement, so this function only computes the minimum
    without rejecting based on the current wall clock.
    """
    rows = (
        (
            await conn.execute(
                select(t.utterances.c.expires_at_wall).where(t.utterances.c.id.in_(utterance_ids))
            )
        )
        .mappings()
        .all()
    )
    if len(rows) != len(utterance_ids):
        raise GatewayError(Code.STATE_CHANGED, 409, "one or more utterance_ids not found")
    expires: list[datetime] = []
    for r in rows:
        if r["expires_at_wall"] is None:
            raise GatewayError(Code.STATE_CHANGED, 409, "utterance has no expires_at_wall")
        expires.append(r["expires_at_wall"])
    return min(expires)


def _build_evidence(
    *,
    plan: dict[str, Any],
    evaluation_id: str,
    risk: str,
    session: dict[str, Any],
) -> InteractiveEvidence:
    """Build InteractiveEvidence for the ALLOW GREEN path.

    final_view_hash is "" because ALLOW decisions have no approval panel.
    """
    return InteractiveEvidence(
        type="INTERACTIVE",
        origin=plan["origin"],
        scope_ids=tuple(plan["scope_ids"]),
        utterance_ids=tuple(plan["utterance_ids"]),
        evaluation_id=evaluation_id,
        approved_evaluation_id=None,
        plan_hash=plan["plan_hash"],
        approval_id=None,
        confirmed_view_hash=None,
        final_view_hash="",
        risk=risk,
        confirmed_at_wall=None,
        authorization_epoch=session["authorization_epoch"],
    )


def _build_confirmed_evidence(
    *,
    plan: dict[str, Any],
    evaluation_id: str,
    risk: str,
    session: dict[str, Any],
    approval: dict[str, Any],
    replayed_view_hash: str,
) -> InteractiveEvidence:
    """Build InteractiveEvidence for the CONFIRMED path.

    confirmed_view_hash and final_view_hash are equal because we already
    validated ``replayed_view_hash == approval["view_hash"]`` before calling.
    """
    return InteractiveEvidence(
        type="INTERACTIVE",
        origin=plan["origin"],
        scope_ids=tuple(plan["scope_ids"]),
        utterance_ids=tuple(plan["utterance_ids"]),
        evaluation_id=evaluation_id,
        approved_evaluation_id=approval["evaluation_id"],
        plan_hash=plan["plan_hash"],
        approval_id=approval["id"],
        confirmed_view_hash=approval["view_hash"],
        final_view_hash=replayed_view_hash,
        risk=risk,
        confirmed_at_wall=approval["confirmed_at_wall"],
        authorization_epoch=session["authorization_epoch"],
    )


# ── ADR-008 STATE_CHANGED fence ───────────────────────────────────────────────


_DECISION_TO_STATUS: dict[str, PlanStatus] = {
    "ALLOW": PlanStatus.EVALUATED,
    "REQUIRE_APPROVAL": PlanStatus.PENDING_APPROVAL,
    "DENY": PlanStatus.DENIED,
    "REPLAN": PlanStatus.REPLAN,
}


async def _handle_state_changed(
    *,
    conn: AsyncConnection,
    plan_id: str,
    caller_customer_id: str,
    plan: dict[str, Any],
    eval_row: dict[str, Any],
    exec_identity: state.ExecutionIdentity,
    plan_draft: PlanDraft,
    evidence: InteractiveEvidence,
    wall_now: Any,
    l3: Any,
    deadline_cap_wall: Any,
    sid: str,
    caller: Any,
    request: Request,
) -> JSONResponse:
    """ADR-008 fence: STATE_CHANGED after successful prepare.

    1. begin_fencing (write tx)
    2. HTTP: fence prepare with deadline_wall = FENCE_AT
    3. acknowledge_fence (write tx)
    4. HTTP: replay_with_context for fresh decision
    5. Write tx: append_evaluation + build_view + publish_paused_decision
    6. Return 409 STATE_CHANGED
    """
    # ── 1. begin_fencing ──────────────────────────────────────────────────
    await conn.rollback()
    async with conn.begin():
        await state.begin_fencing(conn, expected=exec_identity)

    # ── 2. fence prepare (HTTP) ───────────────────────────────────────────
    fence_prepare_body = Prepare(
        customer_id=caller_customer_id,
        plan_id=plan_id,
        attempt=1,
        plan_hash=plan["plan_hash"],
        steps=plan_draft.steps,
        deadline_wall=state.FENCE_AT,
        deadline_cap_wall=deadline_cap_wall,
        authorization_evidence=evidence,
    )
    try:
        fence_prepare_resp = await l3.prepare(fence_prepare_body.model_dump(mode="json"))
    except (L3NotDelivered, L3OutcomeUnknown) as exc:
        await conn.rollback()
        async with conn.begin():
            await state.mark_execution_unknown(
                conn,
                expected=exec_identity,
                from_status=ExecutionJobStatus.FENCING,
                reason={"reason": f"fence prepare: {type(exc).__name__}"},
            )
        return JSONResponse(
            status_code=202,
            content=ExecutionAccepted(plan_id=plan_id).model_dump(mode="json"),
        )
    except L3BusinessError as exc:
        await conn.rollback()
        async with conn.begin():
            await state.mark_execution_unknown(
                conn,
                expected=exec_identity,
                from_status=ExecutionJobStatus.FENCING,
                reason={"reason": f"fence prepare: L3BusinessError {exc.code}"},
            )
        return JSONResponse(
            status_code=202,
            content=ExecutionAccepted(plan_id=plan_id).model_dump(mode="json"),
        )

    fence_result = PrepareResult.model_validate(fence_prepare_resp)
    if fence_result.status == "COMMITTED":
        # L3 already committed — query result and archive normally.
        try:
            exec_status = await l3.execution(caller_customer_id, plan_id)
        except (L3NotDelivered, L3OutcomeUnknown, L3BusinessError) as exc:
            await conn.rollback()
            async with conn.begin():
                await state.mark_execution_unknown(
                    conn,
                    expected=exec_identity,
                    from_status=ExecutionJobStatus.FENCING,
                    reason={"reason": f"fence query after COMMITTED: {type(exc).__name__}"},
                )
            return JSONResponse(
                status_code=202,
                content=ExecutionAccepted(plan_id=plan_id).model_dump(mode="json"),
            )
        from safebank_contracts.protocol import ExecutionStatus

        status_obj = ExecutionStatus.model_validate(exec_status)
        if status_obj.status == "COMMITTED" and status_obj.result is not None:
            await conn.rollback()
            async with conn.begin():
                await state.archive_execution_terminal(
                    conn,
                    expected=exec_identity,
                    from_status=ExecutionJobStatus.FENCING,
                    l3_status="COMMITTED",
                    result=status_obj.result,
                )
            return JSONResponse(
                status_code=200,
                content=status_obj.result.model_dump(mode="json"),
            )
        await conn.rollback()
        async with conn.begin():
            await state.mark_execution_unknown(
                conn,
                expected=exec_identity,
                from_status=ExecutionJobStatus.FENCING,
                reason={"reason": "fence COMMITTED but no usable result"},
            )
        return JSONResponse(
            status_code=202,
            content=ExecutionAccepted(plan_id=plan_id).model_dump(mode="json"),
        )

    if fence_result.status != "PREPARED" or fence_result.attempt != 1:
        await conn.rollback()
        async with conn.begin():
            await state.mark_execution_unknown(
                conn,
                expected=exec_identity,
                from_status=ExecutionJobStatus.FENCING,
                reason={
                    "reason": (
                        f"fence prepare: unexpected status={fence_result.status}"
                        f" attempt={fence_result.attempt}"
                    )
                },
            )
        return JSONResponse(
            status_code=202,
            content=ExecutionAccepted(plan_id=plan_id).model_dump(mode="json"),
        )

    # ── 3. acknowledge_fence ──────────────────────────────────────────────
    await conn.rollback()
    async with conn.begin():
        await state.acknowledge_fence(conn, expected=exec_identity)

    # ── 4. replay (HTTP, fresh snapshot) ──────────────────────────────────
    from .plans import _load_authorization

    now_wall = _exec_now()
    authorization = await _load_authorization(conn, sid, caller_customer_id, now_wall)
    ctx = EvalContext(
        request=None,
        identity=caller,
        authorization=authorization,
        plan_draft=plan_draft,
        plan_id=plan_id,
        origin=plan["origin"],
        customer_id=caller_customer_id,
        session_id=sid,
        profile="B2",
        wall_now=wall_now,
        conn=conn,
        l3=l3,
        audit=None,
    )
    new_decision_raw, fresh = await replay_with_context(ctx)

    # ── 5. write transaction ──────────────────────────────────────────────
    new_eval_id = new_id("evl")
    new_risk = new_decision_raw.risk.value
    new_decision_value = new_decision_raw.decision.value

    await conn.rollback()
    async with conn.begin():
        new_revision = (await latest_revision(conn, plan_id)) + 1

        fresh.decision = fresh.decision.model_copy(update={"evaluation_id": new_eval_id})
        try:
            view, view_hash = build_view(fresh)
        except ValueError as exc:
            raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503) from exc

        await append_evaluation(
            conn,
            plan_id=plan_id,
            evaluation_id=new_eval_id,
            revision=new_revision,
            decision=new_decision_raw.model_dump(mode="json"),
            risk=new_risk,
            findings=[f.model_dump(mode="json") for f in new_decision_raw.findings],
            preflight=new_decision_raw.preflight.model_dump(mode="json"),
            versions=new_decision_raw.versions,
            policy_version=new_decision_raw.policy_version,
            clock_version=new_decision_raw.clock_version,
            view_hash=view_hash,
            view_json=view.model_dump(mode="json"),
            equivalent_to=eval_row["id"],
            created_at_wall=now_wall,
        )

        plan_row = (
            (await conn.execute(select(t.plans).where(t.plans.c.id == plan_id))).mappings().one()
        )
        eval_row_new = (
            (await conn.execute(select(t.evaluations).where(t.evaluations.c.id == new_eval_id)))
            .mappings()
            .one()
        )

        to_status = _DECISION_TO_STATUS[new_decision_value]
        approval_id: str | None = None
        if to_status == PlanStatus.PENDING_APPROVAL:
            approval_id = await create_for_evaluation(
                conn, plan=plan_row, evaluation=eval_row_new, view=view
            )

        fence_new_identity = state.ExecutionIdentity(
            customer_id=exec_identity.customer_id,
            plan_id=exec_identity.plan_id,
            origin=exec_identity.origin,
            attempt=exec_identity.attempt,
            revision=new_revision,
        )
        await state.publish_paused_decision(
            conn,
            expected=fence_new_identity,
            to=to_status,
            approval_id=approval_id,
            now=now_wall,
        )

    # ── 6. return 409 STATE_CHANGED ───────────────────────────────────────
    decision_dict = new_decision_raw.model_dump(mode="json")
    details: dict[str, Any] = {"decision": decision_dict}
    if approval_id is not None:
        details["approval_id"] = approval_id
        decision_dict["approval_id"] = approval_id
        details["decision"] = decision_dict

    trace_id = getattr(request.state, "trace_id", "")
    return JSONResponse(
        status_code=409,
        content=GatewayError(
            Code.STATE_CHANGED,
            409,
            message="state changed; re-evaluated",
            details=details,
        ).body(trace_id),
    )


# ── ADR-008 resume: PAUSED → attempt+1 execute ───────────────────────────────


async def _handle_resume(
    *,
    conn: AsyncConnection,
    plan_id: str,
    caller_customer_id: str,
    plan: dict[str, Any],
    session: dict[str, Any],
    sid: str,
    eval_row: dict[str, Any],
    is_confirmed: bool,
    approval: dict[str, Any] | None,
    wall_now: Any,
    now: datetime,
    l3: Any,
    caller: Any,
    request: Request,
    job_attempt: int,
) -> JSONResponse:
    """ADR-008: resume execution after STATE_CHANGED fence.

    The plan is EVALUATED/CONFIRMED with a PAUSED execution_jobs row
    (l3_attempt=1, deadline=FENCE_AT).  Replay on a fresh snapshot,
    verify equivalence against the fence evaluation, then prepare and
    execute with an incremented attempt from ``resume_paused_execution``.
    """
    # ── plan_draft ──────────────────────────────────────────────────────
    if plan["plan_draft"] is None:
        raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503, "plan has no persisted plan_draft")
    plan_draft = PlanDraft.model_validate(plan["plan_draft"])

    # ── deadline ────────────────────────────────────────────────────────
    deadline_cap_wall = await _compute_deadline_cap(conn, plan["utterance_ids"])
    if is_confirmed and approval is not None and approval["expires_at_wall"] < deadline_cap_wall:
        deadline_cap_wall = approval["expires_at_wall"]

    # ── replay ──────────────────────────────────────────────────────────
    from .plans import _load_authorization

    authorization = await _load_authorization(conn, sid, caller_customer_id, now)

    ctx = EvalContext(
        request=None,
        identity=caller,
        authorization=authorization,
        plan_draft=plan_draft,
        plan_id=plan_id,
        origin=plan["origin"],
        customer_id=caller_customer_id,
        session_id=sid,
        profile="B2",
        wall_now=wall_now,
        conn=conn,
        l3=l3,
        audit=None,
    )

    new_decision, fresh = await replay_with_context(ctx)

    # ── equivalence check ───────────────────────────────────────────────
    if is_confirmed:
        assert approval is not None
        if new_decision.decision.value != "REQUIRE_APPROVAL":
            raise GatewayError(
                Code.STATE_CHANGED, 409, "replay decision no longer REQUIRE_APPROVAL"
            )
        fresh.decision = fresh.decision.model_copy(update={"evaluation_id": eval_row["id"]})
        try:
            _view, replayed_view_hash = build_view(fresh)
        except ValueError as exc:
            raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503) from exc
        if replayed_view_hash != approval["view_hash"]:
            raise GatewayError(
                Code.STATE_CHANGED, 409, "replay view_hash differs from approved view_hash"
            )
        evidence = _build_confirmed_evidence(
            plan=plan,
            evaluation_id=eval_row["id"],
            risk=eval_row["risk"],
            session=session,
            approval=approval,
            replayed_view_hash=replayed_view_hash,
        )
        resume_approval_id: str | None = approval["id"]
    else:
        if new_decision.decision.value != "ALLOW":
            findings_str = [f.model_dump() for f in new_decision.findings]
            raise GatewayError(
                Code.STATE_CHANGED,
                409,
                f"resume replay: got {new_decision.decision.value} findings={findings_str}",
            )
        fresh.decision = fresh.decision.model_copy(update={"evaluation_id": eval_row["id"]})
        try:
            _view, replayed_view_hash = build_view(fresh)
        except ValueError as exc:
            raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503) from exc
        if replayed_view_hash != eval_row["view_hash"]:
            raise GatewayError(
                Code.STATE_CHANGED, 409, "replay view_hash differs from fence evaluation"
            )
        evidence = _build_evidence(
            plan=plan,
            evaluation_id=eval_row["id"],
            risk=eval_row["risk"],
            session=session,
        )
        # resume_paused_execution checks final_view_hash == evaluation.view_hash;
        # _build_evidence leaves it "" for ALLOW — patch it here.
        evidence = evidence.model_copy(update={"final_view_hash": eval_row["view_hash"]})
        resume_approval_id = None

    # plan_hash equivalence (same expression as G4)
    replayed_hash = digest(
        [{"tool": s.tool, "args": s.args, "step_id": s.step_id} for s in fresh.steps]
    )
    if replayed_hash != plan["plan_hash"]:
        raise GatewayError(Code.STATE_CHANGED, 409, "replay plan_hash differs from stored plan")

    # ── resume_paused_execution ─────────────────────────────────────────
    now_wall = _exec_now()

    deadline_wall = deadline_cap_wall
    if is_confirmed and approval is not None and approval["expires_at_wall"] < deadline_wall:
        deadline_wall = approval["expires_at_wall"]

    # End the read transaction first, as the normal path does before each
    # write block; resume_paused_execution re-locks what it checks.
    if conn.in_transaction():
        await conn.rollback()
    async with conn.begin():
        # Re-verify session hasn't been revoked since initial load.
        session_row = (
            (
                await conn.execute(
                    select(
                        t.sessions.c.revoked_at_wall,
                        t.sessions.c.authorization_epoch,
                    )
                    .where(t.sessions.c.id == sid)
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if session_row is None:
            raise GatewayError(Code.OWNERSHIP_MISMATCH, 404, "session not found")
        if session_row["revoked_at_wall"] is not None:
            raise GatewayError(Code.AUTHORIZATION_CANCELLED, 409, "session has been revoked")
        if session_row["authorization_epoch"] != session["authorization_epoch"]:
            raise GatewayError(Code.AUTHORIZATION_CANCELLED, 409, "authorization epoch changed")

        new_attempt = await state.resume_paused_execution(
            conn,
            expected=state.ExecutionIdentity(
                customer_id=caller_customer_id,
                plan_id=plan_id,
                origin=plan["origin"],
                attempt=job_attempt,
                revision=eval_row["revision"],
            ),
            authorization_evidence=evidence,
            deadline_wall=deadline_wall,
            deadline_cap_wall=deadline_cap_wall,
            approval_id=resume_approval_id,
            now=now_wall,
        )
        await audit_append(
            conn,
            customer_id=caller_customer_id,
            event_type="EXECUTION_RESUMED",
            payload={
                "plan_id": plan_id,
                "origin": plan["origin"],
                "attempt": new_attempt,
                "evaluation_id": eval_row["id"],
            },
            event_id=new_id("evt"),
            trace_id=getattr(request.state, "trace_id", None),
            plan_id=plan_id,
            now_wall=now_wall,
        )

    exec_identity = state.ExecutionIdentity(
        customer_id=caller_customer_id,
        plan_id=plan_id,
        origin=plan["origin"],
        attempt=new_attempt,
        revision=eval_row["revision"],
    )

    # ── UNKNOWN helper ──────────────────────────────────────────────────
    async def _unknown(reason: str):
        async with conn.begin():
            await state.mark_execution_unknown(
                conn,
                expected=exec_identity,
                from_status=ExecutionJobStatus.EXECUTING,
                reason={"reason": reason},
            )
        return JSONResponse(
            status_code=202,
            content=ExecutionAccepted(plan_id=plan_id).model_dump(mode="json"),
        )

    # ── prepare ─────────────────────────────────────────────────────────
    prepare_body = Prepare(
        customer_id=caller_customer_id,
        plan_id=plan_id,
        attempt=new_attempt,
        plan_hash=plan["plan_hash"],
        steps=plan_draft.steps,
        deadline_wall=deadline_wall,
        deadline_cap_wall=deadline_cap_wall,
        authorization_evidence=evidence,
    )
    try:
        await l3.prepare(prepare_body.model_dump(mode="json"))
    except (L3NotDelivered, L3OutcomeUnknown, L3BusinessError) as exc:
        return await _unknown(f"prepare(resume): {type(exc).__name__}")

    # ── execute ─────────────────────────────────────────────────────────
    execute_body = Execute(
        customer_id=caller_customer_id,
        plan_id=plan_id,
        attempt=new_attempt,
        plan_hash=plan["plan_hash"],
        steps=plan_draft.steps,
        expected_versions=new_decision.versions,
        clock_version=new_decision.clock_version,
        authorization_evidence=evidence,
    )
    try:
        exec_resp = await l3.execute(execute_body.model_dump(mode="json"))
    except L3BusinessError as exc:
        return await _unknown(f"execute(resume): L3BusinessError {exc.code}")
    except (L3NotDelivered, L3OutcomeUnknown) as exc:
        return await _unknown(f"execute(resume): {type(exc).__name__}")

    result = ExecutionResult.model_validate(exec_resp)
    if result.status != "EXECUTED":
        return await _unknown(f"execute(resume) returned {result.status}")

    # ── archive terminal ────────────────────────────────────────────────
    async with conn.begin():
        await state.archive_execution_terminal(
            conn,
            expected=exec_identity,
            from_status=ExecutionJobStatus.EXECUTING,
            l3_status="COMMITTED",
            result=result,
        )

    return JSONResponse(status_code=200, content=result.model_dump(mode="json"))


# ── G8 POST /plans/{plan_id}/execute ─────────────────────────────────────────


async def _reconcile_job(
    *,
    conn: AsyncConnection,
    plan_id: str,
    caller_customer_id: str,
    plan: dict[str, Any],
    job: dict[str, Any],
    l3: Any,
    trace_id: str | None = None,
) -> dict[str, Any] | None:
    """Reconcile an UNKNOWN job (shared by G5 and G8).

    Queries L3 for the true outcome, reconciles uncommitted intents,
    and archives any terminal result.  Returns the terminal result dict,
    or ``None`` when the outcome is still unclear.
    """
    from safebank_contracts.plan import Reconcile
    from safebank_contracts.protocol import ExecutionStatus

    revision = (
        await conn.execute(
            select(t.evaluations.c.revision)
            .where(t.evaluations.c.plan_id == plan_id)
            .order_by(t.evaluations.c.revision.desc())
            .limit(1)
        )
    ).scalar()
    if revision is None:
        return None

    exec_identity = state.ExecutionIdentity(
        customer_id=caller_customer_id,
        plan_id=plan_id,
        origin=plan["origin"],
        attempt=job["l3_attempt"],
        revision=revision,
    )

    if conn.in_transaction():
        await conn.rollback()

    # ── step 1: query L3 ──────────────────────────────────────────────────
    try:
        exec_status_resp = await l3.execution(caller_customer_id, plan_id)
    except L3BusinessError as exc:
        if exc.status == 404:
            try:
                reconcile_body = Reconcile(
                    customer_id=caller_customer_id,
                    plan_hash=plan["plan_hash"],
                    cancel_if_uncommitted=True,
                )
                await l3.reconcile(plan_id, reconcile_body.model_dump(mode="json"))
            except (L3NotDelivered, L3OutcomeUnknown, L3BusinessError):
                return None
            async with conn.begin():
                await state.archive_execution_terminal(
                    conn,
                    expected=exec_identity,
                    from_status=ExecutionJobStatus.UNKNOWN,
                    l3_status="CANCELLED",
                    result=None,
                )
                await audit_append(
                    conn,
                    customer_id=caller_customer_id,
                    event_type="EXECUTION_RECONCILED",
                    payload={
                        "plan_id": plan_id,
                        "reconciled_status": "CANCELLED",
                        "l3_detail": "404 — never prepared",
                    },
                    event_id=new_id("evt"),
                    trace_id=trace_id,
                    plan_id=plan_id,
                    now_wall=_exec_now(),
                )
            return ExecutionResult(
                plan_id=plan_id,
                status="FAILED",
                steps=(),
                versions={},
                committed_at=None,
                error_code=None,
                replayed=True,
            ).model_dump(mode="json")
        return None
    except (L3NotDelivered, L3OutcomeUnknown):
        return None

    exec_status = ExecutionStatus.model_validate(exec_status_resp)

    # ── step 2a: L3 committed → archive ──────────────────────────────────
    if exec_status.status == "COMMITTED" and exec_status.result is not None:
        async with conn.begin():
            await state.archive_execution_terminal(
                conn,
                expected=exec_identity,
                from_status=ExecutionJobStatus.UNKNOWN,
                l3_status="COMMITTED",
                result=exec_status.result,
            )
            await audit_append(
                conn,
                customer_id=caller_customer_id,
                event_type="EXECUTION_RECONCILED",
                payload={"plan_id": plan_id, "reconciled_status": "COMMITTED"},
                event_id=new_id("evt"),
                trace_id=trace_id,
                plan_id=plan_id,
                now_wall=_exec_now(),
            )
        result_dict = exec_status.result.model_dump(mode="json")
        result_dict["replayed"] = True
        return result_dict

    # ── step 2b: reconcile → CANCELLED ───────────────────────────────────
    try:
        reconcile_body = Reconcile(
            customer_id=caller_customer_id,
            plan_hash=plan["plan_hash"],
            cancel_if_uncommitted=True,
        )
        reconcile_resp = await l3.reconcile(plan_id, reconcile_body.model_dump(mode="json"))
    except (L3NotDelivered, L3OutcomeUnknown, L3BusinessError):
        return None

    reconcile_status = ExecutionStatus.model_validate(reconcile_resp)

    if reconcile_status.status == "COMMITTED" and reconcile_status.result is not None:
        async with conn.begin():
            await state.archive_execution_terminal(
                conn,
                expected=exec_identity,
                from_status=ExecutionJobStatus.UNKNOWN,
                l3_status="COMMITTED",
                result=reconcile_status.result,
            )
            await audit_append(
                conn,
                customer_id=caller_customer_id,
                event_type="EXECUTION_RECONCILED",
                payload={"plan_id": plan_id, "reconciled_status": "COMMITTED"},
                event_id=new_id("evt"),
                trace_id=trace_id,
                plan_id=plan_id,
                now_wall=_exec_now(),
            )
        result_dict = reconcile_status.result.model_dump(mode="json")
        result_dict["replayed"] = True
        return result_dict

    if reconcile_status.status == "CANCELLED":
        async with conn.begin():
            await state.archive_execution_terminal(
                conn,
                expected=exec_identity,
                from_status=ExecutionJobStatus.UNKNOWN,
                l3_status="CANCELLED",
                result=None,
            )
            await audit_append(
                conn,
                customer_id=caller_customer_id,
                event_type="EXECUTION_RECONCILED",
                payload={"plan_id": plan_id, "reconciled_status": "CANCELLED"},
                event_id=new_id("evt"),
                trace_id=trace_id,
                plan_id=plan_id,
                now_wall=_exec_now(),
            )
        return ExecutionResult(
            plan_id=plan_id,
            status="FAILED",
            steps=(),
            versions={},
            committed_at=None,
            error_code=None,
            replayed=True,
        ).model_dump(mode="json")

    return None


async def _handle_reconcile(
    *,
    conn: AsyncConnection,
    plan_id: str,
    caller_customer_id: str,
    plan: dict[str, Any],
    job: dict[str, Any],
    l3: Any,
    request: Request,
) -> JSONResponse:
    """Inline reconciliation for UNKNOWN jobs (T5).

    Delegates to :func:`_reconcile_job` and wraps the result.
    """
    trace_id = getattr(request.state, "trace_id", "")
    result = await _reconcile_job(
        conn=conn,
        plan_id=plan_id,
        caller_customer_id=caller_customer_id,
        plan=plan,
        job=job,
        l3=l3,
        trace_id=trace_id,
    )
    if result is not None:
        return JSONResponse(status_code=200, content=result)
    return JSONResponse(
        status_code=202,
        content=ExecutionAccepted(plan_id=plan_id).model_dump(mode="json"),
    )


@router.post("/plans/{plan_id}/execute")
async def execute_plan(plan_id: str, request: Request):
    caller = getattr(request.state, "caller", None)
    if caller is None or caller.customer_id is None:
        raise GatewayError(Code.UNAUTHENTICATED, 401)

    body = await request.json()
    try:
        ExecuteRequest.model_validate(body)
    except Exception:
        raise GatewayError(Code.INVALID_REQUEST, 422, "expected {request_id: str}") from None

    app_state = request.app.state
    l3 = app_state.l3
    wall_now = getattr(app_state, "wall_now", None)
    if wall_now is None:
        raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503)
    now = wall_now()

    async with customer_lock([caller.customer_id]) as conn:
        # ── 5.1 load + validate ──────────────────────────────────────────

        plan = await _load_plan(conn, plan_id, caller.customer_id)
        # origin must match before anything else, including terminal replays
        # (PRD §7.1: origin comes from the credential, never the request body).
        if plan["origin"] != caller.origin:
            raise GatewayError(Code.OWNERSHIP_MISMATCH, 403)
        sid = plan["sid"]
        session = await _load_session(conn, sid)

        # ── request_dedup (C4) ─────────────────────────────────────────
        request_body_bytes = json.dumps(body, sort_keys=True).encode("utf-8")
        request_hash = digest_hex(request_body_bytes)
        replay_response, is_non_terminal = await _check_request_dedup(
            conn,
            customer_id=caller.customer_id,
            request_id=body["request_id"],
            request_hash=request_hash,
        )
        if replay_response is not None:
            return replay_response  # terminal — return as-is

        # idempotent replay: if already terminal (EXECUTED or FAILED),
        # return the existing result with replayed=True.
        if plan["status"] in {PlanStatus.EXECUTED.value, PlanStatus.FAILED.value}:
            job = (
                (
                    await conn.execute(
                        select(t.execution_jobs.c.result).where(
                            t.execution_jobs.c.plan_id == plan_id
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if job is not None and job["result"] is not None:
                cached = dict(job["result"])
                cached["replayed"] = True
                return cached

        # ── ADR-008: detect PAUSED job (resume after fence) ──────────────
        # Must be checked before the EVALUATED/CONFIRMED status gate because
        # a PAUSED plan may have a non-EVALUATED/non-CONFIRMED plan status
        # (the fence sets it back to EVALUATED or PENDING_APPROVAL, which the
        # user may then confirm).
        existing_job = (
            (
                await conn.execute(
                    select(t.execution_jobs).where(t.execution_jobs.c.plan_id == plan_id)
                )
            )
            .mappings()
            .one_or_none()
        )

        if existing_job is not None:
            if existing_job["status"] == "PAUSED":
                # ADR-008: old request_id must not trigger a new attempt (C4).
                if is_non_terminal:
                    raise GatewayError(
                        Code.STATE_CHANGED,
                        409,
                        "state changed; use a new request_id",
                    )
                # Determine is_confirmed for resume path
                _resume_confirmed = plan["status"] == PlanStatus.CONFIRMED.value
                if plan["origin"] != caller.origin:
                    raise GatewayError(Code.OWNERSHIP_MISMATCH, 403)

                eval_row = (
                    (
                        await conn.execute(
                            select(t.evaluations)
                            .where(t.evaluations.c.plan_id == plan_id)
                            .order_by(t.evaluations.c.revision.desc())
                            .limit(1)
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if eval_row is None:
                    raise GatewayError(Code.STATE_CHANGED, 409, "no evaluation for plan")

                _resume_approval = None
                if _resume_confirmed:
                    _resume_approval = (
                        (
                            await conn.execute(
                                select(t.approvals)
                                .where(
                                    t.approvals.c.plan_id == plan_id,
                                    t.approvals.c.status == ApprovalStatus.CONFIRMED.value,
                                )
                                .order_by(t.approvals.c.confirmed_at_wall.desc())
                                .limit(1)
                                .with_for_update()
                            )
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if _resume_approval is None:
                        raise GatewayError(
                            Code.STATE_CHANGED, 409, "no CONFIRMED approval for plan"
                        )
                    if _resume_approval["customer_id"] != caller.customer_id:
                        raise GatewayError(Code.OWNERSHIP_MISMATCH, 409)
                    if _resume_approval["expires_at_wall"] <= now:
                        raise GatewayError(Code.STATE_CHANGED, 409, "approval has expired")

                return await _handle_resume(
                    conn=conn,
                    plan_id=plan_id,
                    caller_customer_id=caller.customer_id,
                    plan=plan,
                    session=session,
                    sid=sid,
                    eval_row=eval_row,
                    is_confirmed=_resume_confirmed,
                    approval=_resume_approval,
                    wall_now=wall_now,
                    now=now,
                    l3=l3,
                    caller=caller,
                    request=request,
                    job_attempt=existing_job["l3_attempt"],
                )
            if existing_job["status"] == "UNKNOWN":
                return await _handle_reconcile(
                    conn=conn,
                    plan_id=plan_id,
                    caller_customer_id=caller.customer_id,
                    plan=plan,
                    job=existing_job,
                    l3=l3,
                    request=request,
                )
            raise GatewayError(Code.EXECUTION_IN_PROGRESS, 409, "execution already in progress")

        # EVALUATED (ALLOW) or CONFIRMED
        if plan["status"] not in {PlanStatus.EVALUATED.value, PlanStatus.CONFIRMED.value}:
            raise GatewayError(
                Code.DEPENDENCY_UNAVAILABLE,
                503,
                "only EVALUATED or CONFIRMED plans are executable in this component",
            )

        is_confirmed = plan["status"] == PlanStatus.CONFIRMED.value

        # G8 is reachable by both agent and app_form callers; the caller's
        # origin must match the plan's origin (PRD §7.1: origin is derived
        # from the credential, never from the request body).
        if plan["origin"] != caller.origin:
            raise GatewayError(Code.OWNERSHIP_MISMATCH, 403)

        eval_row = (
            (
                await conn.execute(
                    select(t.evaluations)
                    .where(t.evaluations.c.plan_id == plan_id)
                    .order_by(t.evaluations.c.revision.desc())
                    .limit(1)
                )
            )
            .mappings()
            .one_or_none()
        )
        if eval_row is None:
            raise GatewayError(Code.STATE_CHANGED, 409, "no evaluation for plan")

        if is_confirmed:
            # Load the latest CONFIRMED approval for this plan
            approval = (
                (
                    await conn.execute(
                        select(t.approvals)
                        .where(
                            t.approvals.c.plan_id == plan_id,
                            t.approvals.c.status == ApprovalStatus.CONFIRMED.value,
                        )
                        .order_by(t.approvals.c.confirmed_at_wall.desc())
                        .limit(1)
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if approval is None:
                raise GatewayError(Code.STATE_CHANGED, 409, "no CONFIRMED approval for plan")
            if approval["customer_id"] != caller.customer_id:
                raise GatewayError(Code.OWNERSHIP_MISMATCH, 409)
            if approval["expires_at_wall"] <= now:
                raise GatewayError(Code.STATE_CHANGED, 409, "approval has expired")
        else:
            decision_value = eval_row["decision"].get("decision")
            if decision_value != "ALLOW":
                raise GatewayError(
                    Code.DEPENDENCY_UNAVAILABLE,
                    503,
                    "only ALLOW decisions are executable in this component",
                )

        # ── plan_draft ───────────────────────────────────────────────────

        if plan["plan_draft"] is None:
            raise GatewayError(
                Code.DEPENDENCY_UNAVAILABLE,
                503,
                "plan has no persisted plan_draft",
            )
        plan_draft = PlanDraft.model_validate(plan["plan_draft"])

        # ── deadline ─────────────────────────────────────────────────────

        deadline_cap_wall = await _compute_deadline_cap(conn, plan["utterance_ids"])
        if is_confirmed and approval["expires_at_wall"] < deadline_cap_wall:
            deadline_cap_wall = approval["expires_at_wall"]

        # ── replay ───────────────────────────────────────────────────────

        from .plans import _load_authorization

        authorization = await _load_authorization(conn, sid, caller.customer_id, now)

        ctx = EvalContext(
            request=None,
            identity=caller,
            authorization=authorization,
            plan_draft=plan_draft,
            plan_id=plan_id,
            origin=plan["origin"],
            customer_id=caller.customer_id,
            session_id=sid,
            profile="B2",
            wall_now=wall_now,
            conn=conn,
            l3=l3,
            audit=None,
        )

        new_decision, fresh = await replay_with_context(ctx)

        # Replay decision validation
        if is_confirmed:
            if new_decision.decision.value != "REQUIRE_APPROVAL":
                raise GatewayError(
                    Code.STATE_CHANGED, 409, "replay decision no longer REQUIRE_APPROVAL"
                )
            # Build view and verify view_hash matches confirmed approval
            fresh.decision = fresh.decision.model_copy(
                update={"evaluation_id": approval["evaluation_id"]}
            )
            try:
                view, replayed_view_hash = build_view(fresh)
            except ValueError as exc:
                raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503) from exc
            if replayed_view_hash != approval["view_hash"]:
                raise GatewayError(
                    Code.STATE_CHANGED, 409, "replay view_hash differs from confirmed view_hash"
                )
        else:
            if new_decision.decision.value != "ALLOW":
                raise GatewayError(Code.STATE_CHANGED, 409, "replay decision no longer ALLOW")

        # plan_hash equivalence: same expression as G4 (plans.py:289-291)
        replayed_hash = digest(
            [{"tool": s.tool, "args": s.args, "step_id": s.step_id} for s in fresh.steps]
        )
        if replayed_hash != plan["plan_hash"]:
            raise GatewayError(Code.STATE_CHANGED, 409, "replay plan_hash differs from stored plan")

        # ── 5.2 write transaction ────────────────────────────────────────

        # Close any implicit transaction from reads/pipeline before opening
        # the explicit persistence transaction.
        await conn.rollback()

        new_revision = eval_row["revision"] + 1
        new_eval_id = new_id("evl")
        new_risk = new_decision.risk.value
        now_wall = _exec_now()

        if is_confirmed:
            evidence = _build_confirmed_evidence(
                plan=plan,
                evaluation_id=new_eval_id,
                risk=new_risk,
                session=session,
                approval=approval,
                replayed_view_hash=replayed_view_hash,
            )
        else:
            evidence = _build_evidence(
                plan=plan,
                evaluation_id=new_eval_id,
                risk=new_risk,
                session=session,
            )

        async with conn.begin():
            # Re-verify session hasn't been revoked since step 5.1.
            session_row = (
                (
                    await conn.execute(
                        select(
                            t.sessions.c.revoked_at_wall,
                            t.sessions.c.authorization_epoch,
                        )
                        .where(t.sessions.c.id == sid)
                        .with_for_update()
                    )
                )
                .mappings()
                .one_or_none()
            )
            if session_row is None:
                raise GatewayError(Code.OWNERSHIP_MISMATCH, 404, "session not found")
            if session_row["revoked_at_wall"] is not None:
                raise GatewayError(Code.AUTHORIZATION_CANCELLED, 409, "session has been revoked")
            if session_row["authorization_epoch"] != session["authorization_epoch"]:
                raise GatewayError(Code.AUTHORIZATION_CANCELLED, 409, "authorization epoch changed")

            await append_evaluation(
                conn,
                plan_id=plan_id,
                evaluation_id=new_eval_id,
                revision=new_revision,
                decision=new_decision.model_dump(mode="json"),
                risk=new_risk,
                findings=[f.model_dump(mode="json") for f in new_decision.findings],
                preflight=new_decision.preflight.model_dump(mode="json"),
                versions=new_decision.versions,
                policy_version=new_decision.policy_version,
                clock_version=new_decision.clock_version,
                equivalent_to=eval_row["id"],
                created_at_wall=now_wall,
            )
            # The plan must leave its current status before its live job exists:
            # _protect_transition refuses plan transitions while a job is live.
            if is_confirmed:
                await state.transition(
                    conn,
                    t.plans,
                    id_=plan_id,
                    from_statuses=[PlanStatus.CONFIRMED.value],
                    to=PlanStatus.EXECUTING.value,
                )
                await state.transition(
                    conn,
                    t.approvals,
                    id_=approval["id"],
                    from_statuses=[ApprovalStatus.CONFIRMED.value],
                    to=ApprovalStatus.USED.value,
                )
            else:
                await state.transition(
                    conn,
                    t.plans,
                    id_=plan_id,
                    from_statuses=[PlanStatus.EVALUATED.value],
                    to=PlanStatus.EXECUTING.value,
                )
            # Claim every authorizing scope in this same transaction (PRD §7.4):
            # scope_id is the primary key, so a scope can feed only one plan.
            for scope_id in plan["scope_ids"]:
                held = (
                    (
                        await conn.execute(
                            select(t.authorization_claims)
                            .where(t.authorization_claims.c.scope_id == scope_id)
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
                if held is not None:
                    if held["plan_id"] != plan_id and held["status"] == "CLAIMED":
                        raise GatewayError(Code.EXECUTION_IN_PROGRESS, 409)
                    raise GatewayError(Code.STATE_CHANGED, 409, "authorization scope already used")
                await conn.execute(
                    t.authorization_claims.insert().values(
                        scope_id=scope_id, plan_id=plan_id, status="CLAIMED"
                    )
                )
            await conn.execute(
                t.execution_jobs.insert().values(
                    plan_id=plan_id,
                    status="EXECUTING",
                    l3_attempt=1,
                    retry_count=0,
                    authorization_evidence=evidence.model_dump(mode="json"),
                    deadline_wall=deadline_cap_wall,
                    deadline_cap_wall=deadline_cap_wall,
                    created_at_wall=now_wall,
                    updated_at_wall=now_wall,
                )
            )
            await audit_append(
                conn,
                customer_id=caller.customer_id,
                event_type="EXECUTION_STARTED",
                payload={
                    "plan_id": plan_id,
                    "origin": plan["origin"],
                    "decision": "ALLOW",
                    "evaluation_id": new_eval_id,
                },
                event_id=new_id("evt"),
                trace_id=getattr(request.state, "trace_id", None),
                plan_id=plan_id,
                now_wall=now_wall,
            )

        exec_identity = state.ExecutionIdentity(
            customer_id=caller.customer_id,
            plan_id=plan_id,
            origin=plan["origin"],
            attempt=1,
            revision=new_revision,
        )

        async def _unknown(reason: str):
            # The job is already committed EXECUTING: never leave it there and
            # never resubmit. Record UNKNOWN (claims retained) and let
            # reconciliation decide the real L3 outcome.
            async with conn.begin():
                await state.mark_execution_unknown(
                    conn,
                    expected=exec_identity,
                    from_status=ExecutionJobStatus.EXECUTING,
                    reason={"reason": reason},
                )
                # Write non-terminal dedup so the same request_id can't be reused.
                await conn.execute(
                    t.request_dedup.insert().values(
                        customer_id=caller.customer_id,
                        route=ROUTE_EXECUTE,
                        request_id=body["request_id"],
                        request_hash=request_hash,
                        status_code=202,
                        response={"non_terminal": True},
                        created_at_wall=text("now()"),
                    )
                )
            return JSONResponse(
                status_code=202,
                content=ExecutionAccepted(plan_id=plan_id).model_dump(mode="json"),
            )

        # ── 5.3 L3 prepare ────────────────────────────────────────────────

        prepare_body = Prepare(
            customer_id=caller.customer_id,
            plan_id=plan_id,
            attempt=1,
            plan_hash=plan["plan_hash"],
            steps=plan_draft.steps,
            deadline_wall=deadline_cap_wall,
            deadline_cap_wall=deadline_cap_wall,
            authorization_evidence=evidence,
        )
        try:
            await l3.prepare(prepare_body.model_dump(mode="json"))
        except (L3NotDelivered, L3OutcomeUnknown, L3BusinessError) as exc:
            return await _unknown(f"prepare: {type(exc).__name__}")

        # ── 5.4 L3 execute ────────────────────────────────────────────────

        execute_body = Execute(
            customer_id=caller.customer_id,
            plan_id=plan_id,
            attempt=1,
            plan_hash=plan["plan_hash"],
            steps=plan_draft.steps,
            expected_versions=new_decision.versions,
            clock_version=new_decision.clock_version,
            authorization_evidence=evidence,
        )
        try:
            exec_resp = await l3.execute(execute_body.model_dump(mode="json"))
        except L3BusinessError as exc:
            if exc.code == "STATE_CHANGED":
                # ADR-008 fence: prepare succeeded but state changed before execute.
                # Write non-terminal dedup so the same request_id can't be reused.
                await conn.rollback()
                async with conn.begin():
                    await conn.execute(
                        t.request_dedup.insert().values(
                            customer_id=caller.customer_id,
                            route=ROUTE_EXECUTE,
                            request_id=body["request_id"],
                            request_hash=request_hash,
                            status_code=409,
                            response={"non_terminal": True},
                            created_at_wall=text("now()"),
                        )
                    )
                return await _handle_state_changed(
                    conn=conn,
                    plan_id=plan_id,
                    caller_customer_id=caller.customer_id,
                    plan=plan,
                    eval_row=eval_row,
                    exec_identity=exec_identity,
                    plan_draft=plan_draft,
                    evidence=evidence,
                    wall_now=wall_now,
                    l3=l3,
                    deadline_cap_wall=deadline_cap_wall,
                    sid=sid,
                    caller=caller,
                    request=request,
                )
            return await _unknown(f"execute: {type(exc).__name__}")
        except (L3NotDelivered, L3OutcomeUnknown) as exc:
            return await _unknown(f"execute: {type(exc).__name__}")

        result = ExecutionResult.model_validate(exec_resp)
        if result.status != "EXECUTED":
            return await _unknown(f"execute returned {result.status}")

        # ── 5.5 archive terminal ──────────────────────────────────────────

        async with conn.begin():
            await state.archive_execution_terminal(
                conn,
                expected=exec_identity,
                from_status=ExecutionJobStatus.EXECUTING,
                l3_status="COMMITTED",
                result=result,
            )

            # Write terminal request_dedup in the same tx (C4).
            await conn.execute(
                t.request_dedup.insert().values(
                    customer_id=caller.customer_id,
                    route=ROUTE_EXECUTE,
                    request_id=body["request_id"],
                    request_hash=request_hash,
                    status_code=200,
                    response=result.model_dump(mode="json"),
                    created_at_wall=text("now()"),
                )
            )

    return result.model_dump(mode="json")
