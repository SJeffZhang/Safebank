"""Live scenario runner exit-code behavior against a scripted transport."""

from __future__ import annotations

import httpx
import pytest

from services.agent.app.llm.client import make_llm
from services.agent.app.llm.live import _BudgetTransport, run_scenarios
from tests.agent.llm_mock import ScriptedLLM, text_response, tool_response

UNDERSTANDING = {"intents": ["transfer"], "slots": [], "needs_write": True}

TRANSFER_TEXT = "给王五转800元"  # 8[4] 0[5] 0[6] 元[7]
LIMITS_TEXT = "把crd_zs_1的单笔限额降到1000分"  # 1[16] 0[17] 0[18] 0[19] 分[20]


def transfer_plan(payee="pye_zs_ww", amount=80000) -> dict:
    return {
        "intents": ["transfer"],
        "steps": [
            {
                "step_id": "s1",
                "tool": "transfer",
                "args": {
                    "from_account_id": "acc_zs_chk",
                    "payee_id": {"kind": "id", "id": payee},
                    "to_account_id": {"kind": "none"},
                    "amount_cents": amount,
                    "memo": "__NULL__",
                },
                "depends_on": [],
                "arg_sources": [
                    {
                        "param": "from_account_id",
                        "source": {
                            "kind": "read",
                            "ref": "src_syn_acct",
                            "pointer": "/accounts/0/account_id",
                        },
                    },
                    {
                        "param": "payee_id",
                        "source": {
                            "kind": "read",
                            "ref": "src_syn_payee",
                            "pointer": "/payees/0/payee_id",
                        },
                    },
                    {
                        "param": "amount_cents",
                        "source": {"kind": "utterance", "ref": "utt_syn_t1", "span": [4, 7]},
                    },
                ],
            }
        ],
        "rationale": "test",
    }


def freeze_plan() -> dict:
    return {
        "intents": ["card"],
        "steps": [
            {
                "step_id": "s1",
                "tool": "freeze_card",
                "args": {"card_id": "crd_zs_1"},
                "depends_on": [],
                "arg_sources": [
                    {
                        "param": "card_id",
                        "source": {
                            "kind": "read",
                            "ref": "src_syn_card",
                            "pointer": "/cards/0/card_id",
                        },
                    }
                ],
            }
        ],
        "rationale": "test",
    }


def limits_plan() -> dict:
    return {
        "intents": ["card"],
        "steps": [
            {
                "step_id": "s1",
                "tool": "update_card_limits",
                "args": {
                    "card_id": "crd_zs_1",
                    "single_limit_cents": 1000,
                    "daily_limit_cents": -1,
                },
                "depends_on": [],
                "arg_sources": [
                    {
                        "param": "card_id",
                        "source": {
                            "kind": "read",
                            "ref": "src_syn_card",
                            "pointer": "/cards/0/card_id",
                        },
                    },
                    {
                        "param": "single_limit_cents",
                        "source": {"kind": "utterance", "ref": "utt_syn_t3", "span": [16, 20]},
                    },
                ],
            }
        ],
        "rationale": "test",
    }


def limits_plan_with_hint() -> dict:
    plan = limits_plan()
    plan["steps"][0]["arg_sources"].append(
        {"param": "daily_limit_cents", "source": {"kind": "hint", "ref": "hnt_x"}}
    )
    return plan


def run(scripted: ScriptedLLM) -> tuple[list[dict], int]:
    llm = make_llm(api_key="test-key", http_client=scripted.client)
    return run_scenarios(llm)


def valid_responses() -> list:
    return [
        tool_response("report_understanding", UNDERSTANDING),
        tool_response("report_understanding", UNDERSTANDING),
        tool_response("submit_action_plan", transfer_plan()),
        tool_response("submit_action_plan", freeze_plan()),
        tool_response("submit_action_plan", limits_plan()),
        tool_response("get_accounts", {"history_days": 7}),
        text_response("done"),
    ]


def test_run_scenarios_success_exits_zero():
    scripted = ScriptedLLM(valid_responses())
    records, exit_code = run(scripted)
    assert exit_code == 0
    assert len(scripted.requests) == 7


def test_run_scenarios_plan_validation_failure_exits_nonzero():
    bad = {"intents": [], "steps": [], "rationale": "x"}
    responses = [
        tool_response("report_understanding", UNDERSTANDING),
        tool_response("report_understanding", UNDERSTANDING),
        tool_response("submit_action_plan", bad),  # transfer attempt 1
        tool_response("submit_action_plan", bad),  # transfer validation retry
        tool_response("submit_action_plan", freeze_plan()),
        tool_response("submit_action_plan", limits_plan()),
        tool_response("get_accounts", {"history_days": 7}),
        text_response("done"),
    ]
    scripted = ScriptedLLM(responses)
    records, exit_code = run(scripted)
    assert exit_code == 1
    assert len(scripted.requests) == 8
    plan_records = [r for r in records if r["name"].startswith("plan:")]
    assert any(r["ok"] is False and "validation_errors" in r for r in plan_records)


def test_run_scenarios_semantic_assertion_fails_exit_nonzero():
    responses = valid_responses()
    responses[2] = tool_response("submit_action_plan", transfer_plan(payee="pye_other"))
    scripted = ScriptedLLM(responses)
    records, exit_code = run(scripted)
    assert exit_code == 1
    transfer_record = next(r for r in records if r["name"] == "plan:transfer")
    assert transfer_record["ok"] is False
    assert "problems" in transfer_record


def test_run_scenarios_catches_unprovided_hint():
    responses = valid_responses()
    responses[4] = tool_response("submit_action_plan", limits_plan_with_hint())
    scripted = ScriptedLLM(responses)
    records, exit_code = run(scripted)
    assert exit_code == 1
    limits_record = next(r for r in records if r["name"] == "plan:limits")
    assert limits_record["ok"] is False
    assert any("unexpected source kind hint" in p for p in limits_record["problems"])


def test_run_scenarios_catches_wrong_amount_span():
    bad = transfer_plan()
    bad["steps"][0]["arg_sources"][2]["source"]["span"] = [5, 7]  # "00", not "800"
    responses = valid_responses()
    responses[2] = tool_response("submit_action_plan", bad)
    scripted = ScriptedLLM(responses)
    records, exit_code = run(scripted)
    assert exit_code == 1
    transfer_record = next(r for r in records if r["name"] == "plan:transfer")
    assert any("slice" in p for p in transfer_record["problems"])


def test_run_scenarios_budget_exhaustion_fails():
    # only enough responses for part of the run: understand(2) then a budget-style failure
    scripted = ScriptedLLM(
        [
            tool_response("report_understanding", UNDERSTANDING),
            tool_response("report_understanding", UNDERSTANDING),
        ]
    )

    def budget_handler(request):
        raise RuntimeError("live request budget exceeded (8)")

    scripted.responses.append(budget_handler)
    records, exit_code = run(scripted)
    assert exit_code == 1


def test_budget_transport_enforces_hard_limit():
    calls = []

    class Underlying(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(200, json={"ok": True}, request=request)

    transport = _BudgetTransport(Underlying(), 8)
    client = httpx.Client(transport=transport)
    for _ in range(8):
        client.get("https://api.deepseek.com/")
    assert len(calls) == 8
    with pytest.raises(RuntimeError):
        client.get("https://api.deepseek.com/")
    assert len(calls) == 8  # the 9th request never touches the underlying transport
