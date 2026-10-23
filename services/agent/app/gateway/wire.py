"""L1-local gateway wire models (C1c1).

These mirror the real L2 wire shapes from PRD §9.5 / gateway ``app/schemas`` without
importing gateway code.  They are **not** authorization: L2 is the only authority.  They
only decide whether a payload is well-formed enough to route on.

Strictness rules that matter for the security boundary:

- ``decision`` / ``risk`` / finding ``effect`` are closed enums — an unknown value is a
  protocol failure, never a success.
- Financial amounts (cents, versions, clock versions) reject ``float`` / ``bool`` /
  string coercion via the frozen ``safebank_contracts`` scalar types.
- A success ``Decision`` must bind its ``plan_id`` / ``evaluation_id`` / ``revision``;
  a malformed or missing field fails validation.
- Legitimate hint/action utterances may carry ``text=null`` and all wire fields; the
  model preserves them without treating them as typed authorization (that distinction is
  a downstream C1c2 routing concern).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, StrictBool, StrictInt, model_validator
from safebank_contracts.base import Cents, Contract
from safebank_contracts.tools import Id

_DECISION = Literal["ALLOW", "REQUIRE_APPROVAL", "REPLAN", "DENY"]
_RISK = Literal["GREEN", "YELLOW", "RED"]
_EFFECT = Literal["DENY", "REPLAN", "RISK_YELLOW", "RISK_RED", "INFO"]
_RESOLVER = Literal["agent", "user"]


class GatewayUtterance(Contract):
    """G20 item (gateway ``UtteranceRef``). ``text`` may be null for hint/action."""

    id: Id
    kind: str
    text: str | None = None
    scope: dict[str, Any]
    parse_status: str
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
    kind: str
    parent_utterance_ids: tuple[Id, ...]
    required_fields: tuple[str, ...]
    prompt_template_id: str | None = None
    partial_scope: dict[str, Any] = {}
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
    code: str
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
    scope: dict[str, Any]
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
    shadow_b1: dict[str, Any] | None = None


class UnknownExecution(Contract):
    """202 UNKNOWN acknowledgement (gateway ``ExecutionAccepted``)."""

    plan_id: Id
    status: Literal["UNKNOWN"] = "UNKNOWN"
