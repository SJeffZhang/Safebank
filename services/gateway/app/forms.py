"""Credential-bound FORM views. Never materialize an action as an utterance row."""

from __future__ import annotations

import json
from datetime import datetime

from safebank_contracts.codes import Code
from safebank_contracts.plan import PlanDraft
from safebank_contracts.state import BankState
from sqlalchemy import select

from . import tables as t
from .errors import GatewayError
from .policy.registry import policy
from .schemas.enums import ParseStatus, UtteranceStatus
from .schemas.scope import AuthorizationInputs, UtteranceRef
from .utterance.parser import echoes_untrusted
from .utterance.service import form_tool, scope_from_form, untrusted_free_text


async def load_forms(
    conn,
    sid,
    customer_id,
    ids,
    now: datetime,
    *,
    plan_id=None,
    l3=None,
    bank=None,
    lock=False,
    expected_rows=None,
    expected_epoch=None,
    require_bound=False,
):
    if not ids or len(ids) != len(set(ids)):
        raise GatewayError(Code.CONTRACT_INVALID, 422)
    session = (
        (
            await conn.execute(
                select(t.sessions).where(
                    t.sessions.c.id == sid, t.sessions.c.customer_id == customer_id
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if session is None:
        raise GatewayError(Code.OWNERSHIP_MISMATCH, 404)
    if session["revoked_at_wall"] is not None:
        raise GatewayError(Code.AUTHORIZATION_CANCELLED, 409)
    if require_bound:
        bound_plan = (
            (
                await conn.execute(
                    select(t.plans).where(
                        t.plans.c.id == plan_id,
                        t.plans.c.customer_id == customer_id,
                        t.plans.c.sid == sid,
                        t.plans.c.origin == "app_form",
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if (
            bound_plan is None
            or bound_plan["authorization_epoch"] != session["authorization_epoch"]
            or list(ids) != bound_plan["utterance_ids"]
            or list(ids) != bound_plan["scope_ids"]
        ):
            raise GatewayError(Code.AUTHORIZATION_CANCELLED, 409)
    query = (
        select(t.form_actions)
        .where(t.form_actions.c.sid == sid, t.form_actions.c.id.in_(ids))
        .order_by(t.form_actions.c.id)
    )
    if lock:
        query = query.with_for_update()
    rows = (await conn.execute(query)).mappings().all()
    if len(rows) != len(ids):
        raise GatewayError(Code.OWNERSHIP_MISMATCH, 404)
    by_id = {r["id"]: dict(r) for r in rows}
    if (expected_rows is not None and by_id != expected_rows) or (
        expected_epoch is not None and session["authorization_epoch"] != expected_epoch
    ):
        raise GatewayError(Code.AUTHORIZATION_CANCELLED, 409)
    for row in rows:
        if row["kind"] != "FORM":
            raise GatewayError(Code.CONTRACT_INVALID, 422)
        if (require_bound and row["used_by"] != plan_id) or (
            row["used_by"] is not None and row["used_by"] != plan_id
        ):
            raise GatewayError(Code.AUTHORIZATION_CONSUMED, 409)
        if (
            row["created_at_wall"] > now
            or row["expires_at_wall"] is None
            or row["expires_at_wall"] <= now
        ):
            raise GatewayError(Code.UTTERANCE_EXPIRED, 409)
    if bank is None:
        if l3 is None:
            raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503)
        bank = BankState.model_validate(
            await l3.snapshot({"customer_id": customer_id, "steps": [], "event_id": None})
        )
    # Gather only this customer's saved free text, including other sessions.
    sids = (
        (await conn.execute(select(t.sessions.c.id).where(t.sessions.c.customer_id == customer_id)))
        .scalars()
        .all()
    )
    free_text = []
    for own_sid in sids:
        free_text.extend(await untrusted_free_text(conn, own_sid))
    refs = {}
    steps = []
    for index, id_ in enumerate(ids):
        row = by_id[id_]
        tool, args = form_tool(row["action_kind"], row["normalized_fields"], bank)
        refs[id_] = UtteranceRef(
            id=id_,
            kind="action",
            text=None,
            scope=scope_from_form(row["action_kind"], args, bank),
            parse_status=ParseStatus.SCOPED,
            echoes_untrusted=echoes_untrusted(
                json.dumps(args, ensure_ascii=False), bank, customer_id, tuple(free_text)
            ),
            created_at_wall=row["created_at_wall"].isoformat(),
            expires_at_wall=row["expires_at_wall"].isoformat(),
        )
        steps.append(
            {
                "step_id": f"s{index + 1}",
                "tool": tool,
                "args": args,
                "depends_on": [],
                "arg_sources": [
                    {"param": p, "source": {"kind": "action", "ref": id_}}
                    for p in policy(tool).sensitive_params
                    if args.get(p) is not None
                ],
            }
        )
    auth = AuthorizationInputs(
        sid=sid,
        customer_id=customer_id,
        utterance_ids=tuple(ids),
        utterances=refs,
        scope_ids=tuple(ids),
        authorization_epoch=session["authorization_epoch"],
        statuses={id_: UtteranceStatus.ACTIVE for id_ in ids},
    )
    return auth, steps, by_id, bank


def form_draft(request_id, sid, ids, steps):
    return PlanDraft.model_validate(
        {
            "request_id": request_id,
            "session_id": sid,
            "utterance_ids": list(ids),
            "intents": [],
            "rationale": "",
            "steps": steps,
        }
    )
