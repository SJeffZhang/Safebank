"""Formal subscription-year query: bounded repair and safe SSE error payload."""

import json

import pytest
from pydantic import ValidationError

from services.agent.app import main
from services.agent.app.graph.state import Understanding
from services.agent.app.llm.understand import UnderstandAdapter
from tests.agent.test_llm_adapters import (
    ScriptedLLM,
    body_of,
    build,
    ctx,
    tool_response,
    understand_state,
)

TEXT = "我有哪些订阅，一年总共要花多少"


def bad():
    return {
        "intents": ["subscriptions"],
        "slots": [{"intent_index": 0, "name": "year", "value": "1", "span": [0, 0]}],
        "needs_write": False,
    }


def invalid_error():
    with pytest.raises(ValidationError) as caught:
        Understanding.model_validate(bad())
    return caught.value


def test_invalid_span_gets_actionable_sanitized_one_retry_for_original_query():
    scripted = ScriptedLLM(
        [
            tool_response("report_understanding", bad()),
            tool_response(
                "report_understanding",
                {"intents": ["subscriptions"], "slots": [], "needs_write": False},
            ),
        ]
    )
    result = UnderstandAdapter(build(scripted))(understand_state(TEXT), ctx())
    assert not result.needs_write and len(scripted.requests) == 2
    repair = body_of(scripted, 1)["messages"][-1]["content"]
    assert "slots.0.span" in repair and "start" in repair and "end" in repair
    assert "input_value" not in repair and "errors.pydantic.dev" not in repair


def test_two_invalid_outputs_stop_at_existing_budget_without_fabricated_span():
    scripted = ScriptedLLM([tool_response("report_understanding", bad())] * 2)
    with pytest.raises(ValidationError):
        UnderstandAdapter(build(scripted))(understand_state(TEXT), ctx())
    assert len(scripted.requests) == 2


def test_validation_error_public_payload_omits_internal_fields_and_links():
    payload = main._stream_error_payload(invalid_error())
    text = json.dumps(payload, ensure_ascii=False)
    assert payload["code"] == "MODEL_OUTPUT_INVALID"
    assert "实际业务状态" in payload["message"]
    assert all(
        s not in text for s in ["ValidationError", "input_value", "errors.pydantic.dev", "slots.0"]
    )


def test_unknown_exception_text_is_not_exposed_to_client():
    payload = main._stream_error_payload(
        RuntimeError("CANARY_PRIVATE_input_value secret stacktrace")
    )
    text = json.dumps(payload)
    assert payload["code"] == "AGENT_REQUEST_FAILED"
    assert "CANARY" not in text and "stacktrace" not in text


@pytest.mark.parametrize("status", [402, 403, 429])
def test_provider_limits_keep_explicit_stop_status_without_raw_message(status):
    error = RuntimeError("CANARY_PROVIDER_BODY")
    error.status_code = status
    payload = main._stream_error_payload(error)
    assert payload["code"] == "PROVIDER_LIMIT_REACHED" and payload["status_code"] == status
    assert "CANARY" not in json.dumps(payload)


def test_extra_key_error_location_is_never_echoed_in_repair_feedback():
    output = {"intents": [], "slots": [], "needs_write": False, "CANARY_PRIVATE_KEY": "secret"}
    scripted = ScriptedLLM(
        [
            tool_response("report_understanding", output),
            tool_response(
                "report_understanding", {"intents": [], "slots": [], "needs_write": False}
            ),
        ]
    )
    UnderstandAdapter(build(scripted))(understand_state(TEXT), ctx())
    assert "CANARY" not in body_of(scripted, 1)["messages"][-1]["content"]


@pytest.mark.parametrize("status", [402, 403, 429])
def test_understanding_provider_limit_stops_without_repair(status):
    class Limited:
        calls = 0

        def bind_tools(self, *args, **kwargs):
            return self

        def invoke(self, messages):
            self.calls += 1
            error = RuntimeError("private provider body")
            error.status_code = status
            raise error

    model = Limited()
    with pytest.raises(RuntimeError):
        UnderstandAdapter(model)(understand_state(TEXT), ctx())
    assert model.calls == 1
