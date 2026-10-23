from dataclasses import dataclass
from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt


class State(TypedDict):
    approved: bool


@dataclass
class Context:
    assistant_token: str


def test_interrupt_resume_and_runtime_context_not_checkpointed():
    sentinel = "TEST_ONLY_RUNTIME_SECRET"

    def approval(state: State, runtime: Runtime[Context]) -> dict:
        assert runtime.context.assistant_token == sentinel
        answer = interrupt({"approval_id": "apv_test"})
        return {"approved": answer == "apv_test"}

    saver = InMemorySaver()
    graph = StateGraph(State, context_schema=Context)
    graph.add_node("approval", approval)
    graph.add_edge(START, "approval")
    graph.add_edge("approval", END)
    app = graph.compile(checkpointer=saver)
    config = {"configurable": {"thread_id": "ses_test"}}
    first = app.invoke({"approved": False}, config, context=Context(sentinel))
    assert first["__interrupt__"][0].value["approval_id"] == "apv_test"
    result = app.invoke(Command(resume="apv_test"), config, context=Context(sentinel))
    assert result["approved"]
    for checkpoint in saver.list(config):
        assert sentinel not in repr(checkpoint)
    assert sentinel.encode() not in repr(saver.storage).encode()
