"""Gateway-facing shared models (PRD §9.5) — the single home for wire shapes.

These are the types W1–W4 consume and produce. They are pinned in W0: changing
a shape is an interface change and must be raised in the owning task's progress
「待决」 for the conductor to apply centrally.
"""

from .decision import (
    BalanceView,
    Decision,
    DecisionValue,
    FindingView,
    HoldingView,
    PreflightView,
    ReplanHint,
    StepPreflight,
)
from .enums import (
    ActionKind,
    ApprovalStatus,
    ClarificationKind,
    ClarificationStatus,
    ExecutionJobStatus,
    FindingEffect,
    ParseStatus,
    PlanStatus,
    ServiceKind,
    UtteranceStatus,
    WorkflowEventStatus,
)
from .execution import ExecutionAccepted
from .identity import Caller
from .planview import Disclosure, PlanView, PlanViewField, PlanViewStep
from .read import ReadEnvelope
from .requests import AppFormPlanRequest
from .scope import AuthorizationInputs, NormalizedScope, ScopeTransfer, UtteranceRef

__all__ = [
    "ActionKind",
    "ApprovalStatus",
    "ClarificationKind",
    "ClarificationStatus",
    "ExecutionJobStatus",
    "FindingEffect",
    "ParseStatus",
    "PlanStatus",
    "ServiceKind",
    "UtteranceStatus",
    "WorkflowEventStatus",
    "BalanceView",
    "Decision",
    "DecisionValue",
    "FindingView",
    "HoldingView",
    "PreflightView",
    "ReplanHint",
    "StepPreflight",
    "ExecutionAccepted",
    "Caller",
    "Disclosure",
    "PlanView",
    "PlanViewField",
    "PlanViewStep",
    "ReadEnvelope",
    "AppFormPlanRequest",
    "AuthorizationInputs",
    "NormalizedScope",
    "ScopeTransfer",
    "UtteranceRef",
]
