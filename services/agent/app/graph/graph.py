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

# Lazy-imported inside build_gateway_graph to avoid circular import with
# gateway.client → graph.state → graph.__init__ → graph.graph.
# The gateway adapters client is only needed for the gateway-backed graph.

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


# ── C1c2 gateway graph (PRD §6.1.1 with G20/G21 ingest) ────────────────


def build_gateway_graph(
    ports: Ports,
    checkpointer: BaseCheckpointSaver,
    client,  # GatewayClient — lazy import to avoid circular dep with graph.state
) -> CompiledStateGraph:
    """Compile the C1c2 gateway-backed graph.

    Similar to ``build_graph`` but with a gateway-aware ingest node that calls
    G20 (get_utterances) and G21 (get_clarification) instead of relying on
    caller-supplied text/scope.  The evaluate and execute nodes also handle
    ``GatewayClientError`` routing per PRD §6.1.1.

    ``ports.evaluate`` and ``ports.execute`` are expected to be gateway-backed
    (assembled via ``make_gateway_ports``).
    """
    # Lazy imports to break circular: graph.state → graph.__init__ → graph.graph
    # ↔ gateway.client ↔ gateway.adapters.
    from services.agent.app.gateway.adapters import (
        gateway_utterance_to_ref,
        ingest_via_gateway,
    )
    from services.agent.app.gateway.errors import (
        GatewayProtocolError,
        GatewayResponseError,
        GatewayTransportError,
    )
    from services.agent.app.graph.routing import (
        route_evaluate,
        route_evaluate_error,
        route_execute_error,
        route_execute_success,
    )
    from services.agent.app.graph.state import UtteranceRef

    for name in _REQUIRED_PORTS:
        if getattr(ports, name) is None:
            raise MissingPortError(f"port {name!r} is required")
    if checkpointer is None:
        raise MissingPortError("checkpointer is required")
    if client is None:
        raise MissingPortError("gateway client is required")

    # ── G20/G21 ingest ───────────────────────────────────────────────

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

        # Get utterance IDs from the incoming input.  The C1c1 invoke path
        # pre-populates utterances with caller-supplied UUIDs only; G20
        # provides the authoritative text/kinds/parse_status.
        utterances_raw = state.get("utterances") or []
        if not utterances_raw:
            raise EmptyUtterancesError("a new input round requires at least one utterance id")

        # Collect IDs: these may be UtteranceRef (id-only from invoke_gateway_input)
        # or dicts.  Only id is consumed; text/kind are replaced by G20.
        utterance_ids = tuple(
            u.id if hasattr(u, "id") else (u.get("id") if isinstance(u, dict) else str(u))
            for u in utterances_raw
        )

        # G20 + G21
        gateway_utts, clarification, _warnings = ingest_via_gateway(
            client, utterance_ids, context=context, session_id=context.session_id
        )

        # Build authoritative utterance refs.
        refs: list[UtteranceRef] = [gateway_utterance_to_ref(u) for u in gateway_utts]

        # Collect hints (non-typed utterances) for LLM context.
        hints = [
            {"id": u.id, "kind": u.kind, "scope": u.scope.model_dump(mode="json")}
            for u in gateway_utts
            if u.kind != "typed"
        ]

        # Check INTENT_UNCLEAR → needs to be captured in state for routing.
        any_unclear = any(u.parse_status == "INTENT_UNCLEAR" for u in gateway_utts)

        # Combine texts for the HumanMessage.
        texts = [utt.text or "" for utt in refs]

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
            "parse_status": "INTENT_UNCLEAR"
            if any_unclear
            else (
                gateway_utts[0].parse_status.value
                if hasattr(gateway_utts[0].parse_status, "value")
                else str(gateway_utts[0].parse_status)
                if gateway_utts
                else "UNKNOWN"
            ),
            "echoes_untrusted": any(u.echoes_untrusted for u in gateway_utts),
            "parent_ids": tuple(set(p for u in gateway_utts for p in (u.parent_ids or ()))),
            "clarification_id": gateway_utts[0].clarification_id if gateway_utts else None,
            "clarification": (
                clarification.model_dump(mode="json") if clarification is not None else None
            ),
            "hints": hints,
            "route_error": None,
            "evaluate_reentry_count": 0,
            "utterances": refs,
            "messages": [HumanMessage(content=" ".join(texts))],
        }

    # ── gateway-aware understand ──────────────────────────────────────

    def understand(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
        result = ports.understand(state, runtime.context)
        if result is None:
            raise PortFailure("understand port returned None")
        # Propagate G20 parse_status: if INTENT_UNCLEAR, treat as unclear.
        parse_status = state.get("parse_status")
        if parse_status == "INTENT_UNCLEAR":
            from services.agent.app.graph.state import Understanding

            # Force unclear + needs_write so route_understand sends to respond.
            return {
                "understanding": Understanding(
                    intents=result.intents,
                    needs_write=result.needs_write,
                    unclear=True,
                    slots=result.slots,
                )
            }
        return {"understanding": result}

    # ── gateway-aware evaluate ────────────────────────────────────────

    def evaluate(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
        try:
            decision = ports.evaluate(state, runtime.context)
        except GatewayResponseError as exc:
            r = route_evaluate_error(
                exc.status,
                exc.code.value if hasattr(exc.code, "value") else str(exc.code),
                state.get("replan_count", 0),
                decision=exc.decision,
            )
            from services.agent.app.graph.routing import route_error

            err = route_error(
                exc.status,
                exc.code.value if hasattr(exc.code, "value") else str(exc.code),
                "evaluate",
                "RESPONSE",
            )
            return {**r.state, "route_error": err, "reply_draft": r.fixed_reply, "decision": None}
        except GatewayTransportError:
            r = route_evaluate_error(0, "TRANSPORT", state.get("replan_count", 0))
            from services.agent.app.graph.routing import route_error

            err = route_error(None, "TRANSPORT", "evaluate", "TRANSPORT")
            return {**r.state, "route_error": err, "reply_draft": r.fixed_reply, "decision": None}
        except GatewayProtocolError as exc:
            r = route_evaluate_error(exc.status or 0, "PROTOCOL", state.get("replan_count", 0))
            from services.agent.app.graph.routing import route_error

            err = route_error(exc.status, "PROTOCOL", "evaluate", "PROTOCOL")
            return {**r.state, "route_error": err, "reply_draft": r.fixed_reply, "decision": None}

        if decision is None:
            raise PortFailure("evaluate port returned None")
        # GatewayDecision → new-style wire model; store as dict for state.
        # Also store plan_id for backward compat.
        return {
            "plan_id": decision.plan_id,
            "decision": decision.model_dump(mode="json"),
            "route_error": None,
        }

    # ── gateway-aware execute ─────────────────────────────────────────

    def execute(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
        try:
            result = ports.execute(state, runtime.context)
        except GatewayResponseError as exc:
            reentry = state.get("evaluate_reentry_count", 0)
            r = route_execute_error(
                exc.status,
                exc.code.value if hasattr(exc.code, "value") else str(exc.code),
                decision=exc.decision,
                evaluate_reentry_count=reentry,
            )
            from services.agent.app.graph.routing import route_error

            err = route_error(
                exc.status,
                exc.code.value if hasattr(exc.code, "value") else str(exc.code),
                "execute",
                "RESPONSE",
            )
            state_update: dict = {**r.state, "route_error": err, "reply_draft": r.fixed_reply}
            if exc.decision is not None:
                state_update["decision"] = exc.decision.model_dump(mode="json")
                state_update["plan_id"] = exc.decision.plan_id
            return state_update
        except GatewayTransportError:
            reentry = state.get("evaluate_reentry_count", 0)
            r = route_execute_error(0, "TRANSPORT", evaluate_reentry_count=reentry)
            from services.agent.app.graph.routing import route_error

            err = route_error(None, "TRANSPORT", "execute", "TRANSPORT")
            return {**r.state, "route_error": err, "reply_draft": r.fixed_reply}
        except GatewayProtocolError as exc:
            reentry = state.get("evaluate_reentry_count", 0)
            r = route_execute_error(exc.status or 0, "PROTOCOL", evaluate_reentry_count=reentry)
            from services.agent.app.graph.routing import route_error

            err = route_error(exc.status, "PROTOCOL", "execute", "PROTOCOL")
            return {**r.state, "route_error": err, "reply_draft": r.fixed_reply}

        if result is None:
            raise PortFailure("execute port returned None")

        # Distinguish 200 ExecutionResult vs 202 UnknownExecution.
        # GatewayClient.execute returns either ExecutionResult (from contracts)
        # or UnknownExecution.  Check for the UNKNOWN status.
        if hasattr(result, "status") and getattr(result, "status", None) == "UNKNOWN":
            r = route_execute_success(result)
            return {
                **r.state,
                "execution": {"status": "UNKNOWN", "plan_id": getattr(result, "plan_id", "")},
                "route_error": None,
                "reply_draft": r.fixed_reply,
            }

        # 200 success
        r = route_execute_success()
        return {
            **r.state,
            "execution": (
                result.model_dump(mode="json") if hasattr(result, "model_dump") else result
            ),
            "route_error": None,
        }

    # ── respond (with fixed-reply shortcut) ───────────────────────────

    def respond(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
        # If a fixed reply was set (error/template path), use it directly
        # without calling the LLM respond port.
        if state.get("reply_draft") and state.get("route_error") is not None:
            return {"reply_draft": state["reply_draft"]}
        draft = ports.respond(state, runtime.context)
        if draft is None:
            raise PortFailure("respond port returned None")
        return {"reply_draft": draft}

    # ── routing functions ─────────────────────────────────────────────

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
        err = state.get("route_error")
        if err is not None:
            # Error path: always respond (route_*_error already sets reply_draft)
            return "respond"
        decision_raw = state.get("decision")
        if decision_raw is None:
            return "respond"
        replan_count = state.get("replan_count", 0)
        from services.agent.app.gateway.wire import GatewayDecision

        try:
            gw_dec = GatewayDecision.model_validate(decision_raw)
        except Exception:
            return "respond"
        r = route_evaluate(gw_dec, replan_count)
        if r.use_fixed_reply:
            return "respond"
        return r.node

    def _route_await_approval(state: AgentState) -> str:
        return "execute" if state.get("approval_id") else "respond"

    # ── build ─────────────────────────────────────────────────────────

    builder = StateGraph(AgentState, context_schema=AgentContext, input_schema=AgentInput)
    builder.add_node("ingest", ingest)
    builder.add_node("understand", understand)
    builder.add_node("gather", _build_gather_node(ports))
    builder.add_node("plan", _build_plan_node(ports))
    builder.add_node("evaluate", evaluate)
    builder.add_node("await_approval", _build_await_approval())
    builder.add_node("execute", execute)
    builder.add_node("respond", respond)
    builder.add_node("verify_output", _build_verify_node(ports))

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


# ── Shared node builders (reused by both build_graph and build_gateway_graph) ─


def _build_gather_node(ports: Ports):
    def gather(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
        reads = ports.gather(state, runtime.context)
        if reads is None:
            raise PortFailure("gather port returned None")
        update: dict = {"reads": list(reads)}
        decision_raw = state.get("decision")
        if decision_raw is not None:
            value = (
                decision_raw.get("decision")
                if isinstance(decision_raw, dict)
                else (decision_raw.decision if hasattr(decision_raw, "decision") else None)
            )
            if value in (DecisionValue.REPLAN, "REPLAN"):
                update["replan_count"] = state.get("replan_count", 0) + 1
        return update

    return gather


def _build_plan_node(ports: Ports):
    def plan(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
        draft = ports.plan(state, runtime.context)
        if draft is None:
            raise PortFailure("plan port returned None")
        return {"plan_draft": draft}

    return plan


def _build_await_approval():
    def await_approval(
        state: AgentState, runtime: Runtime[AgentContext], config: RunnableConfig
    ) -> dict:
        context = runtime.context
        thread_id = (config.get("configurable") or {}).get("thread_id")
        # Identity check
        if thread_id != context.session_id:
            raise SessionMismatchError(
                f"thread_id {thread_id!r} does not match session {context.session_id!r}"
            )
        if state.get("customer_id") != context.customer_id:
            raise CrossCustomerError(
                f"thread bound to customer {state.get('customer_id')!r},"
                f" got {context.customer_id!r}"
            )
        if state.get("session_id") != context.session_id:
            raise SessionMismatchError(
                f"thread bound to session {state.get('session_id')!r}, got {context.session_id!r}"
            )
        decision_raw = state.get("decision")
        if isinstance(decision_raw, dict):
            approval_id = decision_raw.get("approval_id")
        elif hasattr(decision_raw, "approval_id"):
            approval_id = decision_raw.approval_id
        else:
            approval_id = None
        if approval_id is None:
            raise PortFailure("REQUIRE_APPROVAL decision without approval_id")
        answer = interrupt({"approval_id": approval_id})
        if answer != approval_id:
            return {"approval_id": None}
        return {"approval_id": approval_id}

    return await_approval


def _build_verify_node(ports: Ports):
    def verify_output(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
        final = ports.verify_output(state, runtime.context)
        if final is None:
            raise PortFailure("verify_output port returned None")
        return {"messages": [AIMessage(content=final)]}

    return verify_output
