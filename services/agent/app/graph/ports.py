"""Typed ports injected into the L1 graph (C1a).

Each node delegates to exactly one small, explicitly-injected callable; the graph never
manufactures a success value.  C1b (LLM), C1c (gateway client) and C1e (reply/verify)
supply the real implementations.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from safebank_contracts.plan import ExecutionResult, PlanDraft

from services.agent.app.graph.state import (
    AgentContext,
    AgentState,
    Decision,
    ReadRecord,
    Understanding,
)


@dataclass(frozen=True)
class Ports:
    understand: Callable[[AgentState, AgentContext], Understanding]
    gather: Callable[[AgentState, AgentContext], list[ReadRecord]]
    plan: Callable[[AgentState, AgentContext], PlanDraft]
    evaluate: Callable[[AgentState, AgentContext], Decision]
    execute: Callable[[AgentState, AgentContext], ExecutionResult]
    respond: Callable[[AgentState, AgentContext], str]
    verify_output: Callable[[AgentState, AgentContext], str]
