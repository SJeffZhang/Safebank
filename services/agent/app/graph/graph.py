"""C1a LangGraph skeleton (PRD §6.1): control flow only.

Nodes delegate to injected :class:`Ports`; there is no LLM, no L2/L3 call and no
authorization here.  A decision value returned by the injected ``evaluate`` port routes
the graph — it is never invented by the skeleton.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from services.agent.app.graph.errors import (
    CrossCustomerError,
    EmptyUtterancesError,
    MissingPortError,
    PortFailure,
    SessionMismatchError,
)
from services.agent.app.graph.ports import Ports
from services.agent.app.graph.state import (
    AgentContext,
    AgentInput,
    AgentState,
    DecisionValue,
)

_REQUIRED_PORTS = (
    "understand",
    "gather",
    "plan",
    "evaluate",
    "execute",
    "respond",
    "verify_output",
)


def _route_understand(state: AgentState) -> str:
    understanding = state.get("understanding")
    if understanding is not None and understanding.unclear and understanding.needs_write:
        return "respond"
    return "gather"


def _route_gather(state: AgentState) -> str:
    understanding = state.get("understanding")
    if understanding is None or not understanding.needs_write:
        return "respond"
    return "plan"


def _route_evaluate(state: AgentState) -> str:
    decision = state.get("decision")
    if decision is None:
        return "respond"
    value = decision.decision
    if value is DecisionValue.ALLOW:
        return "execute"
    if value is DecisionValue.REQUIRE_APPROVAL:
        return "await_approval"
    if value is DecisionValue.DENY:
        return "respond"
    if value is DecisionValue.REPLAN:
        resolvers = [finding.resolver for finding in decision.findings]
        if resolvers and all(r == "agent" for r in resolvers) and state.get("replan_count", 0) < 2:
            return "gather"
        return "respond"
    return "respond"


def _route_await_approval(state: AgentState) -> str:
    # approve_id is only set by await_approval on a matching resume value.
    return "execute" if state.get("approval_id") else "respond"


def _require_resume_binding(state: AgentState, context: AgentContext, thread_id: object) -> None:
    """Reject a resume whose runtime identity does not match the thread's persisted binding.

    Runs before ``interrupt()`` so it holds on both the initial entry into
    ``await_approval`` and its resume re-entry; any port side effect (execute/respond/
    verify_output) is therefore never reached with a mismatched customer/session.
    """

    if thread_id != context.session_id:
        raise SessionMismatchError(
            f"thread_id {thread_id!r} does not match session {context.session_id!r}"
        )
    if state.get("customer_id") != context.customer_id:
        raise CrossCustomerError(
            f"thread bound to customer {state.get('customer_id')!r}, got {context.customer_id!r}"
        )
    if state.get("session_id") != context.session_id:
        raise SessionMismatchError(
            f"thread bound to session {state.get('session_id')!r}, got {context.session_id!r}"
        )


def build_graph(ports: Ports, checkpointer: BaseCheckpointSaver) -> CompiledStateGraph:
    """Compile the C1a skeleton.

    ``ports`` and ``checkpointer`` are both required and fail-closed: missing pieces raise
    :class:`MissingPortError` instead of defaulting to a success placeholder.
    """

    for name in _REQUIRED_PORTS:
        if getattr(ports, name) is None:
            raise MissingPortError(f"port {name!r} is required")
    if checkpointer is None:
        raise MissingPortError("checkpointer is required")

    def ingest(state: AgentState, runtime: Runtime[AgentContext], config: RunnableConfig) -> dict:
        context = runtime.context
        thread_id = (config.get("configurable") or {}).get("thread_id")
        if thread_id != context.session_id:
            raise SessionMismatchError(
                f"thread_id {thread_id!r} does not match session {context.session_id!r}"
            )
        existing = state.get("customer_id")
        if existing is not None and existing != context.customer_id:
            raise CrossCustomerError(
                f"thread already bound to customer {existing!r}, got {context.customer_id!r}"
            )
        utterances = state.get("utterances") or []
        if not utterances:
            raise EmptyUtterancesError("a new input round requires at least one utterance")
        texts = [utterance.text for utterance in utterances]
        return {
            "customer_id": context.customer_id,
            "session_id": context.session_id,
            "understanding": None,
            "reads": [],
            "plan_draft": None,
            "plan_id": None,
            "decision": None,
            "replan_count": 0,
            "approval_id": None,
            "execution": None,
            "reply_draft": None,
            "messages": [HumanMessage(content=" ".join(texts))],
        }

    def understand(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
        result = ports.understand(state, runtime.context)
        if result is None:
            raise PortFailure("understand port returned None")
        return {"understanding": result}

    def gather(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
        reads = ports.gather(state, runtime.context)
        if reads is None:
            raise PortFailure("gather port returned None")
        update: dict = {"reads": list(reads)}
        decision = state.get("decision")
        if decision is not None and decision.decision is DecisionValue.REPLAN:
            # PRD §6.1.1: each REPLAN reroute to gather counts one replan.
            update["replan_count"] = state.get("replan_count", 0) + 1
        return update

    def plan(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
        draft = ports.plan(state, runtime.context)
        if draft is None:
            raise PortFailure("plan port returned None")
        return {"plan_draft": draft}

    def evaluate(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
        decision = ports.evaluate(state, runtime.context)
        if decision is None:
            raise PortFailure("evaluate port returned None")
        return {"plan_id": decision.plan_id, "decision": decision}

    def await_approval(
        state: AgentState, runtime: Runtime[AgentContext], config: RunnableConfig
    ) -> dict:
        context = runtime.context
        thread_id = (config.get("configurable") or {}).get("thread_id")
        _require_resume_binding(state, context, thread_id)
        decision = state.get("decision")
        approval_id = decision.approval_id if decision is not None else None
        if approval_id is None:
            raise PortFailure("REQUIRE_APPROVAL decision without approval_id")
        answer = interrupt({"approval_id": approval_id})
        if answer != approval_id:
            return {"approval_id": None}
        return {"approval_id": approval_id}

    def execute(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
        result = ports.execute(state, runtime.context)
        if result is None:
            raise PortFailure("execute port returned None")
        return {"execution": result}

    def respond(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
        draft = ports.respond(state, runtime.context)
        if draft is None:
            raise PortFailure("respond port returned None")
        return {"reply_draft": draft}

    def verify_output(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
        final = ports.verify_output(state, runtime.context)
        if final is None:
            raise PortFailure("verify_output port returned None")
        return {"messages": [AIMessage(content=final)]}

    builder = StateGraph(AgentState, context_schema=AgentContext, input_schema=AgentInput)
    builder.add_node("ingest", ingest)
    builder.add_node("understand", understand)
    builder.add_node("gather", gather)
    builder.add_node("plan", plan)
    builder.add_node("evaluate", evaluate)
    builder.add_node("await_approval", await_approval)
    builder.add_node("execute", execute)
    builder.add_node("respond", respond)
    builder.add_node("verify_output", verify_output)

    builder.add_edge(START, "ingest")
    builder.add_edge("ingest", "understand")
    builder.add_conditional_edges(
        "understand", _route_understand, {"respond": "respond", "gather": "gather"}
    )
    builder.add_conditional_edges("gather", _route_gather, {"respond": "respond", "plan": "plan"})
    builder.add_edge("plan", "evaluate")
    builder.add_conditional_edges(
        "evaluate",
        _route_evaluate,
        {
            "execute": "execute",
            "await_approval": "await_approval",
            "respond": "respond",
            "gather": "gather",
        },
    )
    builder.add_conditional_edges(
        "await_approval", _route_await_approval, {"execute": "execute", "respond": "respond"}
    )
    builder.add_edge("execute", "respond")
    builder.add_edge("respond", "verify_output")
    builder.add_edge("verify_output", END)

    return builder.compile(checkpointer=checkpointer)
