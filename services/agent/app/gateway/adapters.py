"""C1c2 gateway-backed ports factory and ingest adapter.

ASSEMBLES ``Ports`` whose ``evaluate`` and ``execute`` call the C1c1
``GatewayClient`` instead of fake stubs.  Also provides ``ingest_via_gateway``
for the graph's ingest node to call G20/G21.

G4 (approval panel / build_view / create_for_evaluation) and G8 (full execution
pipeline) are not yet implemented in L2 as of 2026-10-24.  ``evaluate`` returns
a ``GatewayDecision`` without a valid view_hash/approval binding, and
``execute`` only receives an ``EXECUTING``/``UNKNOWN`` placeholder.
These fake boundaries are clearly labelled in docstrings and never claimed as
real three-tier integration.
"""

from __future__ import annotations

from services.agent.app.gateway.client import GatewayClient
from services.agent.app.gateway.errors import (
    GatewayClientError,
)
from services.agent.app.gateway.wire import GatewayClarification, GatewayDecision, GatewayUtterance
from services.agent.app.graph.ports import Ports
from services.agent.app.graph.state import (
    AgentContext,
    AgentState,
)

# ── make_gateway_ports ──────────────────────────────────────────────────


def make_gateway_ports(
    client: GatewayClient,
    *,
    understand,
    gather,
    plan,
    respond,
    verify_output,
) -> Ports:
    """Create ``Ports`` whose evaluate/execute are backed by ``GatewayClient``.

    The other ports (understand/gather/plan/respond/verify_output) are passed
    through unchanged — they are the C1b LLM adapters, now bridged to use
    gateway data instead of fake text/scope.
    """
    return Ports(
        understand=understand,
        gather=gather,
        plan=plan,
        evaluate=_GatewayEvaluate(client),
        execute=_GatewayExecute(client),
        respond=respond,
        verify_output=verify_output,
    )


# ── Gateway evaluate port ───────────────────────────────────────────────


class _GatewayEvaluate:
    """Calls ``GatewayClient.evaluate`` and returns ``GatewayDecision``.

    On ``GatewayResponseError`` the exception is raised into the graph node
    which catches it and routes via ``route_evaluate_error``.  This port
    itself never swallows errors — the graph node owns routing.
    """

    def __init__(self, client: GatewayClient) -> None:
        self._client = client

    def __call__(self, state: AgentState, context: AgentContext) -> GatewayDecision:
        draft = state.get("plan_draft")
        if draft is None:
            # This should not happen — plan always runs before evaluate.
            raise ValueError("missing plan_draft before evaluate")
        return self._client.evaluate(draft, context)


# ── Gateway execute port ────────────────────────────────────────────────


class _GatewayExecute:
    """Calls ``GatewayClient.execute`` and returns ``ExecutionResult`` or
    ``UnknownExecution``.

    On ``GatewayResponseError`` the exception is raised into the graph node
    which catches it and routes via ``route_execute_error``.  Transport errors
    are converted to a ``GatewayResponseError``-like handling by the graph.
    """

    def __init__(self, client: GatewayClient) -> None:
        self._client = client

    def __call__(self, state: AgentState, context: AgentContext):
        plan_id = state.get("plan_id")
        if plan_id is None:
            raise ValueError("missing plan_id before execute")
        request_id = (
            state.get("plan_draft", {}).request_id
            if hasattr(state.get("plan_draft", {}), "request_id")
            else "req_unknown"
        )
        if hasattr(state.get("plan_draft", {}), "request_id"):
            request_id = state["plan_draft"].request_id  # type: ignore[union-attr]
        return self._client.execute(plan_id, request_id, context)


# ── G20/G21 ingest helper ───────────────────────────────────────────────


def ingest_via_gateway(
    client: GatewayClient,
    utterance_ids: tuple[str, ...],
    *,
    context: AgentContext,
    session_id: str,
) -> tuple[tuple[GatewayUtterance, ...], GatewayClarification | None, list[str]]:
    """Call G20 (get_utterances) and G21 (get_clarification) for a new input.

    Returns:
        - utterances: authoritative utterance list from L2
        - clarification: current open clarification (None if none)
        - warnings: non-fatal issues (e.g. clarification parent mismatch) that
          should not block routing but may affect prompt construction
    """
    utts = client.get_utterances(utterance_ids, context)

    try:
        clarification = client.get_clarification(context)
    except GatewayClientError:
        clarification = None

    warnings: list[str] = []

    # Clarification parent must be among the current G20 utterance IDs.
    if clarification is not None:
        for parent_id in clarification.parent_utterance_ids:
            if parent_id not in utterance_ids:
                warnings.append(
                    f"clarification {clarification.id} parent {parent_id} "
                    f"not in current utterance ids — clarification ignored"
                )
                clarification = None
                break

    return utts, clarification, warnings


def gateway_utterance_to_ref(utt: GatewayUtterance):
    """Convert a G20 ``GatewayUtterance`` to an L1-only ``UtteranceRef``.

    ``text`` may be null for hint/action; the ref preserves ``kind`` as a
    plain string, ``text`` defaults to ``""`` for null (safe for LLM prompts).
    ``scope`` and ``parse_status`` are carried into state for routing purposes
    but never treated as authorization by L1.
    """
    from services.agent.app.graph.state import UtteranceRef

    return UtteranceRef(
        id=utt.id,
        kind=utt.kind if isinstance(utt.kind, str) else "typed",
        text=utt.text or "",
    )


# ── Gather read bridge ──────────────────────────────────────────────────


def make_gateway_read_executor(client: GatewayClient):
    """Create a ``ReadExecutor`` callable that bridges through the gateway.

    Returns ``(tool_name, args) -> ReadResult`` using ``client.read_tool``.
    The executor takes ``AgentContext`` via closure; context is bound when
    the executor is called from within a gateway graph invocation.
    """

    from services.agent.app.llm.gather import ReadResult as LlmReadResult

    def _executor(context: AgentContext):
        def read(tool: str, args: dict) -> LlmReadResult:
            envelope = client.read_tool(tool, args, context)
            return LlmReadResult(
                data=envelope.data,
                source_ref=envelope.source_ref,
                untrusted_paths=envelope.untrusted_paths,
                versions=dict(envelope.versions),
                observed_at=envelope.observed_at,
            )

        return read

    return _executor
