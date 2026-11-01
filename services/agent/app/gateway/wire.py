"""L1-local gateway wire models (C1c1).

These mirror the real L2 wire shapes from PRD §9.5 / gateway ``app/schemas`` without
importing gateway code.  They are **not** authorization: L2 is the only authority.  They
only decide whether a payload is well-formed enough to route on.

Strictness rules that matter for the security boundary:

- ``decision`` / ``risk`` / finding ``effect`` / ``parse_status`` / clarification ``kind``
  and finding ``code`` are closed enums — an unknown value is a protocol failure, never a
  success.
- Financial amounts (cents, versions, clock versions, scope-transfer amounts) reject
  ``float`` / ``bool`` / string coercion via the frozen ``safebank_contracts`` scalar types.
- ``scope`` / ``partial_scope`` / replan-hint ``scope`` are a structured ``NormalizedScope``
  mirror (``fields`` stays a free map per the real contract), so a float transfer amount is
  rejected instead of passing through a free ``dict``.
- A success ``Decision`` must bind its ``plan_id`` / ``evaluation_id`` / ``revision``;
  ``shadow_b1`` is a recursive ``GatewayDecision | None`` (null for the agent caller).
- Legitimate hint/action utterances may carry ``text=null``; ``kind`` is genuinely an open
  ``str`` on the wire and is preserved as such (typed-vs-hint is a C1c2 routing concern).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, StrictBool, StrictInt, model_validator
from safebank_contracts.base import Cents, Contract
from safebank_contracts.codes import Code
from safebank_contracts.tools import Id

_DECISION = Literal["ALLOW", "REQUIRE_APPROVAL", "REPLAN", "DENY"]
_RISK = Literal["GREEN", "YELLOW", "RED"]
_EFFECT = Literal["DENY", "REPLAN", "RISK_YELLOW", "RISK_RED", "INFO"]
_RESOLVER = Literal["agent", "user"]


class GatewayParseStatus(StrEnum):
    """Mirror of gateway ``ParseStatus`` (schemas/enums.py)."""

    SCOPED = "SCOPED"
    NO_WRITE = "NO_WRITE"
    INTENT_UNCLEAR = "INTENT_UNCLEAR"
    CLARIFYING = "CLARIFYING"


class GatewayClarificationKind(StrEnum):
    """Mirror of gateway ``ClarificationKind`` (schemas/enums.py)."""

    SLOTS = "SLOTS"
    FORM_REQUIRED = "FORM_REQUIRED"


class GatewayScopeTransfer(Contract):
    """Mirror of gateway ``ScopeTransfer``; ``amount_cents`` is strict ``Cents``."""

    target_fingerprint: str
    amount_cents: Cents
    from_account_id: Id | None = None


class GatewayNormalizedScope(Contract):
    """Mirror of gateway ``NormalizedScope``; ``fields`` stays a free map."""

    action_kinds: tuple[str, ...]
    from_account_id: Id | None = None
    transfers: tuple[GatewayScopeTransfer, ...] = ()
    period: str | None = None
    channel: str | None = None
    fields: dict[str, Any] = {}


class GatewayUtterance(Contract):
    """G20 item (gateway ``UtteranceRef``). ``text`` may be null for hint/action."""

    id: Id
    kind: str
    text: str | None = None
    scope: GatewayNormalizedScope
    parse_status: GatewayParseStatus
    echoes_untrusted: StrictBool = False
    parent_ids: tuple[Id, ...] = ()
    clarification_id: Id | None = None
    created_at_wall: str
    expires_at_wall: str


class GatewayCandidate(Contract):
    field: str
    entity_id: str
    label: str
    source_ref: str


class GatewayClarification(Contract):
    """G21 clarification member (the client maps the ``null`` wrapper to ``None``)."""

    id: Id
    kind: GatewayClarificationKind
    parent_utterance_ids: tuple[Id, ...]
    required_fields: tuple[str, ...]
    prompt_template_id: str | None = None
    partial_scope: GatewayNormalizedScope
    candidates: tuple[GatewayCandidate, ...] = ()
    expires_at_wall: str


class GatewayReadEnvelope(Contract):
    """G2 ReadEnvelope: raw ``data`` is preserved verbatim, never re-derived by L1."""

    data: Any
    source_ref: Id
    untrusted_paths: tuple[str, ...] = ()
    versions: dict[str, StrictInt] = {}
    observed_at: str


class GatewayFinding(Contract):
    code: Code
    effect: _EFFECT
    step_id: Id | None = None
    resolver: _RESOLVER | None = None
    message: str = ""
    evidence_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def resolver_rule(self):
        if self.effect == "REPLAN":
            if self.resolver not in ("agent", "user"):
                raise ValueError("REPLAN findings must name a resolver (agent|user)")
        elif self.resolver is not None:
            raise ValueError("only REPLAN findings carry a resolver")
        return self


class GatewayReplanHint(Contract):
    id: Id
    label: str
    scope: GatewayNormalizedScope
    expires_at_wall: str


class GatewayStepPreflight(Contract):
    step_id: Id
    status: str
    effects: tuple[dict[str, Any], ...] = ()
    violations: tuple[dict[str, Any], ...] = ()


class GatewayBalance(Contract):
    account_id: Id
    balance_before_cents: Cents
    balance_after_cents: Cents
    available_before_cents: Cents
    available_after_cents: Cents
    daily_used_before_cents: Cents
    daily_used_after_cents: Cents


class GatewayHolding(Contract):
    product_id: Id
    units_before: str
    units_after: str
    reserved_before: str
    reserved_after: str


class GatewayPreflight(Contract):
    executable: StrictBool = True
    steps: tuple[GatewayStepPreflight, ...] = ()
    balances: tuple[GatewayBalance, ...] = ()
    holdings: tuple[GatewayHolding, ...] = ()
    scheduled_reserve_cents: Cents = 0
    reserve_projection_available: StrictBool = False


class GatewayDecision(Contract):
    """G4/G8 latest decision (gateway ``Decision``); plan-binding fields are required."""

    plan_id: Id
    evaluation_id: Id
    revision: StrictInt
    decision: _DECISION
    risk: _RISK
    approval_id: Id | None = None
    findings: tuple[GatewayFinding, ...] = ()
    replan_hints: tuple[GatewayReplanHint, ...] = ()
    preflight: GatewayPreflight = Field(default_factory=GatewayPreflight)
    versions: dict[str, StrictInt] = {}
    clock_version: StrictInt = 0
    policy_version: str = ""
    shadow_b1: GatewayDecision | None = None


class UnknownExecution(Contract):
    """202 UNKNOWN acknowledgement (gateway ``ExecutionAccepted``)."""

    plan_id: Id
    status: Literal["UNKNOWN"] = "UNKNOWN"


GatewayDecision.model_rebuild()
