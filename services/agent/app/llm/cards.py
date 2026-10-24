"""C1e deterministic UICard generator (PRD §6.7.3, §9.5, C1e Task 5).

``generate_cards(state)`` produces a list of ``UICard`` objects from
``AgentState`` — no LLM, no randomness.  Cards are pushed via SSE ``custom``
events and never enter AgentState.
"""

from __future__ import annotations

from typing import Any

from safebank_contracts.base import Contract
from pydantic import Field
from typing import Annotated, Literal

from services.agent.app.graph.state import AgentState


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

# Untrusted field names — must never appear in card data
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

# Read-tool → card-type mapping
_READ_TOOL_CARD_TYPE: dict[str, _CardType] = {
    "get_accounts": "ACCOUNT",
    "get_balance_history": "TRANSACTIONS",
    "get_transactions": "TRANSACTIONS",
    "get_cards": "CARD",
    "get_subscriptions": "SUBSCRIPTIONS",
    "get_products": "PRODUCTS",
    "get_portfolio": "HOLDINGS",
    "get_anomalies": "ANOMALIES",
}


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
            clean[k] = [
                _filter_untrusted(item) if isinstance(item, dict) else item
                for item in v
            ]
        else:
            clean[k] = v
    return clean


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
    """REPLAN_HINTS card with L2's replan_hints labels."""
    decision = state.get("decision")
    if decision is None:
        return
    hints_raw = _attr_or_get(decision, "replan_hints", ()) or ()
    hint_actions: list[CardAction] = []
    hint_labels: list[str] = []
    for h in hints_raw:
        ref = _attr_or_get(h, "ref", None)
        label = _attr_or_get(h, "label", None)
        if ref is not None:
            hint_actions.append(CardAction(kind="HINT", ref=str(ref)))
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
    reads = state.get("reads") or []
    for r in reads:
        tool = _attr_or_get(r, "tool", "") or ""
        card_type = _READ_TOOL_CARD_TYPE.get(tool)
        if card_type is None:
            continue
        source_ref = _attr_or_get(r, "source_ref", "")
        data = _attr_or_get(r, "data", None)
        safe = _safe_data(data) if data is not None else {}
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
                actions=(
                    CardAction(kind="OPEN_APPROVAL", ref=approval_id),
                ),
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

    return tuple(cards)


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