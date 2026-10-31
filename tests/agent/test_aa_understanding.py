"""Explicit AA collection commands cannot silently take the read-only route."""

from copy import deepcopy

import httpx
import pytest
from openai import APIStatusError

from services.agent.app.graph.state import UtteranceRef
from services.agent.app.llm._common import LlmToolError
from services.agent.app.llm.understand import UnderstandAdapter
from tests.agent.llm_mock import ScriptedLLM, tool_response
from tests.agent.test_llm_adapters import body_of, build, ctx

TEXT = "昨晚聚餐400元，我和小林、小周、小陈AA"
ACTUAL_READ_ONLY = {
    "intents": ["split_bill"],
    "needs_write": False,
    "unclear": False,
    "slots": [
        {"intent_index": 0, "name": "amount", "value": "400", "span": [5, 8]},
        {
            "intent_index": 0,
            "name": "participants",
            "value": "我,小林,小周,小陈",
            "span": [12, 22],
        },
        {"intent_index": 0, "name": "split_type", "value": "AA", "span": [23, 25]},
    ],
}
MODEL_WRITE = {
    "intents": ["create_split_request"],
    "needs_write": True,
    "unclear": False,
    "slots": [
        {"intent_index": 0, "name": "amount_cents", "value": "40000", "span": [4, 8]},
        {"intent_index": 0, "name": "participants", "value": "小林、小周、小陈", "span": [11, 19]},
    ],
}


def state(text=TEXT):
    # L1 UtteranceRef intentionally has no scope/action-kind fields.
    return {
        "utterances": [UtteranceRef(id="utt_aa", kind="typed", text=text)],
        "parse_status": "SCOPED",
        "clarification_id": None,
        "clarification": None,
    }


@pytest.mark.parametrize(
    "text",
    [
        TEXT,
        "昨晚聚餐128.50元，我和小林、小周AA",
        "昨晚聚餐300元，我和小林AA",
        "昨晚聚餐100元，我和小林、小周、小陈、小李AA",
    ],
)
def test_scoped_explicit_aa_false_output_uses_existing_regeneration(text):
    current = state(text)
    original = deepcopy(current)
    scripted = ScriptedLLM(
        [
            tool_response("report_understanding", ACTUAL_READ_ONLY),
            tool_response("report_understanding", MODEL_WRITE),
        ]
    )
    result = UnderstandAdapter(build(scripted))(current, ctx())
    assert len(scripted.requests) == 2
    # The adapter returns the complete actual model output, not a patched bool or derived slots.
    assert result.model_dump(mode="json") == MODEL_WRITE
    assert current == original
    request = body_of(scripted, 1)
    assert [m["role"] for m in request["messages"]] == ["system", "user"]
    feedback = request["messages"][1]["content"].removeprefix(text)
    assert "create_split_request" in feedback and "needs_write" in feedback
    assert "40000" not in feedback and "小林" not in feedback and "split_bill" not in feedback


def test_repeated_false_aa_outputs_exhaust_once_and_fail_explicitly():
    scripted = ScriptedLLM([tool_response("report_understanding", ACTUAL_READ_ONLY)] * 2)
    with pytest.raises(LlmToolError, match="AA"):
        UnderstandAdapter(build(scripted))(state(), ctx())
    assert len(scripted.requests) == 2


def test_aa_semantic_retry_shares_the_schema_retry_budget():
    scripted = ScriptedLLM(
        [
            tool_response(
                "report_understanding", {"intents": "not-an-array", "needs_write": False}
            ),
            tool_response("report_understanding", ACTUAL_READ_ONLY),
        ]
    )
    with pytest.raises(LlmToolError, match="AA"):
        UnderstandAdapter(build(scripted))(state(), ctx())
    assert len(scripted.requests) == 2


@pytest.mark.parametrize(
    "text",
    [
        "昨晚聚餐400元，我和小林、小周、小陈AA？",
        "请问昨晚聚餐400元，我和小林、小周、小陈AA吗",
        "计算昨晚聚餐400元，我和小林、小周、小陈AA",
        "昨晚聚餐400元，我和小林、小周、小陈AA每人多少",
        "查看昨晚聚餐400元，我和小林、小周、小陈AA进度",
        "“昨晚聚餐400元，我和小林、小周、小陈AA”",
        "他说‘昨晚聚餐400元，我和小林、小周、小陈AA’",
        "昨晚聚餐400元，我和小林、小周、小陈AA，然后转账100元",
        "给小林400元，我和小林、小周AA",
        "今天餐费300元，我和小林AA",
        "这次饭钱100元，小林、小周分摊",
        "昨晚聚餐400元，小林、小周、小陈AA",
        "昨晚聚餐400元，我和小林、小周、小陈AA，帮我看看",
    ],
)
def test_aa_questions_quotes_calculations_and_mixed_requests_stay_read_only(text):
    scripted = ScriptedLLM([tool_response("report_understanding", ACTUAL_READ_ONLY)])
    result = UnderstandAdapter(build(scripted))(state(text), ctx())
    assert result.model_dump(mode="json") == ACTUAL_READ_ONLY
    assert len(scripted.requests) == 1


@pytest.mark.parametrize(
    "boundary",
    [
        "unknown_parse",
        "no_write",
        "unclear",
        "clarifying",
        "context",
        "hint",
        "action",
        "clarification_id",
        "clarification",
        "multiple_typed",
        "typed_with_context",
        "empty",
    ],
)
def test_aa_retry_requires_one_typed_scoped_request_without_clarification(boundary):
    current = state()
    if boundary in ("unknown_parse", "no_write", "unclear", "clarifying"):
        current["parse_status"] = {
            "unknown_parse": None,
            "no_write": "NO_WRITE",
            "unclear": "INTENT_UNCLEAR",
            "clarifying": "CLARIFYING",
        }[boundary]
    elif boundary in ("context", "hint", "action"):
        current["utterances"] = [UtteranceRef(id="utt_aa", kind=boundary, text=TEXT)]
    elif boundary == "clarification_id":
        current["clarification_id"] = "clr_actual"
    elif boundary == "clarification":
        current["clarification"] = {"required_fields": ["participant_payee_ids"]}
    elif boundary in ("multiple_typed", "typed_with_context"):
        current["utterances"].append(
            UtteranceRef(
                id="utt_extra",
                kind="typed" if boundary == "multiple_typed" else "context",
                text=TEXT,
            )
        )
    else:
        current["utterances"] = []
    scripted = ScriptedLLM([tool_response("report_understanding", ACTUAL_READ_ONLY)])
    result = UnderstandAdapter(build(scripted))(current, ctx())
    assert result.model_dump(mode="json") == ACTUAL_READ_ONLY
    assert len(scripted.requests) == 1


def test_valid_aa_write_proposal_is_not_replaced_or_retried():
    scripted = ScriptedLLM([tool_response("report_understanding", MODEL_WRITE)])
    result = UnderstandAdapter(build(scripted))(state(), ctx())
    assert result.model_dump(mode="json") == MODEL_WRITE
    assert len(scripted.requests) == 1


@pytest.mark.parametrize("status", [402, 403, 429])
def test_aa_regeneration_quota_failure_stops_instead_of_falling_back(status):
    scripted = ScriptedLLM(
        [
            tool_response("report_understanding", ACTUAL_READ_ONLY),
            httpx.Response(status, json={"error": {"message": "quota", "type": "quota"}}),
        ]
    )
    with pytest.raises(APIStatusError) as caught:
        UnderstandAdapter(build(scripted))(state(), ctx())
    assert caught.value.status_code == status
    assert len(scripted.requests) == 2
