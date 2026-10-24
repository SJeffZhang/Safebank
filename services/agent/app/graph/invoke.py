"""New-input invocation boundary (C1a/C1c2).

LangGraph merges a dict input into the thread's persisted state, so a bare
``graph.invoke({})`` would silently reuse the previous round's ``utterances``.  This
boundary is the single sanctioned way to start a new-input round: it fails closed on empty
utterances and keeps new input clearly distinct from resume (``Command(resume=...)``).

C1c2 adds ``invoke_gateway_input`` for gateway-backed graphs: the caller provides only
utterance IDs (no text/scope), and G20 provides the authoritative data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from services.agent.app.graph.errors import EmptyUtterancesError
from services.agent.app.graph.state import AgentContext, UtteranceRef

if TYPE_CHECKING:
    from services.agent.app.gateway.client import GatewayClient


def invoke_new_input(
    graph: CompiledStateGraph,
    utterances: list[UtteranceRef],
    *,
    config: RunnableConfig,
    context: AgentContext,
):
    if not utterances:
        raise EmptyUtterancesError("a new input round requires at least one utterance")
    return graph.invoke({"utterances": list(utterances)}, config=config, context=context)


def invoke_gateway_input(
    graph: CompiledStateGraph,
    utterance_ids: tuple[str, ...],
    *,
    config: RunnableConfig,
    context: AgentContext,
    client: GatewayClient,
):
    """Start a new-input round for a gateway-backed graph.

    Only utterance IDs are accepted — G20 provides authoritative text/kind/parse_status.
    The caller must NOT pre-populate text or scope.

    Internally, G20 is called to fetch the authoritative utterances, then the graph
    is invoked with id-only ``UtteranceRef`` entries.  The graph's ingest node will
    independently call G20 again (to be the authoritative source within the graph),
    but this pre-fetch provides an early validation layer.
    """
    if not utterance_ids:
        raise EmptyUtterancesError("a new input round requires at least one utterance id")

    # Pre-fetch G20 as a validation step: if L2 rejects the IDs, fail early.
    # The graph's ingest node will call G20 again as its own authority.
    _ = client.get_utterances(utterance_ids, context)

    # Create id-only utterance refs; text/kind will be replaced by G20 in ingest.
    id_only_refs = [UtteranceRef(id=uid, kind="typed", text="") for uid in utterance_ids]

    return graph.invoke(
        {"utterances": id_only_refs},
        config=config,
        context=context,
    )
