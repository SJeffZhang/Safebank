"""C1a ``AgentState`` and L1-local models (PRD §6.1).

Only display user/assistant text lives in ``messages``; every other field is reset by
``ingest`` at the start of each new-input round.  Secret material (assistant token, LLM
API key, service tokens) never enters state: it travels in ``AgentContext`` (LangGraph
runtime context) so it is not checkpointed.

The L1-local models below (``UtteranceRef``, ``Understanding``, ``ReadRecord``,
``Decision``) are placeholders for the C1a control-flow skeleton.  C1b/C1c replace their
wire shape with the real L2 / contracts types; they must not be treated as authorization.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import Field, StrictBool, StrictInt, model_validator
from safebank_contracts.base import Contract
from safebank_contracts.plan import ExecutionResult, PlanDraft
from safebank_contracts.tools import Id


class UtteranceKind(StrEnum):
    TYPED = "typed"
    HINT = "hint"
    ACTION = "action"
    CONTEXT = "context"


class UtteranceRef(Contract):
    """A boundary-adapted utterance already registered with L2 (id, kind, text)."""

    id: Id
    kind: UtteranceKind
    text: str


class Slot(Contract):
    intent_index: Annotated[StrictInt, Field(ge=0)]
    name: str
    value: str
    span: tuple[StrictInt, StrictInt]

    @model_validator(mode="after")
    def ordered_span(self):
        if not 0 <= self.span[0] < self.span[1]:
            raise ValueError("invalid character span")
        return self


class Understanding(Contract):
    """LLM proposal only (PRD §6.1 understand node); never an authorization source."""

    intents: tuple[str, ...]
    needs_write: StrictBool
    unclear: StrictBool = False
    slots: tuple[Slot, ...] = ()


class ReadRecord(Contract):
    """A locally validated read result plus the metadata needed to cite it in a plan.

    ``data`` is the wall-stripped, untrusted-wrapped payload (already safe to feed back into
    the plan prompt); it never carries JWT/service tokens, ``*_wall`` fields, or any untrusted
    content promoted to a trusted source.
    """

    source_ref: Id
    tool: str = ""
    args: dict[str, Any] = {}
    data: dict[str, Any] = {}
    untrusted_paths: tuple[str, ...] = ()
    versions: dict[str, int] = {}
    observed_at: str = ""


class DecisionValue(StrEnum):
    ALLOW = "ALLOW"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    REPLAN = "REPLAN"
    DENY = "DENY"


class Finding(Contract):
    code: str
    resolver: str | None = None
    evidence_refs: tuple[str, ...] = ()


class Decision(Contract):
    """Minimal L2 decision shape for C1a routing; C1c aligns to the real wire type."""

    plan_id: Id
    decision: DecisionValue
    approval_id: Id | None = None
    findings: tuple[Finding, ...] = ()
    replan_hints: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class AgentContext:
    """Run-scoped dependencies injected via LangGraph runtime context (not checkpointed)."""

    assistant_token: str
    customer_id: str
    session_id: str


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    customer_id: str
    session_id: str
    utterances: list[UtteranceRef]
    understanding: Understanding | None
    reads: list[ReadRecord]
    plan_draft: PlanDraft | None
    plan_id: str | None
    decision: Decision | None
    replan_count: int
    approval_id: str | None
    execution: ExecutionResult | None
    reply_draft: str | None
    # ── C1c2 gateway fields (PRD §6.1) ──
    parse_status: str | None  # G20 parse_status
    echoes_untrusted: bool  # G20 echoes_untrusted flag
    parent_ids: tuple[str, ...]  # G20 parent utterance ids
    clarification_id: str | None  # G20 linked clarification id
    business_date: str  # current business date injected from get_profile (best-effort)
    clarification: dict | None  # G21 clarification payload
    hints: list[dict]  # G20 ingested hint/action data (kind/id/scope)
    route_error: dict | None  # Safe error {status, code, phase, category}
    evaluate_reentry_count: int  # STATE_CHANGED re-entry counter (max 1)


class AgentInput(TypedDict):
    """Declared new-input shape (``utterances`` required) for documentation/schema purposes.

    This type declaration alone does **not** make ``graph.invoke`` reject a missing
    ``utterances`` key: LangGraph merges a dict input into the thread's persisted state, so a
    bare ``invoke({})`` would still reuse the previous round's utterances.  New input must go
    through ``invoke_new_input`` (which fails closed on empty/missing utterances), with the
    ``ingest`` empty-utterances check as a second, in-graph guard.
    """

    utterances: list[UtteranceRef]
