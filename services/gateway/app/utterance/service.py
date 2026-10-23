"""Prepare outside SQL transactions; register in one idempotent transaction."""

from __future__ import annotations

import secrets
import time
from datetime import timedelta
from typing import Any

from pydantic import ValidationError
from safebank_contracts.codes import Code
from safebank_contracts.state import BankState
from safebank_contracts.tools import WRITE_ARGS
from safebank_contracts.views import READ_RESULTS
from sqlalchemy import select, update

from .. import state
from .. import tables as t
from ..errors import GatewayError
from ..l3.client import L3Error
from ..policy.registry import action_kind, policy
from ..schemas.enums import ParseStatus
from ..schemas.scope import NormalizedScope, ScopeTransfer
from .lifecycle import cancel_clarifications, cancel_session, supersede
from .loader import as_ref
from .parser import LEXICON, Parsed, contains, echoes_untrusted, normalize, parse


def new_id(prefix: str) -> str:
    # ULID shape is shared with replay canonicalization (§9.1 / §6.6).
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    value = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    encoded = ""
    for _ in range(26):
        encoded = alphabet[value & 31] + encoded
        value >>= 5
    return f"{prefix}_{encoded}"


async def snapshot(l3, customer_id: str) -> BankState:
    try:
        return BankState.model_validate(
            await l3.snapshot({"customer_id": customer_id, "steps": [], "event_id": None})
        )
    except (L3Error, ValidationError) as exc:
        raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503) from exc


async def predicate_values(
    l3,
    bank: BankState,
    customer_id: str,
    text: str,
    evidence: list[dict[str, Any]] | None = None,
) -> dict[str, list[str]]:
    """Use authoritative reads for unfinished L3 features; never invent results."""
    values = {}
    if contains(text, LEXICON["predicates"]["robust_products"]):
        customer = bank.customers.get(customer_id)
        if (
            customer is None
            or customer.risk_level is None
            or customer.risk_valid_until is None
            or customer.risk_valid_until < bank.sim_now.date().isoformat()
        ):
            raise GatewayError(Code.ASSESSMENT_MISSING, 409)
        values["robust_products"] = [
            p.id
            for p in bank.products
            if p.risk_label in ("稳健", "平衡") and p.risk_level <= customer.risk_level
        ]
    queries = {}
    if contains(text, LEXICON["predicates"]["idle_subscriptions"]):
        queries["idle_subscriptions"] = ("detect_subscriptions", {"lookback_days": 90})
    if contains(text, LEXICON["predicates"]["suspicious_cards"]):
        month_start = bank.sim_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        previous_start = (month_start - timedelta(days=1)).replace(day=1)
        queries["suspicious_cards"] = (
            "detect_anomalies",
            {
                "from_at": previous_start.isoformat(),
                "to_at": bank.sim_now.isoformat(),
                "account_id": None,
            },
        )
    for key, (tool, args) in queries.items():
        try:
            result = READ_RESULTS[tool].model_validate(
                await l3.read(tool, {"customer_id": customer_id, "args": args})
            )
        except (L3Error, ValidationError) as exc:
            raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503) from exc
        if evidence is not None:
            evidence.append({"tool": tool, "args": args, "data": result.model_dump(mode="json")})
        if key == "idle_subscriptions":
            values[key] = sorted(
                {
                    item.instruction_id
                    for item in result.items
                    if item.is_idle and item.instruction_id
                }
            )
        else:
            values[key] = sorted(
                {item.transaction.card_id for item in result.items if item.transaction.card_id}
            )
    if queries:
        after = await snapshot(l3, customer_id)
        if dict(after.versions) != dict(bank.versions) or after.clock_version != bank.clock_version:
            raise GatewayError(Code.STATE_CHANGED, 409)
    return values


def form_tool(kind: str, fields: dict[str, Any], bank: BankState) -> tuple[str, dict[str, Any]]:
    """Accept the exact frozen W-tool args and registry-computed action kind."""
    for tool, model in WRITE_ARGS.items():
        try:
            args = model.model_validate(fields).model_dump(mode="json")
        except ValidationError:
            continue
        try:
            computed = action_kind(tool, args, bank).value
        except LookupError as exc:
            raise GatewayError(Code.NOT_FOUND, 404) from exc
        if computed == kind:
            if isinstance(args.get("payee_id"), dict):
                raise GatewayError(Code.CONTRACT_INVALID, 422)
            if "app_form" not in policy(tool).allowed_origins:
                raise GatewayError(Code.ORIGIN_NOT_ALLOWED, 403)
            return tool, args
    raise GatewayError(Code.CONTRACT_INVALID, 422)


def scope_from_form(kind: str, args: dict[str, Any], bank: BankState) -> NormalizedScope:
    transfers = ()
    if kind in ("TRANSFER_EXTERNAL", "TRANSFER_SELF", "SCHEDULE_CREATE"):
        if args.get("payee_id"):
            payee = bank.payees.get(args["payee_id"])
            if payee is None:
                raise GatewayError(Code.NOT_FOUND, 404)
            fingerprint = payee.target_fingerprint
        else:
            fingerprint = "int:" + args["to_account_id"]
        transfers = (
            ScopeTransfer(
                target_fingerprint=fingerprint,
                amount_cents=args["amount_cents"],
                from_account_id=args["from_account_id"],
            ),
        )
    return NormalizedScope(
        action_kinds=(kind,),
        from_account_id=args.get("from_account_id"),
        transfers=transfers,
        period=args.get("period"),
        fields=args,
    )


async def current_clarification(conn, sid: str, now):
    return (
        (
            await conn.execute(
                select(t.pending_clarifications).where(
                    t.pending_clarifications.c.sid == sid,
                    t.pending_clarifications.c.status == "OPEN",
                    t.pending_clarifications.c.expires_at_wall > now,
                )
            )
        )
        .mappings()
        .one_or_none()
    )


def clarification_fields(clarification, provided: list[dict[str, str]]) -> dict[str, str]:
    chosen = {}
    allowed = {(c["field"], c["entity_id"]) for c in clarification["candidate_refs"]}
    for item in provided:
        if (
            set(item) != {"field", "entity_id"}
            or item["field"] in chosen
            or (item["field"], item["entity_id"]) not in allowed
        ):
            raise GatewayError(Code.INTENT_SCOPE_EXCEEDED, 409)
        chosen[item["field"]] = item["entity_id"]
    if set(chosen) != set(clarification["required_fields"]):
        raise GatewayError(Code.INTENT_SCOPE_EXCEEDED, 409)
    return chosen


def typed_fields(clarification, text: str) -> dict[str, str]:
    """Whole normalized answer must exactly equal one candidate label/id."""
    text = normalize(text)
    matches = [
        c
        for c in clarification["candidate_refs"]
        if text in (normalize(c["label"]), c["entity_id"])
    ]
    if len(matches) != 1 or clarification["required_fields"] != [matches[0]["field"]]:
        raise GatewayError(Code.INTENT_SCOPE_EXCEEDED, 409)
    return {matches[0]["field"]: matches[0]["entity_id"]}


def merge_scope(clarification, chosen: dict[str, str], bank: BankState) -> NormalizedScope:
    scope = NormalizedScope.model_validate(clarification["partial_scope"])
    fields = dict(scope.fields)
    fields.update(chosen)
    transfers = scope.transfers
    source = chosen.get("from_account_id", scope.from_account_id)
    kinds = list(scope.action_kinds)
    if "payee_id" in chosen:
        payee = bank.payees.get(chosen["payee_id"])
        if payee is None:
            raise GatewayError(Code.NOT_FOUND, 404)
        transfers = (
            ScopeTransfer(
                target_fingerprint=payee.target_fingerprint,
                amount_cents=fields["amount_cents"],
                from_account_id=source,
            ),
        )
    if "from_account_id" in chosen:
        transfers = tuple(
            value.model_copy(update={"from_account_id": source}) for value in transfers
        )
    tool = fields.pop("pending_tool", None)
    if tool:
        args = {"card_id": fields["card_id"]}
        names = (
            ("single_limit_cents", "daily_limit_cents")
            if tool == "update_card_limits"
            else ("online", "overseas", "atm", "quickpass")
        )
        args.update({name: fields.get(name) for name in names})
        kinds.append(action_kind(tool, args, bank).value)
    return scope.model_copy(
        update={
            "action_kinds": tuple(kinds),
            "fields": fields,
            "transfers": transfers,
            "from_account_id": source,
        }
    )


async def untrusted_free_text(conn, sid: str) -> tuple[str, ...]:
    rows = (
        (await conn.execute(select(t.read_records).where(t.read_records.c.sid == sid)))
        .mappings()
        .all()
    )
    found = []
    for row in rows:
        response = row["response"]
        for pointer in row["untrusted_paths"]:
            if pointer.rsplit("/", 1)[-1] not in (
                "memo",
                "counterparty",
                "merchant_name",
                "risk_disclosure",
                "target_display",
            ):
                continue
            node = response.get("data", response)
            try:
                for token in pointer.lstrip("/").split("/"):
                    token = token.replace("~1", "/").replace("~0", "~")
                    node = node[int(token)] if isinstance(node, list) else node[token]
            except (KeyError, IndexError, ValueError, TypeError):
                continue
            if isinstance(node, str):
                found.append(node)
    return tuple(found)


async def register(
    conn,
    sid: str,
    session,
    body,
    bank: BankState | None,
    now,
    predicates: dict[str, list[str]] | None = None,
    evidence: list[dict[str, Any]] | None = None,
):
    expiry = now + timedelta(minutes=10)
    parent_ids = ()
    source_id = None
    clarification = await current_clarification(conn, sid, now)
    parsed = Parsed()
    echo = False
    source_ref = new_id("src")
    if bank is not None:
        data = bank.model_dump(mode="json")
        await conn.execute(
            t.read_records.insert().values(
                id=source_ref,
                sid=sid,
                tool="_utterance_snapshot",
                canonical_args={},
                response={"data": data},
                untrusted_paths=untrusted_paths(data),
                versions=dict(bank.versions),
                observed_at=bank.sim_now,
                created_at_wall=now,
            )
        )
    for record in evidence or []:
        id_ = new_id("src")
        await conn.execute(
            t.read_records.insert().values(
                id=id_,
                sid=sid,
                tool=record["tool"],
                canonical_args=record["args"],
                response={
                    "data": record["data"],
                    "source_ref": id_,
                    "untrusted_paths": untrusted_paths(record["data"]),
                    "versions": dict(bank.versions),
                    "observed_at": bank.sim_now.isoformat(),
                },
                untrusted_paths=untrusted_paths(record["data"]),
                versions=dict(bank.versions),
                observed_at=bank.sim_now,
                created_at_wall=now,
            )
        )
    if body.kind == "typed":
        if body.clarification_id:
            if clarification is None or clarification["id"] != body.clarification_id:
                raise GatewayError(Code.AUTHORIZATION_CANCELLED, 409)
            parsed = Parsed(
                scope=merge_scope(clarification, typed_fields(clarification, body.text), bank),
                status=ParseStatus.SCOPED,
            )
            parent_ids = tuple(clarification["parent_utterance_ids"])
            expiry = min(expiry, clarification["expires_at_wall"])
            await state.transition(
                conn,
                t.pending_clarifications,
                id_=clarification["id"],
                from_statuses=["OPEN"],
                to="RESOLVED",
            )
        else:
            await cancel_clarifications(conn, sid)
            parsed = parse(body.text, bank, session["customer_id"], predicates)
        if bank is not None:
            echo = echoes_untrusted(
                body.text, bank, session["customer_id"], await untrusted_free_text(conn, sid)
            )
    else:
        table = t.hints if body.kind == "hint" else t.form_actions
        source_id = body.hint_id if body.kind == "hint" else body.action_id
        row = (
            (
                await conn.execute(
                    select(table)
                    .where(table.c.id == source_id, table.c.sid == sid)
                    .with_for_update()
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
        expiry = min(expiry, row["expires_at_wall"])
        if body.kind == "hint":
            parsed = Parsed(
                scope=NormalizedScope.model_validate(row["scope_json"]), status=ParseStatus.SCOPED
            )
            await cancel_clarifications(conn, sid)
        elif row["kind"] == "FORM":
            parsed = Parsed(
                scope=scope_from_form(row["action_kind"], row["normalized_fields"], bank),
                status=ParseStatus.SCOPED,
            )
            await cancel_clarifications(conn, sid)
        else:
            if (
                clarification is None
                or row["parent_utterance_id"] not in clarification["parent_utterance_ids"]
            ):
                raise GatewayError(Code.AUTHORIZATION_CANCELLED, 409)
            parsed = Parsed(
                scope=merge_scope(
                    clarification,
                    clarification_fields(clarification, row["normalized_fields"]),
                    bank,
                ),
                status=ParseStatus.SCOPED,
            )
            parent_ids = tuple(clarification["parent_utterance_ids"])
            expiry = min(expiry, clarification["expires_at_wall"])
            await state.transition(
                conn,
                t.pending_clarifications,
                id_=clarification["id"],
                from_statuses=["OPEN"],
                to="RESOLVED",
            )
    if parsed.cancel:
        await cancel_session(conn, sid, now)
        epoch = session["authorization_epoch"] + 1
    else:
        epoch = session["authorization_epoch"]
    if parsed.scope.action_kinds or parsed.required_fields:
        await supersede(conn, sid, keep=parent_ids)
    id_ = new_id("utt")
    clr_id = None
    if parsed.status in (ParseStatus.CLARIFYING, ParseStatus.INTENT_UNCLEAR):
        clr_id = new_id("clr")
        candidates = [{**candidate, "source_ref": source_ref} for candidate in parsed.candidates]
        await conn.execute(
            t.pending_clarifications.insert().values(
                id=clr_id,
                sid=sid,
                kind="SLOTS" if parsed.required_fields and parsed.candidates else "FORM_REQUIRED",
                prompt_template_id="select_missing_slot"
                if parsed.required_fields and parsed.candidates
                else "open_structured_form",
                parent_utterance_ids=[id_],
                authorization_epoch=epoch,
                required_fields=parsed.required_fields,
                candidate_refs=candidates,
                partial_scope=parsed.scope.model_dump(mode="json"),
                status="OPEN",
                expires_at_wall=expiry,
            )
        )
    row = dict(
        id=id_,
        sid=sid,
        kind=body.kind,
        text=body.text,
        normalized_scope=parsed.scope.model_dump(mode="json"),
        echoes_untrusted=echo,
        source_id=source_id,
        parent_ids=list(parent_ids),
        status="ACTIVE",
        parse_status=parsed.status.value,
        clarification_id=clr_id,
        created_at_wall=now,
        expires_at_wall=expiry,
        authorization_epoch=epoch,
    )
    await conn.execute(t.utterances.insert().values(**row))
    if body.kind != "typed":
        await conn.execute(update(table).where(table.c.id == source_id).values(used_by=id_))
    return as_ref(row), expiry


def untrusted_paths(data: Any, prefix: str = "") -> list[str]:
    """Mark free text in snapshot evidence with RFC6901 paths relative to data."""
    result = []
    if isinstance(data, dict):
        for key, value in data.items():
            pointer = prefix + "/" + key.replace("~", "~0").replace("/", "~1")
            if key in {
                "name",
                "alias",
                "display_name",
                "bank_name",
                "merchant_name",
                "memo",
                "counterparty",
                "risk_disclosure",
                "target_display",
            } and isinstance(value, str):
                result.append(pointer)
            result.extend(untrusted_paths(value, pointer))
    elif isinstance(data, list):
        for index, value in enumerate(data):
            result.extend(untrusted_paths(value, prefix + "/" + str(index)))
    return result
