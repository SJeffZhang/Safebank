"""Evaluation pipeline shared types (PRD §7.2, §7.9, §10.1).

This file is owned by W0 (the conductor). W2/W3 fill the stage-specific result
objects; they must not change the shape of ``EvalContext`` or ``Stage``. Each
result slot has exactly one owning stage and is read by later stages only.

Stage → slot ownership:
  ② entity      writes ``ctx.steps`` and the resolved references, reads lazy state
  ③ provenance  writes ``ctx.provenance``
  ④ intent      writes ``ctx.intent``
  ⑤ snapshot    writes ``ctx.snapshot`` (pins versions/clock_version)
  ⑥ preflight   writes ``ctx.preflight``
  ⑦ workflow    writes ``ctx.workflow``
  ⑧ decision    writes ``ctx.decision``

Each result dataclass is owned by its stage's task: the owner may add fields to
its own result class, but must not remove or change existing fields.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from safebank_contracts.codes import Code
from safebank_contracts.plan import PlanDraft
from safebank_contracts.state import BankState
from sqlalchemy.ext.asyncio import AsyncConnection

from ..l3.client import L3Client
from ..schemas.decision import Decision, FindingView, PreflightView
from ..schemas.enums import FindingEffect, ServiceKind
from ..schemas.identity import Caller
from ..schemas.scope import AuthorizationInputs

Finding = FindingView


class EvalMode(StrEnum):
    EVALUATE = "EVALUATE"
    EXECUTE_REPLAY = "EXECUTE_REPLAY"
    SHADOW_B1 = "SHADOW_B1"


class StageStatus(StrEnum):
    PENDING = "PENDING"
    PASSED = "PASSED"
    BLOCKED = "BLOCKED"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"


class Resolver(StrEnum):
    AGENT = "agent"
    USER = "user"


DISABLED_BY_PROFILE: dict[str, frozenset[str]] = {
    "B0": frozenset({"provenance", "intent", "preflight", "workflow"}),
    "B1": frozenset({"workflow"}),
    "B2": frozenset(),
    "B2_NO_PREFLIGHT": frozenset({"preflight"}),
    "B2_NO_WORKFLOW": frozenset({"workflow"}),
    "B2_NO_TAINT": frozenset({"provenance"}),
}


@dataclass
class ResolvedStep:
    """② writes. Resolved plan step + registry-derived metadata.

    Populated by the entity stage for every step; ③④⑥⑦⑧ read it.
    """

    step_id: str
    tool: str
    args: dict[str, Any]
    action_kind: str
    base_risk: str
    outflow: bool


@dataclass
class ProvenanceResult:
    """③ writes (owned by W2). W2 may add fields; existing fields stay."""

    arg_sources: dict[str, Any] = field(default_factory=dict)
    read_refs: tuple[str, ...] = ()


@dataclass
class IntentResult:
    """④ writes (owned by W2). W2 may add fields; existing fields stay."""

    matched: bool = True
    notes: tuple[str, ...] = ()


@dataclass
class PreflightResult:
    """⑥ writes (owned by W3). W3 may add fields; existing fields stay."""

    executable: bool = True
    violations: tuple[dict[str, Any], ...] = ()
    view: PreflightView | None = None
    shadow_state: BankState | None = None


@dataclass
class WorkflowResult:
    """⑦ writes (owned by W3). W3 may add fields; existing fields stay."""

    findings: tuple[dict[str, Any], ...] = ()
    cooldowns: tuple[tuple[str, datetime], ...] = ()


@dataclass
class EvalContext:
    request: Any = None
    identity: Caller | None = None
    authorization: AuthorizationInputs | None = None
    plan_draft: PlanDraft | None = None
    plan_id: str | None = None
    origin: str | None = None
    customer_id: str | None = None
    session_id: str | None = None

    mode: EvalMode = EvalMode.EVALUATE
    profile: str = "B2"
    wall_now: Callable[[], datetime] = field(default_factory=lambda: lambda: datetime.now(UTC))
    conn: AsyncConnection | None = None
    l3: L3Client | None = None

    provenance: ProvenanceResult | None = None
    intent: IntentResult | None = None
    snapshot: BankState | None = None
    preflight: PreflightResult | None = None
    workflow: WorkflowResult | None = None
    decision: Decision | None = None
    steps: tuple[ResolvedStep, ...] = ()

    findings: list[Finding] = field(default_factory=list)
    stage_status: dict[str, StageStatus] = field(default_factory=dict)
    short_circuit: bool = False
    audit: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None

    _lazy_state: BankState | None = field(default=None, repr=False, init=False)

    @property
    def kind(self) -> ServiceKind | None:
        return self.identity.kind if self.identity else None

    async def bank_state(self) -> BankState:
        """Lazily fetch and cache the L3 state (conductor decision #4).

        ② entity uses this for ownership checks; ⑤ snapshot reuses the cached
        state and pins versions/clock_version, so the snapshot is not fetched
        twice. The snapshot is requested with the plan steps so payee directory
        and counterparty customer data are loaded.
        """
        if self._lazy_state is None:
            if self.l3 is None:
                raise RuntimeError("EvalContext.l3 is required to load bank state")
            steps = (
                [step.model_dump(mode="json") for step in self.plan_draft.steps]
                if self.plan_draft is not None
                else []
            )
            response = await self.l3.snapshot(
                {"customer_id": self.customer_id, "steps": steps, "event_id": None}
            )
            self._lazy_state = BankState.model_validate(response)
        return self._lazy_state

    def add_finding(self, finding: Finding) -> None:
        self.findings.append(finding)

    def add(
        self,
        code: Code,
        effect: FindingEffect,
        *,
        step_id: str | None = None,
        resolver: Resolver | None = None,
        message: str = "",
        evidence_refs: tuple[str, ...] = (),
    ) -> None:
        self.findings.append(
            Finding(
                code=code,
                effect=effect,
                step_id=step_id,
                resolver=resolver.value if resolver else None,
                message=message,
                evidence_refs=evidence_refs,
            )
        )

    def has_hard_deny(self) -> bool:
        return any(f.effect is FindingEffect.DENY for f in self.findings)


class Stage(ABC):
    name: str
    always_run: bool = False

    @abstractmethod
    async def run(self, ctx: EvalContext) -> None:
        """Execute this stage; append findings to ``ctx.findings``."""

    def enabled(self, profile: str) -> bool:
        return self.name not in DISABLED_BY_PROFILE.get(profile, frozenset())
