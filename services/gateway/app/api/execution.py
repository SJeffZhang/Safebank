"""G5 / G8 (W4): plan status and execution.

G8 ALLOW path (T2e): implements the EVALUATED/ALLOW → EXECUTED flow.
CONFIRMED, STATE_CHANGED, UNKNOWN, G5, and request_id replay are later tasks.
"""

from __future__ import annotations

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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from .. import state
from .. import tables as t
from ..audit.chain import append as audit_append
from ..errors import GatewayError
from ..evaluation.repo import append_evaluation
from ..execution.replay import replay_with_context
from ..l3.client import L3BusinessError, L3NotDelivered, L3OutcomeUnknown
from ..locks import customer_lock
from ..pipeline.context import EvalContext
from ..schemas.enums import ExecutionJobStatus, PlanStatus, ServiceKind
from ..schemas.execution import ExecutionAccepted
from ..utterance.service import new_id

router = APIRouter()


class ExecuteRequest(BaseModel):
    request_id: str


# ── helpers ──────────────────────────────────────────────────────────────────


def _exec_now() -> datetime:
    from datetime import UTC

    return datetime.now(UTC)


async def _stub_g5(request: Request) -> None:
    raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503, "G5 plan status lands in W4")


router.get("/plans/{plan_id}")(_stub_g5)


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


# ── G8 POST /plans/{plan_id}/execute ─────────────────────────────────────────


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
        sid = plan["sid"]
        session = await _load_session(conn, sid)

        # idempotent replay: if already terminal, return existing result
        if plan["status"] == PlanStatus.EXECUTED.value:
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
                return job["result"]

        # only EVALUATED ALLOW is implemented now
        if plan["status"] != PlanStatus.EVALUATED.value:
            raise GatewayError(
                Code.DEPENDENCY_UNAVAILABLE,
                503,
                "only EVALUATED plans are executable in this component",
            )

        if caller.kind is not ServiceKind.A or plan["origin"] != "agent":
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

        # ALLOW equivalence: decision must still be ALLOW
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

        evidence = _build_evidence(
            plan=plan,
            evaluation_id=new_eval_id,
            risk=new_risk,
            session=session,
        )

        async with conn.begin():
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
            # The plan must leave EVALUATED before its live job exists:
            # _protect_transition refuses plan transitions while a job is live.
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
        except (L3NotDelivered, L3OutcomeUnknown, L3BusinessError) as exc:
            # STATE_CHANGED after a successful prepare needs the ADR-008 fence
            # (T4); until then it is reconciled like any other unclear outcome.
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

    return result.model_dump(mode="json")
