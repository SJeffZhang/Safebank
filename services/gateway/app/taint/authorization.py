"""Read-only proof of the link from a claimed authorization to saved sources.

A claims ACTIVE utterance IDs. Hint/action IDs are evidence reached through the
persisted parent graph, never alternative claim IDs. F direct FORM views are a
separate, credential-bound adapter; they do not create fake utterance rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from ..schemas.enums import ParseStatus, ServiceKind, UtteranceStatus
from ..tables import form_actions, hints, sessions, utterances

if TYPE_CHECKING:
    from ..pipeline.context import EvalContext

MAX_LINEAGE_NODES = 64
MAX_LINEAGE_DEPTH = 32


@dataclass
class AuthorizationEvidence:
    utterances: dict[str, dict[str, Any]] = field(default_factory=dict)
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)


def _live(row: dict[str, Any], now: datetime) -> bool:
    return (
        row["created_at_wall"] <= now
        and row["expires_at_wall"] is not None
        and row["expires_at_wall"] > now
    )


def _times_match(row: dict[str, Any], ref: Any) -> bool:
    try:
        return (
            datetime.fromisoformat(ref.created_at_wall) == row["created_at_wall"]
            and datetime.fromisoformat(ref.expires_at_wall) == row["expires_at_wall"]
        )
    except (TypeError, ValueError, AttributeError):
        return False


def _ref_matches(row: dict[str, Any], ref: Any) -> bool:
    try:
        return (
            ref.id == row["id"]
            and ref.kind == row["kind"]
            and ref.text == row["text"]
            and ref.scope.model_dump(mode="json") == row["normalized_scope"]
            and ref.parent_ids == tuple(row["parent_ids"])
            and ref.parse_status.value == row["parse_status"]
            and ref.echoes_untrusted == row["echoes_untrusted"]
            and _times_match(row, ref)
        )
    except (TypeError, ValueError, AttributeError):
        return False


def _action_fields(row: dict[str, Any], node: dict[str, Any]) -> dict[str, Any] | None:
    fields = row["normalized_fields"]
    if row["kind"] == "FORM":
        return (
            fields
            if (
                isinstance(fields, dict)
                and fields == node["normalized_scope"].get("fields")
                and row["action_kind"] in node["normalized_scope"]["action_kinds"]
            )
            else None
        )
    if (
        row["kind"] != "CLARIFICATION"
        or not isinstance(fields, list)
        or not fields
        or row["parent_utterance_id"] not in node["parent_ids"]
    ):
        return None
    selected = {}
    for item in fields:
        if (
            not isinstance(item, dict)
            or set(item) != {"field", "entity_id"}
            or not isinstance(item["field"], str)
            or not isinstance(item["entity_id"], str)
            or not item["field"]
            or not item["entity_id"]
            or item["field"] in selected
        ):
            return None
        selected[item["field"]] = item["entity_id"]
    if any(node["normalized_scope"].get("fields", {}).get(k) != v for k, v in selected.items()):
        return None
    return selected


async def authorization_evidence(ctx: EvalContext) -> AuthorizationEvidence:
    empty = AuthorizationEvidence()
    auth, caller = ctx.authorization, ctx.identity
    if auth is None or caller is None or ctx.conn is None or ctx.plan_draft is None:
        return empty
    ids = tuple(auth.utterance_ids)
    if (
        not ids
        or len(set(ids)) != len(ids)
        or set(ids) != set(auth.scope_ids)
        or len(set(auth.scope_ids)) != len(auth.scope_ids)
        or set(ids) != set(ctx.plan_draft.utterance_ids)
        or auth.sid != ctx.session_id
        or auth.customer_id != ctx.customer_id
        or caller.customer_id != ctx.customer_id
        or caller.origin != ctx.origin
        or any(
            id_ not in auth.utterances
            or auth.statuses.get(id_) is not UtteranceStatus.ACTIVE
            or auth.utterances[id_].parse_status is not ParseStatus.SCOPED
            for id_ in ids
        )
    ):
        return empty
    if caller.kind is ServiceKind.A:
        if (
            caller.sid != ctx.session_id
            or not set(ids).issubset(caller.utts)
            or "assistant" not in caller.scopes
        ):
            return empty
    elif caller.kind is ServiceKind.F:
        if ctx.origin != "app_form" or "app" not in caller.scopes or ctx.plan_id is None:
            return empty
    else:
        return empty
    now = ctx.wall_now()
    session = (
        (
            await ctx.conn.execute(
                select(sessions).where(
                    sessions.c.id == ctx.session_id,
                    sessions.c.customer_id == ctx.customer_id,
                    sessions.c.revoked_at_wall.is_(None),
                    sessions.c.authorization_epoch == auth.authorization_epoch,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if session is None:
        return empty
    if caller.kind is ServiceKind.F:
        return await _direct_forms(ctx, ids, now)
    return await _adopted_sources(ctx, ids, now)


async def _direct_forms(
    ctx: EvalContext, ids: tuple[str, ...], now: datetime
) -> AuthorizationEvidence:
    rows = (
        (
            await ctx.conn.execute(
                select(form_actions).where(
                    form_actions.c.sid == ctx.session_id,
                    form_actions.c.id.in_(ids),
                    form_actions.c.kind == "FORM",
                )
            )
        )
        .mappings()
        .all()
    )
    if len(rows) != len(ids):
        return AuthorizationEvidence()
    result = {}
    for raw in rows:
        row = dict(raw)
        ref = ctx.authorization.utterances[row["id"]]
        if (
            not _live(row, now)
            or row["used_by"] not in (None, ctx.plan_id)
            or ref.id != row["id"]
            or ref.kind != "action"
            or ref.parent_ids
            or not _times_match(row, ref)
            or ref.scope.fields != row["normalized_fields"]
            or ref.scope.action_kinds != (row["action_kind"],)
        ):
            return AuthorizationEvidence()
        result[row["id"]] = row | {"type": "action"}
    return AuthorizationEvidence(sources=result)


async def _adopted_sources(
    ctx: EvalContext, ids: tuple[str, ...], now: datetime
) -> AuthorizationEvidence:
    nodes: dict[str, dict[str, Any]] = {}
    visiting: set[str] = set()
    completed: set[str] = set()

    async def visit(id_: str, depth: int) -> bool:
        if id_ in visiting or depth >= MAX_LINEAGE_DEPTH:
            return False
        if id_ in completed:
            return True
        if len(nodes) >= MAX_LINEAGE_NODES:
            return False
        raw = (
            (
                await ctx.conn.execute(
                    select(utterances).where(
                        utterances.c.id == id_,
                        utterances.c.sid == ctx.session_id,
                        utterances.c.authorization_epoch == ctx.authorization.authorization_epoch,
                        utterances.c.status == "ACTIVE",
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if raw is None:
            return False
        row = dict(raw)
        if (
            not _live(row, now)
            or row["kind"] not in ("typed", "hint", "action")
            or not isinstance(row["parent_ids"], list)
            or any(not isinstance(p, str) or not p for p in row["parent_ids"])
            or len(set(row["parent_ids"])) != len(row["parent_ids"])
        ):
            return False
        if id_ in ids and not _ref_matches(row, ctx.authorization.utterances[id_]):
            return False
        nodes[id_] = row
        visiting.add(id_)
        for parent in row["parent_ids"]:
            if (
                not await visit(parent, depth + 1)
                or row["expires_at_wall"] > nodes[parent]["expires_at_wall"]
                or row["created_at_wall"] < nodes[parent]["created_at_wall"]
            ):
                return False
        visiting.remove(id_)
        completed.add(id_)
        return True

    for id_ in ids:
        if not await visit(id_, 0):
            return AuthorizationEvidence()
    result = {}
    for node in nodes.values():
        kind, source_id = node["kind"], node["source_id"]
        if kind == "typed":
            if source_id is not None:
                return AuthorizationEvidence()
            continue
        if not isinstance(source_id, str) or not source_id:
            return AuthorizationEvidence()
        table = hints if kind == "hint" else form_actions
        raw = (
            (
                await ctx.conn.execute(
                    select(table).where(
                        table.c.id == source_id,
                        table.c.sid == ctx.session_id,
                        table.c.used_by == node["id"],
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if raw is None:
            return AuthorizationEvidence()
        source = dict(raw)
        if (
            not _live(source, now)
            or source["created_at_wall"] > node["created_at_wall"]
            or node["expires_at_wall"] > source["expires_at_wall"]
        ):
            return AuthorizationEvidence()
        if kind == "hint":
            if source["scope_json"] != node["normalized_scope"]:
                return AuthorizationEvidence()
        else:
            fields = _action_fields(source, node)
            if fields is None:
                return AuthorizationEvidence()
            source["normalized_fields"] = fields
        result[source_id] = source | {"type": kind}
    return AuthorizationEvidence(utterances=nodes, sources=result)
