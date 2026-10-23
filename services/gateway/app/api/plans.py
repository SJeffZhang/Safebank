"""G4 evaluation: fetch HTTP state first, persist in short database transactions.

Resources are supplied centrally on app.state: db, l3, wall_now, settings.
W1 owns load_for_plan; W2 owns stages ①–④; W4 owns view/approval creation.
No local fallback clients, pools, authorization parser or approvals are created.
"""

from __future__ import annotations

from datetime import datetime
from importlib import import_module

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from safebank_bankcore.references import request_digest
from safebank_contracts.codes import Code
from safebank_contracts.plan import STEP_MODELS, PlanDraft
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .. import tables as t
from ..approval import service as approvals
from ..audit import chain as audit
from ..errors import GatewayError
from ..evaluation.repo import append_evaluation
from ..idempotency import request_idempotent
from ..l3.client import L3Error
from ..middleware import digest_hex
from ..pipeline import evaluate
from ..pipeline.context import EvalContext
from ..schemas.decision import DecisionValue
from ..schemas.enums import ParseStatus, ServiceKind, UtteranceStatus
from ..schemas.requests import AppFormPlanRequest
from ..schemas.scope import AuthorizationInputs, NormalizedScope, UtteranceRef
from ..state import transition
from ..workflow.identity import new_id

router = APIRouter()
ROUTE = "POST /plans/evaluate"
FORM_TOOLS = {
    "TRANSFER_EXTERNAL": "transfer",
    "TRANSFER_SELF": "transfer",
    "PAYEE_ADD": "add_payee",
    "SCHEDULE_CREATE": "schedule_transfer",
    "SCHEDULE_CANCEL": "cancel_scheduled",
    "SPLIT_CREATE": "create_split_request",
    "CARD_LIMIT_TIGHTEN": "update_card_limits",
    "CARD_LIMIT_LOOSEN": "update_card_limits",
    "CARD_CONTROL_TIGHTEN": "update_card_controls",
    "CARD_CONTROL_LOOSEN": "update_card_controls",
    "CARD_FREEZE": "freeze_card",
    "CARD_UNFREEZE": "unfreeze_card",
    "CARD_LOST": "report_card_lost",
    "MANDATE_CANCEL": "cancel_subscription",
    "ASSESSMENT_SUBMIT": "submit_risk_assessment",
    "FUND_SUBSCRIBE": "subscribe_product",
    "FUND_REDEEM": "redeem_product",
}


async def load_authorization(conn, sid, ids, caller) -> AuthorizationInputs:
    """Late import preserves W1's ownership while allowing independent tests."""
    try:
        loader = import_module("services.gateway.app.utterance.loader")
    except ModuleNotFoundError as exc:
        if exc.name not in (
            "services.gateway.app.utterance",
            "services.gateway.app.utterance.loader",
        ):
            raise
        raise GatewayError(
            Code.DEPENDENCY_UNAVAILABLE, 503, "authorization loader unavailable"
        ) from exc
    return await loader.load_for_plan(conn, sid, ids, caller)


async def form_plan(conn, body: AppFormPlanRequest, caller, now: datetime, *, plan_id=None):
    """Internal PlanDraft carries form steps so bank_state includes counterparties."""
    if len(set(body.form_action_ids)) != len(body.form_action_ids):
        raise GatewayError(Code.CONTRACT_INVALID, 422)
    session = (
        (
            await conn.execute(
                select(t.sessions)
                .where(
                    t.sessions.c.id == body.session_id,
                    t.sessions.c.customer_id == caller.customer_id,
                )
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if session is None:
        raise GatewayError(Code.NOT_FOUND, 404)
    if session["revoked_at_wall"] is not None:
        raise GatewayError(Code.AUTHORIZATION_CANCELLED, 409)
    rows = (
        (
            await conn.execute(
                select(t.form_actions)
                .where(
                    t.form_actions.c.sid == body.session_id,
                    t.form_actions.c.id.in_(body.form_action_ids),
                )
                .with_for_update()
            )
        )
        .mappings()
        .all()
    )
    by_id = {r["id"]: r for r in rows}
    if set(by_id) != set(body.form_action_ids):
        raise GatewayError(Code.NOT_FOUND, 404)
    steps, refs = [], {}
    for index, id_ in enumerate(body.form_action_ids):
        row = by_id[id_]
        if row["kind"] != "FORM":
            raise GatewayError(Code.ORIGIN_NOT_ALLOWED, 403)
        if row["expires_at_wall"] is None or row["expires_at_wall"] <= now:
            raise GatewayError(Code.UTTERANCE_EXPIRED, 409)
        if row["used_by"] is not None and row["used_by"] != plan_id:
            raise GatewayError(Code.AUTHORIZATION_CONSUMED, 409)
        tool = FORM_TOOLS.get(row["action_kind"])
        if tool is None:
            raise GatewayError(Code.CONTRACT_INVALID, 422)
        fields = row["normalized_fields"]
        try:
            step = STEP_MODELS[tool].model_validate(
                dict(
                    step_id=f"s{index + 1}",
                    tool=tool,
                    args=fields,
                    depends_on=[],
                    arg_sources=[
                        dict(param=k, source=dict(kind="action", ref=id_)) for k in fields
                    ],
                )
            )
        except ValidationError as exc:
            raise GatewayError(Code.CONTRACT_INVALID, 422) from exc
        steps.append(step)
        refs[id_] = UtteranceRef(
            id=id_,
            kind="action",
            text=None,
            parse_status=ParseStatus.SCOPED,
            scope=NormalizedScope(action_kinds=(row["action_kind"],), fields=fields),
            created_at_wall=row["created_at_wall"].isoformat(),
            expires_at_wall=row["expires_at_wall"].isoformat(),
        )
    draft = PlanDraft(
        request_id=body.request_id,
        session_id=body.session_id,
        utterance_ids=body.form_action_ids,
        intents=body.form_action_ids,
        steps=tuple(steps),
        rationale="",
    )
    authorization = AuthorizationInputs(
        sid=body.session_id,
        customer_id=caller.customer_id,
        utterance_ids=(),
        utterances=refs,
        scope_ids=body.form_action_ids,
        authorization_epoch=session["authorization_epoch"],
    )
    return draft, authorization


def public_decision(decision, customer_id):
    """Do not expose counterparty versions or experimental shadow decisions."""
    return decision.model_copy(
        update={
            "versions": {k: v for k, v in decision.versions.items() if k == customer_id},
            "shadow_b1": None,
        }
    ).model_dump(mode="json")


async def cached_response(conn, *, customer_id, origin, body):
    route = ROUTE + ":" + origin
    exists = await conn.scalar(
        select(t.request_dedup.c.request_id).where(
            t.request_dedup.c.customer_id == customer_id,
            t.request_dedup.c.route == route,
            t.request_dedup.c.request_id == body.request_id,
        )
    )
    if exists is None:
        return None

    async def unreachable():
        raise RuntimeError("cached response disappeared")

    status, response, _ = await request_idempotent(
        conn,
        customer_id=customer_id,
        route=route,
        request_id=body.request_id,
        body=body.model_dump(mode="json"),
        produce=unreachable,
    )
    return JSONResponse(status_code=status, content=response)


async def persist(conn, ctx: EvalContext, body, caller, now):
    """All writes and W4 calls below are local database operations only."""
    if caller.kind == ServiceKind.A:
        # Lock the session to serialize evaluation-budget and source lifecycle checks.
        await conn.execute(
            select(t.sessions.c.id).where(t.sessions.c.id == ctx.session_id).with_for_update()
        )
        ctx.authorization = await load_authorization(
            conn,
            ctx.session_id,
            body.utterance_ids,
            caller,
        )
    else:
        _, ctx.authorization = await form_plan(conn, body, caller, now, plan_id=ctx.plan_id)
    for scope_id in ctx.authorization.scope_ids:
        count = await conn.scalar(
            select(func.count())
            .select_from(t.plans)
            .where(
                t.plans.c.customer_id == ctx.customer_id,
                t.plans.c.scope_ids.contains([scope_id]),
            )
        )
        if count >= 6:
            # Rate limits invalidate the scope; persist the error response so that
            # throwing the 429 does not roll back its lifecycle changes.
            rows = (
                (
                    await conn.execute(
                        select(t.utterances.c.id).where(
                            t.utterances.c.id.in_(ctx.authorization.utterance_ids),
                            t.utterances.c.status == UtteranceStatus.ACTIVE.value,
                        )
                    )
                )
                .scalars()
                .all()
            )
            for id_ in rows:
                await transition(
                    conn, t.utterances, id_=id_, from_statuses=["ACTIVE"], to="CANCELLED"
                )
            return 429, GatewayError(Code.RATE_LIMITED, 429).body(ctx.request.state.trace_id)
    if not ctx.steps and not ctx.has_hard_deny():
        raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503, "entity stage unavailable")
    stored = []
    for step in ctx.plan_draft.steps:
        item = step.model_dump(mode="json")
        metadata = next((s for s in ctx.steps if s.step_id == step.step_id), None)
        item.update(
            action_kind=metadata.action_kind if metadata else None,
            base_risk=metadata.base_risk if metadata else None,
            outflow=metadata.outflow if metadata else False,
        )
        target = getattr(step.args, "payee_id", None)
        payee = (
            ctx.snapshot.payees.get(target) if ctx.snapshot and isinstance(target, str) else None
        )
        item["target_fingerprint"] = payee.target_fingerprint if payee else None
        stored.append(item)
    row = dict(
        id=ctx.plan_id,
        customer_id=ctx.customer_id,
        sid=ctx.session_id,
        origin=ctx.origin,
        request_id=body.request_id,
        request_hash=digest_hex(body.model_dump(mode="json")),
        plan_hash=digest_hex(
            dict(customer_id=ctx.customer_id, sid=ctx.session_id, origin=ctx.origin, steps=stored)
        ),
        steps_hash=request_digest(ctx.plan_draft.steps),
        resolved_steps=stored,
        utterance_ids=list(ctx.authorization.utterance_ids),
        scope_ids=list(ctx.authorization.scope_ids),
        status="EVALUATED",
        created_at_wall=now,
    )
    await conn.execute(t.plans.insert().values(**row))
    ctx.conn = conn
    evaluation = await append_evaluation(conn, ctx)
    value = ctx.decision.decision
    to = {
        DecisionValue.DENY: "DENIED",
        DecisionValue.REPLAN: "REPLAN",
        DecisionValue.REQUIRE_APPROVAL: "PENDING_APPROVAL",
    }.get(value)
    if to:
        await transition(conn, t.plans, id_=ctx.plan_id, from_statuses=["EVALUATED"], to=to)
        row["status"] = to
    if value == DecisionValue.REQUIRE_APPROVAL:
        approval_id = await approvals.create_for_evaluation(
            conn,
            plan=row,
            evaluation=evaluation,
            view=evaluation["view"],
        )
        if approval_id is None:
            raise RuntimeError("approval creation returned no id")
        ctx.decision = ctx.decision.model_copy(update={"approval_id": approval_id})
        await conn.execute(
            update(t.evaluations)
            .where(t.evaluations.c.id == evaluation["id"])
            .values(
                decision=ctx.decision.model_dump(mode="json", exclude={"shadow_b1"}),
            )
        )
    if ctx.workflow:
        for target, until in ctx.workflow.cooldowns:
            await conn.execute(
                pg_insert(t.cooldowns)
                .values(
                    customer_id=ctx.customer_id,
                    payee_fingerprint=target,
                    until_at=until,
                    reason=Code.RISKY_SEQUENCE.value,
                )
                .on_conflict_do_update(
                    index_elements=[t.cooldowns.c.customer_id, t.cooldowns.c.payee_fingerprint],
                    set_={"until_at": func.greatest(t.cooldowns.c.until_at, until)},
                )
            )
    if value == DecisionValue.DENY:
        for id_ in ctx.authorization.utterance_ids:
            if (
                ctx.authorization.statuses.get(id_, UtteranceStatus.ACTIVE)
                == UtteranceStatus.ACTIVE
            ):
                await transition(
                    conn, t.utterances, id_=id_, from_statuses=["ACTIVE"], to="CONSUMED"
                )
                await conn.execute(
                    update(t.utterances)
                    .where(t.utterances.c.id == id_)
                    .values(consumed_by=ctx.plan_id)
                )
    if caller.kind == ServiceKind.F:
        await conn.execute(
            update(t.form_actions)
            .where(t.form_actions.c.id.in_(body.form_action_ids))
            .values(used_by=ctx.plan_id)
        )
    return 200, public_decision(ctx.decision, ctx.customer_id)


@router.post("/plans/evaluate")
async def evaluate_plan(request: Request):
    caller = request.state.caller
    if caller is None or caller.customer_id is None or caller.origin is None:
        raise GatewayError(Code.FORBIDDEN, 403)
    try:
        raw = await request.json()
        body = (PlanDraft if caller.kind == ServiceKind.A else AppFormPlanRequest).model_validate(
            raw
        )
    except (ValidationError, ValueError) as exc:
        raise GatewayError(Code.CONTRACT_INVALID, 422) from exc
    if caller.kind == ServiceKind.A and (
        body.session_id != caller.sid or not set(body.utterance_ids).issubset(caller.utts)
    ):
        raise GatewayError(Code.FORBIDDEN, 403)
    db, l3, clock = (getattr(request.app.state, key, None) for key in ("db", "l3", "wall_now"))
    settings = getattr(request.app.state, "settings", None)
    if db is None or l3 is None or clock is None or settings is None:
        raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503, "gateway resources unavailable")
    now = clock()
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("wall_now must be timezone aware")
    async with db.begin() as conn:
        cached = await cached_response(
            conn, customer_id=caller.customer_id, origin=caller.origin, body=body
        )
        if cached:
            return cached
        if caller.kind == ServiceKind.A:
            draft = body
            authorization = await load_authorization(
                conn, body.session_id, body.utterance_ids, caller
            )
        else:
            draft, authorization = await form_plan(conn, body, caller, now)
    ctx = EvalContext(
        request=request,
        identity=caller,
        authorization=authorization,
        plan_draft=draft,
        plan_id=new_id("pln"),
        origin=caller.origin,
        customer_id=caller.customer_id,
        session_id=body.session_id,
        profile=settings.profile,
        wall_now=lambda: now,
        l3=l3,
    )
    try:
        # Load HTTP state while no gateway transaction is open. Stage ②/⑤ use
        # this same cache; subsequent stages only read persisted gateway records.
        await ctx.bank_state()
        async with db.connect() as conn:
            ctx.conn = conn
            await evaluate(ctx)
    except L3Error as exc:
        raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503) from exc
    if ctx.decision is None:
        raise RuntimeError("evaluation pipeline did not return a Decision")
    async with db.begin() as conn:
        status, response, replayed = await request_idempotent(
            conn,
            customer_id=caller.customer_id,
            route=ROUTE + ":" + caller.origin,
            request_id=body.request_id,
            body=body.model_dump(mode="json"),
            produce=lambda: persist(conn, ctx, body, caller, now),
        )
    if not replayed and status == 200:
        await audit.append_tx(
            db,
            customer_id=caller.customer_id,
            event_type="EVALUATION",
            payload=ctx.decision.model_dump(mode="json", exclude={"shadow_b1"}),
            event_id=new_id("evt"),
            plan_id=ctx.plan_id,
            trace_id=getattr(request.state, "trace_id", None),
            now_wall=now,
        )
    return JSONResponse(status_code=status, content=response)
