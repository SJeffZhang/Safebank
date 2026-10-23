"""New-input invocation boundary (C1a).

LangGraph merges a dict input into the thread's persisted state, so a bare
``graph.invoke({})`` would silently reuse the previous round's ``utterances``.  This
boundary is the single sanctioned way to start a new-input round: it fails closed on empty
utterances and keeps new input clearly distinct from resume (``Command(resume=...)``).
"""

from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph

from services.agent.app.graph.errors import EmptyUtterancesError
from services.agent.app.graph.state import AgentContext, UtteranceRef


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
