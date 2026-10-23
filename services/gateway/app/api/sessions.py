"""G3/G15/G18–G22a: immutable raw authorization and L2-issued UI sources."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from safebank_contracts.codes import Code
from sqlalchemy import select

from .. import locks
from .. import tables as t
from ..audit.chain import append, append_tx
from ..auth.tokens import check_revocation, issue_assistant
from ..errors import GatewayError
from ..idempotency import request_idempotent
from ..schemas.enums import ServiceKind
from ..utterance.lifecycle import Revocation, deferred_error, raise_cached_error
from ..utterance.loader import as_ref
from ..utterance.models import ActionRequest, Registration, RequestId
from ..utterance.parser import LEXICON, is_correction, normalize
from ..utterance.runtime import db, l3, private_key, require_database, session_for, wall_now
from ..utterance.service import (
    clarification_fields,
    current_clarification,
    form_tool,
    new_id,
    predicate_values,
    register,
    snapshot,
)

router = APIRouter(dependencies=[Depends(require_database)])


async def _audit(request: Request, event: str, payload: dict) -> None:
    await append_tx(
        db(request),
        customer_id=request.state.caller.customer_id,
        event_type=event,
        payload=payload,
        event_id=new_id("evt"),
        now_wall=wall_now(request),
    )


async def _dedup(conn, request, body, route, produce):
    return await request_idempotent(
        conn,
        customer_id=request.state.caller.customer_id,
        route=route,
        request_id=body.request_id,
        body=body.model_dump(mode="json"),
        produce=produce,
    )


async def _already_registered(conn, request, body, route):
    """Only peek for a completed retry; all idempotency semantics remain in helper."""
    exists = await conn.scalar(
        select(t.request_dedup.c.request_id).where(
            t.request_dedup.c.customer_id == request.state.caller.customer_id,
            t.request_dedup.c.route == route,
            t.request_dedup.c.request_id == body.request_id,
        )
    )
    if exists is None:
        return None

    async def unreachable():
        raise AssertionError("existing request must be handled by request_idempotent")

    return await _dedup(conn, request, body, route, unreachable)


@router.post("/sessions", status_code=201)
async def create_session(request: Request, body: RequestId):
    caller = request.state.caller
    now = wall_now(request)
    async with locks.customer_lock([caller.customer_id]) as conn:
        async with conn.begin():
            await check_revocation(conn, caller, now=now)

            async def produce():
                sid = new_id("ses")
                await conn.execute(
                    t.sessions.insert().values(
                        id=sid, customer_id=caller.customer_id, created_at_wall=now
                    )
                )
                return 201, {"session_id": sid, "created_at_wall": now.isoformat()}

            status, result, replayed = await _dedup(conn, request, body, "/sessions", produce)
    if not replayed:
        await _audit(request, "SESSION_CREATED", {"session_id": result["session_id"]})
    return JSONResponse(result, status_code=status)


@router.post("/sessions/{sid}/utterances", status_code=201)
async def register_utterance(sid: str, request: Request, body: Registration):
    caller = request.state.caller
    route = f"/sessions/{sid}/utterances"
    async with locks.customer_lock([caller.customer_id]) as conn:
        # No L3 call occurs inside a SQL transaction, including the pre-check.
        async with conn.begin():
            await session_for(conn, sid, caller, wall_now(request))
            cached = await _already_registered(conn, request, body, route)
        if cached is not None:
            raise_cached_error(cached[0], cached[1])
            return JSONResponse(cached[1], status_code=cached[0])
        cancellation = (
            body.kind == "typed" and normalize(body.text).strip(" 。.!！") in LEXICON["cancel"]
        )
        correction = body.kind == "typed" and is_correction(body.text)
        evidence = []
        try:
            bank = None if cancellation else await snapshot(l3(request), caller.customer_id)
            values = (
                {}
                if cancellation
                else await predicate_values(
                    l3(request), bank, caller.customer_id, body.text or "", evidence
                )
            )
        except GatewayError as exc:
            if not correction or exc.code not in {
                Code.DEPENDENCY_UNAVAILABLE,
                Code.STATE_CHANGED,
                Code.ASSESSMENT_MISSING,
            }:
                raise
            # User withdrew the previous instruction even if new data cannot be read.
            # Persist the correction as FORM_REQUIRED and grant no replacement scope.
            bank, values, evidence = None, {}, []
        async with conn.begin():
            now = wall_now(request)
            session = await session_for(conn, sid, caller, now, lock=True)

            async def produce():
                registered = await register(conn, sid, session, body, bank, now, values, evidence)
                if isinstance(registered, Revocation):
                    await append(
                        conn,
                        customer_id=caller.customer_id,
                        event_type="AUTHORIZATION_REVOKED",
                        event_id=new_id("evt"),
                        trace_id=request.state.trace_id,
                        now_wall=now,
                        payload={
                            "sid": sid,
                            "utterance_ids": list(registered.utterance_ids),
                            "pending_plan_ids": list(registered.pending_plan_ids),
                            "reason": "cancel" if cancellation else "supersede",
                            "cleanup_pending": True,
                        },
                    )
                    return 409, deferred_error().body(request.state.trace_id)
                utterance, expiry = registered
                token, token_expiry = await issue_assistant(
                    conn,
                    private_key(request),
                    caller.customer_id,
                    sid,
                    [utterance.id],
                    now,
                    expires_at=expiry,
                )
                return 201, {
                    "utterance": utterance.model_dump(mode="json"),
                    "clarification_id": utterance.clarification_id,
                    "assistant_token": token,
                    "expires_at_wall": token_expiry.isoformat(),
                }

            status, result, replayed = await _dedup(conn, request, body, route, produce)
    raise_cached_error(status, result)
    if not replayed:
        await _audit(
            request,
            "UTTERANCE_REGISTERED",
            {
                "utterance_id": result["utterance"]["id"],
                "parse_status": result["utterance"]["parse_status"],
            },
        )
    return JSONResponse(result, status_code=status)


@router.post("/sessions/{sid}/assistant-token")
async def renew_assistant_token(sid: str, request: Request, body: RequestId):
    caller = request.state.caller
    async with locks.customer_lock([caller.customer_id]) as conn:
        async with conn.begin():
            now = wall_now(request)
            await session_for(conn, sid, caller, now, lock=True)

            async def produce():
                token, expiry = await issue_assistant(
                    conn, private_key(request), caller.customer_id, sid, [], now
                )
                return 200, {"assistant_token": token, "expires_at_wall": expiry.isoformat()}

            _, result, _ = await _dedup(
                conn, request, body, f"/sessions/{sid}/assistant-token", produce
            )
    return result


@router.post("/sessions/{sid}/actions")
async def create_action(sid: str, request: Request, body: ActionRequest):
    caller = request.state.caller
    route = f"/sessions/{sid}/actions"
    async with locks.customer_lock([caller.customer_id]) as conn:
        async with conn.begin():
            await session_for(conn, sid, caller, wall_now(request))
            cached = await _already_registered(conn, request, body, route)
        if cached is not None:
            return JSONResponse(cached[1], status_code=cached[0])
        bank = await snapshot(l3(request), caller.customer_id) if body.kind == "FORM" else None
        async with conn.begin():
            now = wall_now(request)
            await session_for(conn, sid, caller, now, lock=True)

            async def produce():
                from datetime import timedelta

                expiry = now + timedelta(minutes=10)
                fields = body.fields
                if body.kind == "FORM":
                    _, fields = form_tool(body.action_kind.value, body.fields, bank)
                else:
                    clarification = await current_clarification(conn, sid, now)
                    if (
                        clarification is None
                        or body.parent_utterance_id not in clarification["parent_utterance_ids"]
                    ):
                        raise GatewayError(Code.AUTHORIZATION_CANCELLED, 409)
                    clarification_fields(clarification, body.fields)
                    expiry = min(expiry, clarification["expires_at_wall"])
                id_ = new_id("act")
                await conn.execute(
                    t.form_actions.insert().values(
                        id=id_,
                        sid=sid,
                        kind=body.kind,
                        action_kind=body.action_kind.value if body.action_kind else None,
                        normalized_fields=fields,
                        parent_utterance_id=body.parent_utterance_id,
                        created_at_wall=now,
                        expires_at_wall=expiry,
                    )
                )
                return 200, {
                    "action_id": id_,
                    "normalized_fields": fields,
                    "expires_at_wall": expiry.isoformat(),
                }

            _, result, replayed = await _dedup(conn, request, body, route, produce)
    if not replayed:
        await _audit(request, "ACTION_CREATED", {"action_id": result["action_id"]})
    return result


@router.get("/sessions/{sid}/utterances")
async def list_utterances(sid: str, request: Request, ids: str | None = None):
    caller = request.state.caller
    now = wall_now(request)
    selected = ids.split(",") if ids else None
    if selected is not None and (len(selected) > 100 or len(set(selected)) != len(selected)):
        raise GatewayError(Code.CONTRACT_INVALID, 422)
    if caller.kind is ServiceKind.A:
        if selected is not None and not set(selected).issubset(caller.utts):
            raise GatewayError(Code.AUTHORIZATION_CONSUMED, 409)
        selected = list(caller.utts) if selected is None else selected
    async with db(request).connect() as conn:
        session = await session_for(conn, sid, caller, now)
        query = select(t.utterances).where(
            t.utterances.c.sid == sid,
            t.utterances.c.status == "ACTIVE",
            t.utterances.c.authorization_epoch == session["authorization_epoch"],
            t.utterances.c.expires_at_wall > now,
        )
        if selected is not None:
            query = query.where(t.utterances.c.id.in_(selected))
        rows = (
            (await conn.execute(query.order_by(t.utterances.c.created_at_wall, t.utterances.c.id)))
            .mappings()
            .all()
        )
    return {"items": [as_ref(row).model_dump(mode="json") for row in rows]}


@router.get("/sessions/{sid}/clarifications/current")
async def get_clarification(sid: str, request: Request):
    async with db(request).connect() as conn:
        now = wall_now(request)
        session = await session_for(conn, sid, request.state.caller, now)
        row = await current_clarification(conn, sid, now)
        if row is None or row["authorization_epoch"] != session["authorization_epoch"]:
            return {"clarification": None}
        result = {
            key: row[key]
            for key in (
                "id",
                "kind",
                "parent_utterance_ids",
                "required_fields",
                "prompt_template_id",
                "partial_scope",
            )
        }
        result.update(
            candidates=row["candidate_refs"], expires_at_wall=row["expires_at_wall"].isoformat()
        )
        return {"clarification": result}


@router.get("/sessions/{sid}/hints/{hint_id}")
async def get_hint(sid: str, hint_id: str, request: Request):
    async with db(request).connect() as conn:
        now = wall_now(request)
        await session_for(conn, sid, request.state.caller, now)
        row = (
            (
                await conn.execute(
                    select(t.hints).where(t.hints.c.id == hint_id, t.hints.c.sid == sid)
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise GatewayError(Code.NOT_FOUND, 404)
        if row["used_by"] is not None:
            raise GatewayError(Code.AUTHORIZATION_CONSUMED, 409)
        if row["expires_at_wall"] is None or row["expires_at_wall"] <= now:
            raise GatewayError(Code.UTTERANCE_EXPIRED, 409)
        return {
            "id": row["id"],
            "label": row["label"],
            "scope": row["scope_json"],
            "expires_at_wall": row["expires_at_wall"].isoformat(),
        }
