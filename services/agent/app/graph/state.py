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
from pydantic import StrictInt
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
    intent_index: StrictInt
    name: str
    value: str
    span: tuple[StrictInt, StrictInt]


class Understanding(Contract):
    """LLM proposal only (PRD §6.1 understand node); never an authorization source."""

    intents: tuple[str, ...]
    needs_write: bool
    unclear: bool = False
    slots: tuple[Slot, ...] = ()


class ReadRecord(Contract):
    source_ref: Id
    untrusted_paths: tuple[str, ...] = ()


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


class AgentInput(TypedDict):
    """Declared new-input shape (``utterances`` required) for documentation/schema purposes.

    This type declaration alone does **not** make ``graph.invoke`` reject a missing
    ``utterances`` key: LangGraph merges a dict input into the thread's persisted state, so a
    bare ``invoke({})`` would still reuse the previous round's utterances.  New input must go
    through ``invoke_new_input`` (which fails closed on empty/missing utterances), with the
    ``ingest`` empty-utterances check as a second, in-graph guard.
    """

    utterances: list[UtteranceRef]
