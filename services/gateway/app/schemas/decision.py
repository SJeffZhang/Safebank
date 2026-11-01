"""Decision and finding wire models (PRD §7.9, §9.5)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import model_validator
from safebank_contracts.base import Contract
from safebank_contracts.codes import Code
from safebank_contracts.enums import Risk
from safebank_contracts.tools import Id

from .enums import FindingEffect
from .scope import NormalizedScope


class DecisionValue(StrEnum):
    ALLOW = "ALLOW"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    REPLAN = "REPLAN"
    DENY = "DENY"


class FindingView(Contract):
    code: Code
    effect: FindingEffect
    step_id: Id | None = None
    resolver: str | None = None
    message: str = ""
    evidence_refs: tuple[str, ...] = ()

    @model_validator(mode="after")
    def resolver_rule(self):
        if self.effect is FindingEffect.REPLAN:
            if self.resolver not in ("agent", "user"):
                raise ValueError("REPLAN findings must name a resolver (agent|user)")
        elif self.resolver is not None:
            raise ValueError("only REPLAN findings carry a resolver")
        return self


class ReplanHint(Contract):
    id: Id
    label: str
    scope: NormalizedScope
    expires_at_wall: str


class StepPreflight(Contract):
    step_id: Id
    status: str
    effects: tuple[dict[str, Any], ...] = ()
    violations: tuple[dict[str, Any], ...] = ()


class BalanceView(Contract):
    account_id: Id
    balance_before_cents: int
    balance_after_cents: int
    available_before_cents: int
    available_after_cents: int
    daily_used_before_cents: int
    daily_used_after_cents: int


class HoldingView(Contract):
    product_id: Id
    units_before: str
    units_after: str
    reserved_before: str
    reserved_after: str


class PreflightView(Contract):
    executable: bool
    steps: tuple[StepPreflight, ...] = ()
    balances: tuple[BalanceView, ...] = ()
    holdings: tuple[HoldingView, ...] = ()
    scheduled_reserve_cents: int = 0
    reserve_projection_available: bool = False


class Decision(Contract):
    plan_id: Id
    evaluation_id: Id
    revision: int
    decision: DecisionValue
    risk: Risk
    approval_id: Id | None = None
    findings: tuple[FindingView, ...] = ()
    replan_hints: tuple[ReplanHint, ...] = ()
    preflight: PreflightView = PreflightView(executable=True)
    versions: dict[str, int] = {}
    clock_version: int = 0
    policy_version: str = ""
    shadow_b1: Decision | None = None
