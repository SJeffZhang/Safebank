"""Live scenario runner exit-code behavior against a scripted transport."""

from __future__ import annotations

from services.agent.app.llm.client import make_llm
from services.agent.app.llm.live import run_scenarios
from tests.agent.llm_mock import ScriptedLLM, text_response, tool_response

UNDERSTANDING = {"intents": ["transfer"], "slots": [], "needs_write": True}


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
                "arg_sources": [],
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
                "arg_sources": [],
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
                "arg_sources": [],
            }
        ],
        "rationale": "test",
    }


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
    responses = valid_responses()
    bad = {"intents": [], "steps": [], "rationale": "x"}
    responses[2] = tool_response("submit_action_plan", bad)
    scripted = ScriptedLLM(responses)
    records, exit_code = run(scripted)
    assert exit_code == 1
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
