"""Evaluation pipeline shared types: EvalContext, Finding, Stage protocol (PRD §7.2, §7.9)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from safebank_contracts.codes import Code
from safebank_contracts.plan import PlanDraft
from safebank_contracts.state import BankState


class Effect(StrEnum):
    DENY = "DENY"
    REPLAN = "REPLAN"
    RISK_YELLOW = "RISK_YELLOW"
    RISK_RED = "RISK_RED"
    INFO = "INFO"
    ERROR = "ERROR"


class Resolver(StrEnum):
    AGENT = "agent"
    USER = "user"


@dataclass(frozen=True)
class Finding:
    code: Code
    effect: Effect
    step_id: str | None = None
    resolver: Resolver | None = None
    message: str = ""
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.effect is Effect.REPLAN and self.resolver is None:
            raise ValueError("REPLAN findings must name a resolver (agent|user)")
        if self.effect is not Effect.REPLAN and self.resolver is not None:
            raise ValueError("only REPLAN findings carry a resolver")


AuditHandle = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass
class EvalContext:
    """Mutable evaluation state threaded through the nine stages.

    W2 fills entity/provenance/intent; W3 fills snapshot/preflight/workflow/
    decision. ``snapshot`` is a placeholder the snapshot stage populates only
    after the pre-snapshot stages have passed.
    """

    request: dict[str, Any] | None = None
    caller: str | None = None
    origin: str | None = None
    customer_id: str | None = None
    session_id: str | None = None
    plan_draft: PlanDraft | None = None
    plan_id: str | None = None
    snapshot: BankState | None = None
    findings: list[Finding] = field(default_factory=list)
    audit: AuditHandle | None = None
    short_circuit: bool = False

    def add_finding(self, finding: Finding) -> None:
        self.findings.append(finding)

    def add(
        self,
        code: Code,
        effect: Effect,
        *,
        step_id: str | None = None,
        resolver: Resolver | None = None,
        message: str = "",
        evidence_refs: tuple[str, ...] = (),
    ) -> None:
        self.add_finding(Finding(code, effect, step_id, resolver, message, evidence_refs))

    def has_hard_deny(self) -> bool:
        return any(f.effect is Effect.DENY for f in self.findings)


class Stage(ABC):
    name: str

    @abstractmethod
    async def run(self, ctx: EvalContext) -> None:
        """Execute this stage; append findings to ``ctx.findings``."""
