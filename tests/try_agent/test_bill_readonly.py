"""Driver outcomes for completed analysis, independent of any write Decision."""

from __future__ import annotations

import copy
import json

import httpx
import pytest

import scripts.try_agent as driver


def _analysis_values() -> dict:
    return {
        "understanding": {
            "intents": ["bill_report"],
            "needs_write": False,
            "unclear": False,
            "slots": [],
        },
        "reads": [
            {
                "tool": "get_spending_summary",
                "source_ref": "src_spending",
                "args": {"month": "2026-09", "account_id": None},
                "data": {
                    "month": "2026-09",
                    "total_cents": 644800,
                    "previous_total_cents": 133190,
                    "mom_change_cents": 511610,
                    "mom_change_bp": 38412,
                    "categories": [],
                    "daily": [],
                    "transfer_out_cents": 100000,
                },
                "untrusted_paths": [],
                "versions": {},
                "observed_at": "2026-10-15T09:00:00+08:00",
            }
        ],
        "parse_status": "NO_WRITE",
        "decision": None,
        "plan_id": None,
        "execution": None,
        "clarification_id": None,
        "clarification": None,
        "route_error": None,
        "reply_draft": "2026年9月消费支出6448.00元。",
    }


def _run(values: dict, monkeypatch, *, include_messages: bool = True) -> tuple[int, list[str]]:
    monkeypatch.setenv("FRONTEND_SERVICE_TOKEN", "synthetic-frontend-token")
    urls: list[str] = []
    responses = {
        "http://gateway:8200/auth/login": (200, {"app_token": "synthetic-app-token"}),
        "http://gateway:8200/sessions": (201, {"session_id": "ses_bill"}),
        "http://gateway:8200/sessions/ses_bill/utterances": (
            201,
            {
                "utterance": {"id": "utt_bill", "text": "给我出一份9月的账单报告"},
                "assistant_token": "synthetic-assistant-token",
            },
        ),
        "http://agent:8100/threads": (200, {"thread_id": "ses_bill"}),
    }
    reply = values.get("reply_draft") or ""
    sse = "event: values\ndata: " + json.dumps(values) + "\n\n"
    if include_messages:
        sse += "event: messages\ndata: " + json.dumps([{"type": "ai", "content": reply}]) + "\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        urls.append(url)
        if url == "http://agent:8100/threads/ses_bill/runs/stream":
            return httpx.Response(200, content=sse.encode(), request=request)
        assert request.method == "POST" and url in responses, (
            f"unexpected call: {request.method} {url}"
        )
        status, body = responses[url]
        return httpx.Response(status, json=body, request=request)

    config = driver.Config(
        gateway_url="http://gateway:8200",
        agent_url="http://agent:8100",
        customer_id="cus_zs",
        text="给我出一份9月的账单报告",
        pin="123456",
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = driver.run(config, client, [])
    return result, urls


def test_completed_bill_report_needs_no_write_decision(monkeypatch):
    """A verified read-only reply must complete without an artificial ALLOW/write."""
    result, urls = _run(_analysis_values(), monkeypatch)
    assert result == driver.EXIT_COMPLETED
    assert len(urls) == 5


@pytest.mark.parametrize(
    "field,value",
    [
        ("understanding", None),
        ("understanding", {"needs_write": "false", "unclear": False}),
        ("understanding", {"needs_write": True, "unclear": False}),
        ("parse_status", "INTENT_UNCLEAR"),
        ("reads", []),
        ("route_error", {"code": "DEPENDENCY_UNAVAILABLE"}),
        ("reply_draft", ""),
        ("decision", "unexpected-wire-decision"),
        ("execution", {"status": "UNKNOWN"}),
        ("plan_id", "pln_without_decision"),
    ],
)
def test_incomplete_or_ambiguous_state_is_not_report_success(field, value, monkeypatch):
    """A nonempty reply alone is insufficient evidence of completed analysis."""
    values = copy.deepcopy(_analysis_values())
    values[field] = value
    result, _ = _run(values, monkeypatch)
    assert result == driver.EXIT_UNKNOWN


def test_analysis_cannot_override_deny(monkeypatch):
    values = _analysis_values()
    values["decision"] = {"decision": "DENY", "plan_id": "pln_denied"}
    result, _ = _run(values, monkeypatch)
    assert result == driver.EXIT_DENY


def test_draft_without_final_message_is_not_report_success(monkeypatch):
    result, _ = _run(_analysis_values(), monkeypatch, include_messages=False)
    assert result == driver.EXIT_UNKNOWN


def test_deterministic_no_write_scope_does_not_invent_model_clarification(monkeypatch):
    values = _analysis_values()
    values["understanding"]["unclear"] = True
    result, _ = _run(values, monkeypatch)
    assert result == driver.EXIT_COMPLETED


def test_open_clarification_stays_clarify(monkeypatch):
    values = _analysis_values()
    values.update(
        reads=[],
        parse_status="CLARIFYING",
        clarification_id="clr_month",
        clarification={"required_fields": ["month"]},
        reply_draft="请补充月份。",
    )
    result, _ = _run(values, monkeypatch)
    assert result == driver.EXIT_CLARIFY


def test_actual_fixed_report_failure_after_verification_is_not_completed(monkeypatch):
    """R1: the fail-closed monthly formatter must not become driver success."""
    from services.agent.app.llm.respond import RespondAdapter
    from services.agent.app.llm.verify import OutputVerifier
    from tests.agent.test_bill_accuracy import NoInference, _context, _report_reads, _state

    state = _state()
    state["reads"] = _report_reads()[:-1]
    state["reply_draft"] = RespondAdapter(NoInference())(state, _context())
    final = OutputVerifier(NoInference())(state, _context())
    values = _analysis_values()
    values["reply_draft"] = final
    result, _ = _run(values, monkeypatch)
    assert result == driver.EXIT_UNKNOWN


@pytest.mark.parametrize("variant", ["canonical", "nfkc", "surrounding_space"])
def test_exact_reserved_analysis_error_is_not_completed(variant, monkeypatch):
    import unicodedata

    from services.agent.app.llm.fixed_templates import respond_template

    reply = respond_template("GENERIC_ERROR")
    if variant != "canonical":
        reply = unicodedata.normalize("NFKC", reply)
    if variant == "surrounding_space":
        reply = " \n" + reply + "\t "
    values = _analysis_values()
    values["reply_draft"] = reply
    result, _ = _run(values, monkeypatch)
    assert result == driver.EXIT_UNKNOWN


@pytest.mark.parametrize("quote", ["请求无法处理", "请求无法处理，请重新发起。未执行任何操作。"])
def test_legitimate_report_with_error_text_as_data_still_completes(quote, monkeypatch):
    values = _analysis_values()
    values["reply_draft"] = f"2026年9月消费6448.00元。流水备注中出现「{quote}」，仅作为数据展示。"
    result, _ = _run(values, monkeypatch)
    assert result == driver.EXIT_COMPLETED


@pytest.mark.parametrize(
    "decision,execution,expected",
    [
        ({"decision": "DENY", "plan_id": "pln_status"}, None, driver.EXIT_DENY),
        ({"decision": "REPLAN", "plan_id": "pln_status"}, None, driver.EXIT_REPLAN),
        ({"decision": "ALLOW", "plan_id": "pln_status"}, None, driver.EXIT_ALLOW_READY),
        (
            {"decision": "ALLOW", "plan_id": "pln_status"},
            {"status": "EXECUTED"},
            driver.EXIT_COMPLETED,
        ),
    ],
)
def test_fixed_error_reply_cannot_override_structured_decision(
    decision, execution, expected, monkeypatch
):
    from services.agent.app.llm.fixed_templates import respond_template

    values = _analysis_values()
    values.update(
        decision=decision, execution=execution, reply_draft=respond_template("GENERIC_ERROR")
    )
    result, _ = _run(values, monkeypatch)
    assert result == expected


def test_fixed_error_reply_cannot_override_actual_clarification(monkeypatch):
    from services.agent.app.llm.fixed_templates import respond_template

    values = _analysis_values()
    values.update(clarification_id="clr_actual", reply_draft=respond_template("GENERIC_ERROR"))
    result, _ = _run(values, monkeypatch)
    assert result == driver.EXIT_CLARIFY
