"""One end-to-end pass wiring the C1b adapters into the C1a graph (mock transport)."""

from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver
from safebank_contracts.plan import ExecutionResult

from services.agent.app.graph import (
    AgentContext,
    Decision,
    Ports,
    UtteranceRef,
    build_graph,
    invoke_new_input,
)
from services.agent.app.llm.client import make_llm
from services.agent.app.llm.gather import GatherAdapter, ReadResult
from services.agent.app.llm.plan import PlanAdapter
from services.agent.app.llm.understand import UnderstandAdapter
from tests.agent.llm_mock import ScriptedLLM, text_response, tool_response


def ctx() -> AgentContext:
    return AgentContext(assistant_token="SECRET_TOKEN", customer_id="cus_a", session_id="ses_a")


def valid_understanding() -> dict:
    return {"intents": ["transfer"], "slots": [], "needs_write": True}


def strict_plan() -> dict:
    return {
        "intents": ["transfer"],
        "steps": [
            {
                "step_id": "s1",
                "tool": "transfer",
                "args": {
                    "from_account_id": "acc_a",
                    "payee_id": {"kind": "id", "id": "pye_b"},
                    "to_account_id": {"kind": "none"},
                    "amount_cents": 80000,
                    "memo": "__NULL__",
                },
                "depends_on": [],
                "arg_sources": [],
            }
        ],
        "rationale": "test",
    }


def test_c1b_adapters_drive_c1a_graph_end_to_end():
    scripted = ScriptedLLM(
        [
            tool_response("report_understanding", valid_understanding()),
            tool_response("get_accounts", {"history_days": 7}),
            text_response("enough"),
            tool_response("submit_action_plan", strict_plan()),
        ]
    )
    llm = make_llm(api_key="test-key", http_client=scripted.client)
    executor_calls = []

    def executor(name, args):
        executor_calls.append((name, args))
        return ReadResult(data={"accounts": [{"account_id": "acc_a"}]}, source_ref="src_acct")

    ports = Ports(
        understand=UnderstandAdapter(llm),
        gather=GatherAdapter(llm, executor),
        plan=PlanAdapter(llm),
        evaluate=lambda s, c: Decision(plan_id="pln_a", decision="ALLOW"),
        execute=lambda s, c: ExecutionResult(
            plan_id="pln_a",
            status="EXECUTED",
            steps=(),
            versions={},
            committed_at=None,
            error_code=None,
            replayed=False,
        ),
        respond=lambda s, c: "已执行",
        verify_output=lambda s, c: s["reply_draft"],
    )
    graph = build_graph(ports, InMemorySaver())
    result = invoke_new_input(
        graph,
        [UtteranceRef(id="utt_a", kind="typed", text="给王五转800元")],
        config={"configurable": {"thread_id": "ses_a"}},
        context=ctx(),
    )
    assert result["decision"].decision == "ALLOW"
    assert result["execution"].status == "EXECUTED"
    assert result["plan_id"] == "pln_a"
    assert executor_calls == [("get_accounts", {"history_days": 7})]
    assert len(scripted.requests) == 4
