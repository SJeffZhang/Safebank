"""Publish and verify server hints as bounded, saved authorization proposals.

Private commitments live in hints.view_hash, never inside the public scope.
Writers/adopters hold the session lock; GET is a non-authorizing live view.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from hmac import compare_digest
from typing import Any, NoReturn

from safebank_bankcore.ids import digest
from safebank_contracts.codes import Code
from safebank_contracts.plan import PlanDraft
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from . import tables as t
from .errors import GatewayError
from .pipeline.context import EvalContext
from .schemas.decision import DecisionValue, ReplanHint
from .schemas.enums import ServiceKind, UtteranceStatus
from .schemas.scope import AuthorizationInputs, NormalizedScope
from .taint.authorization import _adopted_sources
from .utterance.loader import as_ref


def _reject() -> NoReturn:
    raise GatewayError(Code.AUTHORIZATION_CANCELLED, 409)


def _aware(value: Any) -> bool:
    return (
        isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None
    )


def _json(value: Any) -> Any:
    if isinstance(value, datetime):
        if not _aware(value):
            _reject()
        return value.astimezone(UTC).isoformat()
    if isinstance(value, dict):
        return {key: _json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    return value


def _narrowed(original: NormalizedScope, candidate: NormalizedScope) -> tuple[int, ...]:
    before, after = original.model_dump(mode="json"), candidate.model_dump(mode="json")
    left, right = before.pop("transfers"), after.pop("transfers")
    if before != after or not left or len(left) != len(right):
        _reject()
    changes = []
    for old, new in zip(left, right, strict=True):
        amount, prior = new.pop("amount_cents"), old.pop("amount_cents")
        if new != old or type(amount) is not int or not 0 < amount <= prior:
            _reject()
        if amount < prior:
            changes.append(amount)
    if not changes:
        _reject()
    return tuple(changes)


def _label(amounts: tuple[int, ...]) -> str:
    return "最多 " + "、".join(f"{amount // 100:,}.{amount % 100:02d} 元" for amount in amounts)


async def _parent(
    conn: AsyncConnection, session: Mapping[str, Any], plan_id: str, now: datetime
) -> dict[str, Any]:
    if not plan_id or not _aware(now) or session["revoked_at_wall"] is not None:
        _reject()
    plan = (
        (await conn.execute(select(t.plans).where(t.plans.c.id == plan_id)))
        .mappings()
        .one_or_none()
    )
    if plan is None or (
        plan["id"] != plan_id
        or plan["sid"] != session["id"]
        or plan["customer_id"] != session["customer_id"]
        or plan["origin"] != "agent"
        or plan["status"] != "REPLAN"
    ):
        _reject()
    try:
        draft = PlanDraft.model_validate(plan["plan_draft"])
        ids = tuple(plan["utterance_ids"])
        if (
            not ids
            or len(set(ids)) != len(ids)
            or set(ids) != set(plan["scope_ids"])
            or set(ids) != set(draft.utterance_ids)
            or draft.session_id != session["id"]
        ):
            _reject()
        rows = (
            (
                await conn.execute(
                    select(t.utterances).where(
                        t.utterances.c.id.in_(ids), t.utterances.c.sid == session["id"]
                    )
                )
            )
            .mappings()
            .all()
        )
        if len(rows) != len(ids) or {row["id"] for row in rows} != set(ids):
            _reject()
        refs = {}
        for row in rows:
            if (
                row["sid"] != session["id"]
                or row["status"] != "ACTIVE"
                or row["parse_status"] != "SCOPED"
                or row["echoes_untrusted"]
                or row["authorization_epoch"] != session["authorization_epoch"]
                or not _aware(row["created_at_wall"])
                or row["created_at_wall"] > now
                or not _aware(row["expires_at_wall"])
                or row["expires_at_wall"] <= now
            ):
                _reject()
            refs[row["id"]] = as_ref(row)
        auth = AuthorizationInputs(
            sid=session["id"],
            customer_id=session["customer_id"],
            utterance_ids=ids,
            scope_ids=ids,
            authorization_epoch=session["authorization_epoch"],
            utterances=refs,
            statuses={id_: UtteranceStatus.ACTIVE for id_ in ids},
        )
        # This is a saved-source proof for an already authenticated session,
        # not a fabricated token/caller or a new authorization decision.
        proof_ctx = EvalContext(
            conn=conn,
            authorization=auth,
            session_id=session["id"],
            customer_id=session["customer_id"],
            plan_draft=draft,
        )
        proof = await _adopted_sources(proof_ctx, ids, now)
        if not set(ids).issubset(proof.utterances) or len(ids) != 1:
            _reject()
        evaluation = (
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
        if (
            evaluation is None
            or evaluation["plan_id"] != plan_id
            or evaluation["decision"].get("decision") != "REPLAN"
        ):
            _reject()
        return {
            "plan": dict(plan),
            "evaluation": dict(evaluation),
            "auth": auth,
            "roots": proof.utterances,
            "sources": proof.sources,
            "scope": refs[ids[0]].scope,
        }
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise GatewayError(Code.AUTHORIZATION_CANCELLED, 409) from exc


def _commitment(session: Mapping[str, Any], parent: dict[str, Any], row: Mapping[str, Any]) -> str:
    plan, evaluation = parent["plan"], parent["evaluation"]
    return digest(
        _json(
            {
                "kind": "server-hint-v1",
                "customer_id": session["customer_id"],
                "sid": session["id"],
                "epoch": session["authorization_epoch"],
                "plan": {
                    key: plan.get(key)
                    for key in (
                        "id",
                        "origin",
                        "plan_hash",
                        "plan_draft",
                        "steps_hash",
                        "request_hash",
                        "utterance_ids",
                        "scope_ids",
                    )
                },
                "evaluation": {
                    key: evaluation.get(key)
                    for key in (
                        "id",
                        "revision",
                        "decision",
                        "findings",
                        "preflight",
                        "created_at_wall",
                        "risk",
                        "policy_version",
                        "versions",
                        "clock_version",
                    )
                },
                "roots": parent["roots"],
                "sources": parent["sources"],
                "hint": {
                    key: row[key]
                    for key in (
                        "id",
                        "sid",
                        "parent_plan_id",
                        "scope_json",
                        "label",
                        "created_at_wall",
                        "expires_at_wall",
                    )
                },
            }
        )
    )


async def publish_replan_hints(
    conn: AsyncConnection,
    ctx: EvalContext,
    session: Mapping[str, Any],
    *,
    evaluation_id: str,
    revision: int,
    now: datetime,
) -> tuple[ReplanHint, ...]:
    decision, caller, auth = ctx.decision, ctx.identity, ctx.authorization
    if (
        decision is None
        or decision.decision is not DecisionValue.REPLAN
        or not decision.replan_hints
        or ctx.origin != "agent"
    ):
        return ()
    if (
        caller is None
        or caller.kind is not ServiceKind.A
        or "assistant" not in caller.scopes
        or caller.customer_id != session["customer_id"]
        or caller.sid != session["id"]
        or auth is None
        or auth.authorization_epoch != session["authorization_epoch"]
        or not set(auth.utterance_ids).issubset(caller.utts)
    ):
        _reject()
    parent = await _parent(conn, session, ctx.plan_id, now)
    if (
        parent["evaluation"]["id"] != evaluation_id
        or parent["evaluation"]["revision"] != revision
        or parent["auth"] != auth
    ):
        _reject()
    published = []
    expiry_cap = min(
        datetime.fromisoformat(ref.expires_at_wall) for ref in auth.utterances.values()
    )
    for proposal in decision.replan_hints:
        amounts = _narrowed(parent["scope"], proposal.scope)
        try:
            expiry = datetime.fromisoformat(proposal.expires_at_wall)
        except ValueError as exc:
            raise GatewayError(Code.AUTHORIZATION_CANCELLED, 409) from exc
        if not _aware(expiry):
            _reject()
        expiry = min(expiry, expiry_cap, now + timedelta(minutes=10)).astimezone(UTC)
        if expiry <= now:
            _reject()
        row = dict(
            id=proposal.id,
            sid=session["id"],
            scope_json=proposal.scope.model_dump(mode="json"),
            label=_label(amounts),
            parent_plan_id=ctx.plan_id,
            created_at_wall=now,
            expires_at_wall=expiry,
            used_by=None,
        )
        row["view_hash"] = _commitment(session, parent, row)
        await conn.execute(t.hints.insert().values(**row))
        published.append(
            proposal.model_copy(
                update={"label": row["label"], "expires_at_wall": expiry.isoformat()}
            )
        )
    return tuple(published)


async def validate_hint_source(
    conn: AsyncConnection, session: Mapping[str, Any], row: Mapping[str, Any], now: datetime
) -> None:
    """Used by G22a and, under its session/source locks, G3 before adoption."""
    if row["sid"] != session["id"] or row["used_by"] is not None:
        _reject()
    if (
        not _aware(now)
        or not _aware(row["created_at_wall"])
        or row["created_at_wall"] > now
        or not _aware(row["expires_at_wall"])
        or row["expires_at_wall"] <= now
        or row["expires_at_wall"] > row["created_at_wall"] + timedelta(minutes=10)
    ):
        _reject()
    parent = await _parent(conn, session, row["parent_plan_id"], now)
    try:
        scope = NormalizedScope.model_validate(row["scope_json"])
    except ValueError as exc:
        raise GatewayError(Code.AUTHORIZATION_CANCELLED, 409) from exc
    if row["label"] != _label(_narrowed(parent["scope"], scope)):
        _reject()
    if any(
        row["expires_at_wall"] > datetime.fromisoformat(ref.expires_at_wall)
        for ref in parent["auth"].utterances.values()
    ):
        _reject()
    expected = _commitment(session, parent, dict(row))
    if (
        not isinstance(row["view_hash"], str)
        or len(row["view_hash"]) != 64
        or any(char not in "0123456789abcdef" for char in row["view_hash"])
        or not compare_digest(row["view_hash"], expected)
    ):
        _reject()
