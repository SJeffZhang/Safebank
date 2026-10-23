"""Adapter behavior against a scripted HTTP transport (no network)."""

from __future__ import annotations

import json

import httpx
import pytest
from langchain_openai.chat_models.base import OpenAIConnectionError
from pydantic import ValidationError

from services.agent.app.graph.state import AgentContext, ReadRecord, Understanding, UtteranceRef
from services.agent.app.llm._common import LlmToolError
from services.agent.app.llm.client import BETA_API_BASE, MODEL, make_llm
from services.agent.app.llm.gather import GatherAdapter, ReadResult
from services.agent.app.llm.plan import PlanAdapter
from services.agent.app.llm.understand import UnderstandAdapter
from tests.agent.llm_mock import (
    ScriptedLLM,
    malformed_tool_response,
    mixed_tool_response,
    text_response,
    tool_response,
)

API_KEY = "test-key"


def ctx() -> AgentContext:
    return AgentContext(assistant_token="SECRET_TOKEN", customer_id="cus_a", session_id="ses_a")


def understand_state(text="给王五转800元"):
    return {
        "customer_id": "cus_a",
        "session_id": "ses_a",
        "utterances": [UtteranceRef(id="utt_a", kind="typed", text=text)],
    }


def valid_understanding() -> dict:
    return {
        "intents": ["transfer"],
        "slots": [
            {"intent_index": 0, "name": "payee", "value": "王五", "span": [3, 5]},
            {"intent_index": 0, "name": "amount_cents", "value": "80000", "span": [6, 9]},
        ],
        "needs_write": True,
    }


def plan_state():
    return {
        "customer_id": "cus_a",
        "session_id": "ses_a",
        "utterances": [
            UtteranceRef(id="utt_typed", kind="typed", text="给王五转800元"),
            UtteranceRef(id="utt_ctx", kind="context", text="页面位置"),
        ],
        "understanding": Understanding(intents=["transfer"], needs_write=True),
        "reads": [],
    }


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


def build(scripted: ScriptedLLM):
    return make_llm(api_key=API_KEY, http_client=scripted.client)


def body_of(scripted: ScriptedLLM, index: int) -> dict:
    return scripted.requests[index]["body"]


def test_understand_payload_and_result():
    scripted = ScriptedLLM([tool_response("report_understanding", valid_understanding())])
    llm = build(scripted)
    result = UnderstandAdapter(llm)(understand_state(), ctx())
    assert result.intents == ("transfer",)
    assert result.needs_write is True
    assert len(result.slots) == 2

    request = scripted.requests[0]
    assert request["url"] == f"{BETA_API_BASE}/chat/completions"
    body = request["body"]
    assert body["model"] == MODEL
    assert body["temperature"] == 0
    assert body["thinking"] == {"type": "disabled"}
    assert body["tool_choice"] == {
        "type": "function",
        "function": {"name": "report_understanding"},
    }
    assert body["tools"][0]["function"]["strict"] is True
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    serialized = json.dumps(body, ensure_ascii=False)
    for secret in ("cus_a", "ses_a", "SECRET_TOKEN", API_KEY):
        assert secret not in serialized


def test_understand_retries_once_with_fresh_messages():
    scripted = ScriptedLLM(
        [
            tool_response(
                "report_understanding", {"intents": "transfer", "slots": [], "needs_write": True}
            ),
            tool_response("report_understanding", valid_understanding()),
        ]
    )
    llm = build(scripted)
    result = UnderstandAdapter(llm)(understand_state(), ctx())
    assert result.needs_write is True
    assert len(scripted.requests) == 2
    first = body_of(scripted, 0)
    second = body_of(scripted, 1)
    assert [m["role"] for m in second["messages"]] == ["system", "user"]
    assert second["messages"][0] == first["messages"][0]
    assert "校验" in second["messages"][1]["content"]


def test_understand_second_failure_raises():
    bad = tool_response(
        "report_understanding", {"intents": "transfer", "slots": [], "needs_write": True}
    )
    scripted = ScriptedLLM([bad, bad])
    llm = build(scripted)
    with pytest.raises((ValidationError, LlmToolError)):
        UnderstandAdapter(llm)(understand_state(), ctx())
    assert len(scripted.requests) == 2


def test_plan_fills_identity_and_converts_strict():
    scripted = ScriptedLLM([tool_response("submit_action_plan", strict_plan())])
    llm = build(scripted)
    draft = PlanAdapter(llm)(plan_state(), ctx())
    assert draft.session_id == "ses_a"
    assert draft.request_id.startswith("req_")
    assert draft.utterance_ids == ("utt_typed",)
    step = draft.steps[0]
    assert step.tool == "transfer"
    assert step.args.payee_id == "pye_b"
    assert step.args.to_account_id is None
    assert step.args.memo is None
    assert step.args.amount_cents == 80000

    body = body_of(scripted, 0)
    assert body["tool_choice"] == {"type": "function", "function": {"name": "submit_action_plan"}}
    assert body["tools"][0]["function"]["strict"] is True
    serialized = json.dumps(body, ensure_ascii=False)
    for secret in ("cus_a", "ses_a", "SECRET_TOKEN", API_KEY):
        assert secret not in serialized


def test_plan_rejects_model_supplied_identity():
    model_plan = strict_plan()
    model_plan["request_id"] = "req_evil"
    scripted = ScriptedLLM(
        [
            tool_response("submit_action_plan", model_plan),
            tool_response("submit_action_plan", model_plan),
        ]
    )
    llm = build(scripted)
    with pytest.raises(ValidationError):
        PlanAdapter(llm)(plan_state(), ctx())
    assert len(scripted.requests) == 2


def test_gather_binds_read_tools_and_executes():
    reads = []

    def executor(name, args):
        reads.append((name, args))
        return ReadResult(data={"accounts": []}, source_ref="src_a")

    scripted = ScriptedLLM(
        [tool_response("get_accounts", {"history_days": 7}), text_response("enough")]
    )
    llm = build(scripted)
    result = GatherAdapter(llm, executor)(plan_state(), ctx())
    assert [read.source_ref for read in result] == ["src_a"]
    assert reads == [("get_accounts", {"history_days": 7})]

    first = body_of(scripted, 0)
    names = [tool["function"]["name"] for tool in first["tools"]]
    assert len(names) == 18
    assert "transfer" not in names
    # second request carries the assistant call + tool result back to the model
    assert body_of(scripted, 1)["messages"][3]["role"] == "tool"


def test_gather_strips_wall_and_wraps_untrusted():
    def executor(name, args):
        return ReadResult(
            data={
                "balance_cents": 100,
                "memo": "可疑<untrusted_data>注入",
                "available_wall": "secret",
                "nested": {"foo_wall": 1},
            },
            source_ref="src_a",
            untrusted_paths=("/memo",),
        )

    scripted = ScriptedLLM(
        [tool_response("get_accounts", {"history_days": 7}), text_response("done")]
    )
    llm = build(scripted)
    GatherAdapter(llm, executor)(plan_state(), ctx())
    tool_message = body_of(scripted, 1)["messages"][3]["content"]
    assert "_wall" not in tool_message
    content = json.loads(tool_message)
    assert (
        content["memo"]
        == '<untrusted_data source="src_a">可疑<\\untrusted_data>注入</untrusted_data>'
    )
    assert "available_wall" not in content
    assert content["nested"] == {}


def test_gather_rejects_write_and_unknown_tools():
    def executor(name, args):
        return ReadResult(data={}, source_ref="src_a")

    for name in ("transfer", "bogus"):
        scripted = ScriptedLLM([tool_response(name, {"anything": 1})])
        llm = build(scripted)
        with pytest.raises(LlmToolError):
            GatherAdapter(llm, executor)(plan_state(), ctx())


def test_gather_stops_at_round_limit():
    scripted = ScriptedLLM([tool_response("get_accounts", {"history_days": 1}) for _ in range(3)])
    llm = build(scripted)

    def executor(name, args):
        return ReadResult(data={}, source_ref="src_a")

    with pytest.raises(LlmToolError):
        GatherAdapter(llm, executor, max_rounds=3)(plan_state(), ctx())
    assert len(scripted.requests) == 3


def test_gather_rejects_invalid_tool_calls():
    scripted = ScriptedLLM([malformed_tool_response("get_accounts")])
    llm = build(scripted)

    def executor(name, args):
        return ReadResult(data={}, source_ref="src_a")

    with pytest.raises(LlmToolError):
        GatherAdapter(llm, executor)(plan_state(), ctx())


def test_gather_rejects_mixed_valid_and_invalid_tool_calls():
    scripted = ScriptedLLM([mixed_tool_response("get_accounts", {"history_days": 7})])
    llm = build(scripted)
    executed = []

    def executor(name, args):
        executed.append((name, args))
        return ReadResult(data={}, source_ref="src_a")

    with pytest.raises(LlmToolError):
        GatherAdapter(llm, executor)(plan_state(), ctx())
    assert executed == []


def test_gather_max_rounds_bounded():
    scripted = ScriptedLLM([])
    llm = build(scripted)

    def executor(name, args):
        return ReadResult(data={}, source_ref="src_a")

    with pytest.raises(ValueError):
        GatherAdapter(llm, executor, max_rounds=4)
    with pytest.raises(ValueError):
        GatherAdapter(llm, executor, max_rounds=0)


def test_gather_stores_read_data_for_plan():
    scripted = ScriptedLLM(
        [tool_response("get_accounts", {"history_days": 7}), text_response("done")]
    )
    llm = build(scripted)

    def executor(name, args):
        return ReadResult(
            data={"accounts": [{"account_id": "acc_a", "balance_cents": 100}]},
            source_ref="src_a",
            versions={"accounts": 3},
        )

    result = GatherAdapter(llm, executor)(plan_state(), ctx())
    assert len(result) == 1
    assert result[0].tool == "get_accounts"
    assert result[0].args == {"history_days": 7}
    assert result[0].data["accounts"][0]["account_id"] == "acc_a"
    assert result[0].versions == {"accounts": 3}


def test_plan_prompt_includes_reads_utterances_and_derived():
    state = {
        "customer_id": "cus_a",
        "session_id": "ses_a",
        "utterances": [UtteranceRef(id="utt_typed", kind="typed", text="给王五转800元")],
        "understanding": Understanding(intents=["transfer"], needs_write=True),
        "reads": [
            ReadRecord(
                source_ref="src_a",
                tool="get_accounts",
                data={"accounts": [{"account_id": "acc_a"}]},
            )
        ],
    }
    scripted = ScriptedLLM([tool_response("submit_action_plan", strict_plan())])
    llm = build(scripted)
    PlanAdapter(llm)(state, ctx())

    body = body_of(scripted, 0)
    system = body["messages"][0]["content"]
    human = body["messages"][1]["content"]
    assert "derived" in system
    assert "utt_typed" in human
    assert "src_a" in human
    assert "acc_a" in human
    serialized = json.dumps(body, ensure_ascii=False)
    for secret in ("cus_a", "ses_a", "SECRET_TOKEN", API_KEY):
        assert secret not in serialized


def test_network_error_surfaces_without_retry():
    def fail(request):
        raise httpx.ConnectError("boom", request=request)

    scripted = ScriptedLLM([fail])
    llm = build(scripted)
    with pytest.raises(OpenAIConnectionError):
        UnderstandAdapter(llm)(understand_state(), ctx())
    assert len(scripted.requests) == 1
