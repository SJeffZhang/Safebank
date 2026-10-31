"""Bad read-call JSON gets bounded feedback, never execution or quota retries."""

import httpx
import pytest
from openai import APIStatusError

from services.agent.app.llm._common import LlmToolError
from services.agent.app.llm.gather import GatherAdapter, ReadResult
from tests.agent.llm_mock import (
    ScriptedLLM,
    malformed_tool_response,
    mixed_tool_response,
    text_response,
    tool_response,
)
from tests.agent.test_llm_adapters import build, ctx, plan_state


def test_actual_unquoted_product_id_response_recovers_without_executing_it():
    body = tool_response("get_holdings", {}).json()
    raw = '{"account_id": null, "product_id": prd_mmf}'
    body["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = raw
    scripted = ScriptedLLM(
        [
            httpx.Response(200, json=body),
            tool_response("get_holdings", {"account_id": None, "product_id": "prd_mmf"}),
            text_response("done"),
        ]
    )
    executed = []

    def execute(tool, args):
        executed.append((tool, args))
        return ReadResult(data={"holdings": []}, source_ref="src_verified_holdings")

    reads = GatherAdapter(build(scripted), execute)(plan_state(), ctx())
    assert executed == [("get_holdings", {"account_id": None, "product_id": "prd_mmf"})]
    assert [r.source_ref for r in reads] == ["src_verified_holdings"]
    assert len(scripted.requests) == 3
    assert raw not in str(scripted.requests[1]["body"]["messages"])


@pytest.mark.parametrize("mixed", [False, True])
def test_bad_read_call_json_recovers_without_executing_bad_batch(mixed):
    calls = []

    def execute(tool, args):
        calls.append((tool, args))
        return ReadResult(data={"holdings": []}, source_ref="src_actual_holdings")

    bad = (
        mixed_tool_response("get_accounts", {"history_days": 7})
        if mixed
        else malformed_tool_response("get_holdings")
    )
    scripted = ScriptedLLM(
        [
            bad,
            tool_response("get_holdings", {"account_id": None, "product_id": None}),
            text_response("done"),
        ]
    )
    reads = GatherAdapter(build(scripted), execute)(plan_state(), ctx())
    assert calls == [("get_holdings", {"account_id": None, "product_id": None})]
    assert [r.source_ref for r in reads] == ["src_actual_holdings"]
    assert len(scripted.requests) == 3
    feedback = scripted.requests[1]["body"]["messages"]
    assert feedback[-1]["role"] == "user"
    assert "JSON" in feedback[-1]["content"]
    assert "{not json" not in str(feedback)


def test_bad_json_exhausts_round_budget_without_extra_model_call():
    scripted = ScriptedLLM([malformed_tool_response("get_holdings") for _ in range(3)])
    calls = []
    with pytest.raises(LlmToolError, match="malformed"):
        GatherAdapter(build(scripted), lambda *a: calls.append(a))(plan_state(), ctx())
    assert len(scripted.requests) == 3
    assert calls == []


@pytest.mark.parametrize("status", [402, 403, 429])
def test_provider_error_stops_after_format_feedback_without_retry(status):
    scripted = ScriptedLLM(
        [
            malformed_tool_response("get_holdings"),
            httpx.Response(status, json={"error": {"message": "upstream unavailable"}}),
        ]
    )
    calls = []
    with pytest.raises(APIStatusError) as error:
        GatherAdapter(build(scripted), lambda *a: calls.append(a))(plan_state(), ctx())
    assert error.value.status_code == status
    assert len(scripted.requests) == 2
    assert calls == []


def test_malformed_non_read_tool_is_rejected_immediately():
    scripted = ScriptedLLM([malformed_tool_response("transfer")])
    calls = []
    with pytest.raises(LlmToolError):
        GatherAdapter(build(scripted), lambda *a: calls.append(a))(plan_state(), ctx())
    assert len(scripted.requests) == 1
    assert calls == []
