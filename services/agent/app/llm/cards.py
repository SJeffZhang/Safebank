"""C1e deterministic UICard generator (PRD §6.7.3, §9.5, C1e Task 5).

``generate_cards(state)`` produces a list of ``UICard`` objects from
``AgentState`` — no LLM, no randomness.  Cards are pushed via SSE ``custom``
events and never enter AgentState.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from safebank_contracts.base import Contract

from services.agent.app.graph.state import AgentState
from services.agent.app.llm._cross_scene_reads import x1_analysis_records
from services.agent.app.llm._query_reads import query
from services.agent.app.llm.redact import wrap_untrusted

# ── CardAction / UICard L1-local models ─────────────────────────────────────

_CardActionKind = Literal["HINT", "CLARIFY", "NAVIGATE", "OPEN_APPROVAL"]

_CardType = Literal[
    "ACCOUNT",
    "TRANSACTIONS",
    "ANOMALIES",
    "CARD",
    "SUBSCRIPTIONS",
    "PRODUCTS",
    "HOLDINGS",
    "PLAN_STATUS",
    "BLOCKED",
    "REPLAN_HINTS",
    "CLARIFICATION",
    "NAVIGATE",
]

# Fields omitted from generic control data; read cards allow selected display strings.
_UNTRUSTED_FIELDS: frozenset[str] = frozenset(
    {
        "name",
        "merchant_name",
        "memo",
        "alias",
        "description",
        "note",
        "address",
        "phone",
        "email",
        "id_number",
        "passport",
    }
)

# Read-tool → card-type mapping (keys are real tools in READ_ARGS, PRD §9.3)
_READ_TOOL_CARD_TYPE: dict[str, _CardType] = {
    "get_accounts": "ACCOUNT",
    "get_transactions": "TRANSACTIONS",
    "get_spending_summary": "TRANSACTIONS",
    "get_cards": "CARD",
    "list_subscriptions": "SUBSCRIPTIONS",
    "upcoming_charges": "SUBSCRIPTIONS",
    "detect_subscriptions": "SUBSCRIPTIONS",
    "list_products": "PRODUCTS",
    "compare_products": "PRODUCTS",
    "get_product": "PRODUCTS",
    "get_holdings": "HOLDINGS",
    "detect_anomalies": "ANOMALIES",
}

# Read tools with no suitable card type (payees / scheduled / split / risk profile).
# They are deliberately skipped — their data reaches the user through the
# assistant reply, not a card.  Kept as an explicit set so the mapping guard can
# prove every real read tool is either mapped or intentionally skipped.
_SKIPPED_READ_TOOLS: frozenset[str] = frozenset(
    {
        "list_payees",
        "list_scheduled",
        "get_split_status",
        "get_risk_questionnaire",
        "get_risk_profile",
        "get_profile",
    }
)


class CardAction(Contract):
    """Deterministic card action — no visible text (frontend fetches labels from L2)."""

    kind: _CardActionKind
    ref: str  # hnt_*, entity_id, page route, apv_*


class UICard(Contract):
    """Deterministic UI card for SSE push."""

    id: str
    type: _CardType
    source_ref: str | None = None
    data: dict[str, Any] = {}
    actions: tuple[CardAction, ...] = ()


# ── ID counter (simple monotonic, per-call) ────────────────────────────────

_ID_PREFIX = "card_"


def _next_id(seq: list[object]) -> str:
    return _ID_PREFIX + str(len(seq) + 1)


# ── Safe data extraction ───────────────────────────────────────────────────


def _safe_data(raw: object) -> dict[str, Any]:
    """Extract a safe subset of *raw*, stripping untrusted fields."""
    if isinstance(raw, dict):
        return _filter_untrusted(raw)
    if hasattr(raw, "model_dump"):
        return _filter_untrusted(raw.model_dump(mode="json"))
    return {}


def _filter_untrusted(data: dict[str, Any]) -> dict[str, Any]:
    """Recursively remove untrusted field keys from *data*."""
    clean: dict[str, Any] = {}
    for k, v in data.items():
        if k in _UNTRUSTED_FIELDS:
            continue
        if isinstance(v, dict):
            clean[k] = _filter_untrusted(v)
        elif isinstance(v, list):
            clean[k] = [_filter_untrusted(item) if isinstance(item, dict) else item for item in v]
        else:
            clean[k] = v
    return clean


# Read cards render names as plain text, while prompt records retain their taint.
_READ_DISPLAY_FIELDS = frozenset(
    {"name", "merchant_name", "display_name", "counterparty", "target_display"}
)


def _read_card_data(record: object) -> dict[str, Any]:
    """Copy a read for display; unwrap only exact source-bound marked values."""
    raw = _attr_or_get(record, "data", {})
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump(mode="json")
    if not isinstance(raw, dict):
        return {}
    paths = _attr_or_get(record, "untrusted_paths", ()) or ()
    source = _attr_or_get(record, "source_ref", "")
    closing = "</untrusted_data>"
    opening = wrap_untrusted("", source).removesuffix(closing)

    def project(value: Any, pointer: str) -> Any:
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if key in _UNTRUSTED_FIELDS and key not in _READ_DISPLAY_FIELDS:
                    continue
                if key in _READ_DISPLAY_FIELDS and item is not None and not isinstance(item, str):
                    continue
                token = key.replace("~", "~0").replace("/", "~1")
                result[key] = project(item, pointer + "/" + token)
            return result
        if isinstance(value, list):
            return [project(item, pointer + "/" + str(index)) for index, item in enumerate(value)]
        if (
            isinstance(value, str)
            and pointer in paths
            and value.startswith(opening)
            and value.endswith(closing)
        ):
            return value[len(opening) : -len(closing)]
        return value

    return project(raw, "")


def _card_reads(state: AgentState) -> list[object]:
    """Hide lookup pages only after the precise requested result is complete."""
    reads = list(state.get("reads") or [])
    selected = query(state)
    if selected is None:
        return reads
    kind, values = selected
    if kind == "compare":
        catalogs = [r for r in reads if _attr_or_get(r, "tool") == "list_products"]
        comparisons = [r for r in reads if _attr_or_get(r, "tool") == "compare_products"]
        if not catalogs or not comparisons or _read_card_data(catalogs[-1]).get("next_cursor"):
            return reads
        compared = comparisons[-1]
        rows = [item for r in catalogs for item in _read_card_data(r).get("items", [])]
        ids = [{item.get("id") for item in rows if item.get("name") == name} for name in values]
        if any(len(matches) != 1 or None in matches for matches in ids):
            return reads
        expected = set.union(*ids)
        result = _read_card_data(compared)
        args = _attr_or_get(compared, "args", {})
        items = result.get("items", [])
        if (
            len(expected) != 2
            or set(args.get("product_ids", [])) != expected
            or len(items) != 2
            or {item.get("id") for item in items} != expected
            or result.get("next_cursor")
        ):
            return reads
        return [
            r
            for r in reads
            if _attr_or_get(r, "tool") != "list_products"
            and (_attr_or_get(r, "tool") != "compare_products" or r is compared)
        ]
    if kind != "merchant":
        return reads
    transactions = [r for r in reads if _attr_or_get(r, "tool") == "get_transactions"]
    scans = [r for r in transactions if not _attr_or_get(r, "args", {}).get("merchant_id")]
    if not scans or _read_card_data(scans[-1]).get("next_cursor"):
        return reads
    ids = {
        item.get("merchant_id")
        for r in scans
        for item in _read_card_data(r).get("items", [])
        if (item.get("merchant_name") or item.get("counterparty")) == values[0]
    }
    if len(ids) != 1 or None in ids:
        return reads

    def window(record: object) -> dict:
        return {
            key: value
            for key, value in _attr_or_get(record, "args", {}).items()
            if key not in {"cursor", "merchant_id"}
        }

    scope = window(scans[0])
    if any(window(r) != scope for r in scans):
        return reads
    results = [
        r
        for r in transactions
        if _attr_or_get(r, "args", {}).get("merchant_id") in ids and window(r) == scope
    ]
    if (
        not results
        or _read_card_data(results[-1]).get("next_cursor")
        or any(
            _read_card_data(r).get("totals_scope") != "filtered_all"
            or type(_read_card_data(r).get("expense_cents")) is not int
            for r in results
        )
    ):
        return reads
    return [r for r in reads if _attr_or_get(r, "tool") != "get_transactions" or r in results]


def _product_names(reads: list[object]) -> dict[str, tuple[str, str]]:
    """Join a name only when successful product reads agree on its actual ID."""
    names: dict[str, list[tuple[str, str]]] = {}
    for read in reads:
        tool = _attr_or_get(read, "tool")
        if tool not in {"list_products", "get_product", "compare_products"}:
            continue
        data = _read_card_data(read)
        items = [data] if tool == "get_product" else data.get("items", [])
        for item in items:
            id_, name = item.get("id"), item.get("name")
            if isinstance(id_, str) and isinstance(name, str) and name:
                names.setdefault(id_, []).append((name, _attr_or_get(read, "source_ref", "")))
    return {
        id_: candidates[-1]
        for id_, candidates in names.items()
        if len({name for name, _source in candidates}) == 1
    }


# ── Card generators (one per card type) ─────────────────────────────────────


def _card_plan_status(state: AgentState, cards: list[UICard]) -> None:
    """PLAN_STATUS card from execution result.

    Includes OPEN_APPROVAL action if decision is REQUIRE_APPROVAL
    with a known approval_id.
    """
    execution = state.get("execution")
    if execution is None:
        return
    steps_raw = _attr_or_get(execution, "steps", ()) or ()
    steps_data = [
        {
            "step_id": _attr_or_get(s, "step_id", ""),
            "tool": _attr_or_get(s, "tool", ""),
            "status": _attr_or_get(s, "status", ""),
        }
        for s in steps_raw
    ]

    actions: list[CardAction] = []

    # Attach OPEN_APPROVAL if pending
    decision = state.get("decision")
    if decision is not None and _attr_or_get(decision, "decision", "") == "REQUIRE_APPROVAL":
        approval_id = _attr_or_get(decision, "approval_id", None)
        if approval_id is not None:
            actions.append(CardAction(kind="OPEN_APPROVAL", ref=str(approval_id)))

    cards.append(
        UICard(
            id=_next_id(cards),
            type="PLAN_STATUS",
            source_ref=None,
            data={
                "plan_id": _attr_or_get(execution, "plan_id", ""),
                "status": _attr_or_get(execution, "status", ""),
                "steps": steps_data,
            },
            actions=tuple(actions),
        )
    )


def _card_blocked(state: AgentState, cards: list[UICard]) -> None:
    """BLOCKED card from decision findings."""
    decision = state.get("decision")
    if decision is None:
        return
    findings_raw = _attr_or_get(decision, "findings", ()) or ()
    findings_data: list[dict] = []
    for f in findings_raw:
        findings_data.append(
            {
                "code": _attr_or_get(f, "code", ""),
                "resolver": _attr_or_get(f, "resolver", None),
            }
        )
    cards.append(
        UICard(
            id=_next_id(cards),
            type="BLOCKED",
            source_ref=None,
            data={"findings": findings_data},
        )
    )


def _card_replan_hints(state: AgentState, cards: list[UICard]) -> None:
    """Expose L2 hint IDs; visible action labels are fetched from G22a."""
    decision = state.get("decision")
    if decision is None:
        return
    hints_raw = _attr_or_get(decision, "replan_hints", ()) or ()
    hint_actions: list[CardAction] = []
    hint_labels: list[str] = []
    for h in hints_raw:
        ref = _attr_or_get(h, "id", None)
        label = _attr_or_get(h, "label", None)
        if isinstance(ref, str) and re.fullmatch(r"hnt_[A-Za-z0-9_-]+", ref):
            hint_actions.append(CardAction(kind="HINT", ref=ref))
        if label is not None:
            hint_labels.append(str(label))
    cards.append(
        UICard(
            id=_next_id(cards),
            type="REPLAN_HINTS",
            source_ref=None,
            data={"hints": hint_labels},
            actions=tuple(hint_actions),
        )
    )


def _card_clarification(state: AgentState, cards: list[UICard]) -> None:
    """CLARIFICATION card from G21 clarification payload."""
    clarification = state.get("clarification")
    if clarification is None:
        return
    data = _safe_data(clarification)
    clarify_actions: list[CardAction] = []
    # If there are entity_id candidates, add CLARIFY actions
    candidates = _attr_or_get(clarification, "candidates", ()) or ()
    for c in candidates:
        eid = _attr_or_get(c, "entity_id", None)
        if eid is not None:
            clarify_actions.append(CardAction(kind="CLARIFY", ref=str(eid)))
    cards.append(
        UICard(
            id=_next_id(cards),
            type="CLARIFICATION",
            source_ref=None,
            data=data,
            actions=tuple(clarify_actions),
        )
    )


def _cards_from_reads(state: AgentState, cards: list[UICard]) -> None:
    """Generate ACCOUNT/CARD/SUBSCRIPTIONS/etc. cards from reads data."""
    reads = _card_reads(state)
    names = _product_names(list(state.get("reads") or []))
    for r in reads:
        tool = _attr_or_get(r, "tool", "") or ""
        card_type = _READ_TOOL_CARD_TYPE.get(tool)
        if card_type is None:
            continue
        source_ref = _attr_or_get(r, "source_ref", "")
        safe = _read_card_data(r)
        if tool == "get_holdings":
            for key in ("holdings", "orders"):
                for item in safe.get(key, []):
                    matched = names.get(item.get("product_id"))
                    if matched is not None:
                        item["product_name"], item["product_name_source_ref"] = matched
        if not safe:
            continue
        cards.append(
            UICard(
                id=_next_id(cards),
                type=card_type,
                source_ref=str(source_ref) if source_ref else None,
                data=safe,
            )
        )


# ── Main public function ───────────────────────────────────────────────────


def generate_cards(state: AgentState) -> tuple[UICard, ...]:
    """Deterministically generate UICards from AgentState.

    Order is stable: BLOCKED / REPLAN_HINTS first (actionable), then
    PLAN_STATUS, then CLARIFICATION, then data cards from reads.
    """
    cards: list[UICard] = []

    decision = state.get("decision")
    execution = state.get("execution")

    d_value = _attr_or_get(decision, "decision", "")

    # BLOCKED: DENY or FAILED
    if d_value == "DENY" or _attr_or_get(execution, "status", "") == "FAILED":
        _card_blocked(state, cards)

    # REPLAN_HINTS: REPLAN + user resolver
    if d_value == "REPLAN" and _has_user_resolver(decision):
        _card_replan_hints(state, cards)

    # PLAN_STATUS: EXECUTED or UNKNOWN
    exec_status = _attr_or_get(execution, "status", "")
    if exec_status in ("EXECUTED", "UNKNOWN"):
        _card_plan_status(state, cards)
    elif (
        d_value == "REQUIRE_APPROVAL"
        and exec_status not in ("EXECUTED", "UNKNOWN")
        and _attr_or_get(decision, "approval_id", None) is not None
    ):
        # Awaiting approval: no execution yet, but push the approval action
        plan_id = _attr_or_get(decision, "plan_id", "") or ""
        approval_id = str(_attr_or_get(decision, "approval_id", ""))
        cards.append(
            UICard(
                id=_next_id(cards),
                type="PLAN_STATUS",
                source_ref=None,
                data={"plan_id": plan_id, "status": "PENDING_APPROVAL"},
                actions=(CardAction(kind="OPEN_APPROVAL", ref=approval_id),),
            )
        )

    # OPEN_APPROVAL is now attached inside _card_plan_status when
    # decision == REQUIRE_APPROVAL — no separate step needed.

    # CLARIFICATION: unclear + INTENT_UNCLEAR
    understanding = state.get("understanding")
    if understanding is not None:
        unclear = _attr_or_get(understanding, "unclear", False)
        parse_status = state.get("parse_status", "")
        if unclear and parse_status == "INTENT_UNCLEAR":
            _card_clarification(state, cards)

    # Analysis reads → typed cards
    if not d_value or d_value in ("ALLOW",):
        # Only generate data cards when no write decision is pending (analysis)
        needs_write = (
            _attr_or_get(understanding, "needs_write", False)
            if understanding is not None
            else False
        )
        if not needs_write:
            _cards_from_reads(state, cards)

    # Scoped X1 retains its write/approval status, while its complete current
    # analysis evidence remains visible with the real source refs.
    analysis = x1_analysis_records(state)
    if analysis:
        _cards_from_reads({"reads": list(analysis)}, cards)

    return tuple(cards[:5])


# ── Helpers ─────────────────────────────────────────────────────────────────


def _attr_or_get(obj: object, key: str, default: object = None) -> object:
    if hasattr(obj, key):
        return getattr(obj, key, default)
    if isinstance(obj, dict):
        return obj.get(key, default)
    return default


def _has_user_resolver(decision: object) -> bool:
    findings = _attr_or_get(decision, "findings", ()) or ()
    for f in findings:
        resolver = _attr_or_get(f, "resolver", None)
        if resolver == "user":
            return True
    return False
