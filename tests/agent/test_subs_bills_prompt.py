"""L1 prompt rules for the two L3-implemented scenarios: subscriptions and bill analysis.

- Subscriptions: ``cancel_subscription.instruction_id`` must be read-sourced (si_ id
  from list_subscriptions / detect_subscriptions), and the X-2 "cancel idle
  subscriptions + schedule the saved fees to savings" plan must derive
  ``schedule_transfer.amount_cents`` from ``cancelled_monthly_fees`` — never carry a
  literal utterance amount source.
- Bill analysis is read-only: the gather prompt must steer the model toward
  get_transactions / get_spending_summary / detect_anomalies with calendar-month
  ranges in Asia/Shanghai, and the planner must not invent a write plan.
"""

from __future__ import annotations

import pytest

from services.agent.app.graph.state import AgentContext, ReadRecord, Understanding, UtteranceRef
from services.agent.app.llm.client import make_llm
from services.agent.app.llm.gather import GatherAdapter
from services.agent.app.llm.plan import PlanAdapter, ReferenceError
from tests.agent.llm_mock import ScriptedLLM, text_response, tool_response

API_KEY = "test-key"


def _ctx() -> AgentContext:
    return AgentContext(assistant_token="SECRET_TOKEN", customer_id="cus_a", session_id="ses_a")


def _subs_read() -> ReadRecord:
    return ReadRecord(
        source_ref="src_sub",
        tool="detect_subscriptions",
        args={"lookback_days": 180},
        data={
            "items": [
                {"instruction_id": "si_sub1", "is_idle": True, "merchant_name": "视频会员"}
            ]
        },
    )


def _subs_state(utterance_text: str) -> dict:
    return {
        "customer_id": "cus_a",
        "session_id": "ses_a",
        "utterances": [UtteranceRef(id="utt_typed", kind="typed", text=utterance_text)],
        "understanding": Understanding(intents=["cancel_subscription"], needs_write=True),
        "reads": [_subs_read()],
    }


def _cancel_plan() -> dict:
    return {
        "intents": ["cancel_subscription"],
        "steps": [
            {
                "step_id": "s1",
                "tool": "cancel_subscription",
                "args": {"instruction_id": "si_sub1"},
                "depends_on": [],
                "arg_sources": [
                    {
                        "param": "instruction_id",
                        "source": {
                            "kind": "read",
                            "ref": "src_sub",
                            "pointer": "/items/0/instruction_id",
                        },
                    }
                ],
            }
        ],
        "rationale": "test",
    }


def _x2_plan_with_literal_amount() -> dict:
    """X-2 combo; the schedule amount wrongly carries a literal utterance source."""
    return {
        "intents": ["cancel_subscription", "schedule_transfer"],
        "steps": [
            {
                "step_id": "s1",
                "tool": "cancel_subscription",
                "args": {"instruction_id": "si_sub1"},
                "depends_on": [],
                "arg_sources": [
                    {
                        "param": "instruction_id",
                        "source": {
                            "kind": "read",
                            "ref": "src_sub",
                            "pointer": "/items/0/instruction_id",
                        },
                    }
                ],
            },
            {
                "step_id": "s2",
                "tool": "schedule_transfer",
                "args": {
                    "from_account_id": "acc_zs_chk",
                    "payee_id": {"kind": "none"},
                    "to_account_id": {"kind": "id", "id": "acc_zs_sav"},
                    "amount_cents": 2000,
                    "period": "MONTHLY",
                    "first_run_at": "2026-11-01T00:00:00+08:00",
                    "end_at": "__NULL__",
                },
                "depends_on": ["s1"],
                "arg_sources": [
                    {
                        "param": "amount_cents",
                        "source": {
                            "kind": "derived",
                            "template": "cancelled_monthly_fees",
                            "inputs": [
                                {"kind": "utterance", "ref": "utt_typed", "span": [0, 3]}
                            ],
                        },
                    }
                ],
            },
        ],
        "rationale": "test",
    }


def _system_prompt(scripted: ScriptedLLM) -> str:
    messages = scripted.requests[0]["body"]["messages"]
    for message in messages:
        if message.get("role") == "system":
            return str(message.get("content", ""))
    return ""


# ── prompt rules present ──


def test_plan_prompt_contains_subs_bills_rules():
    scripted = ScriptedLLM([tool_response("submit_action_plan", _cancel_plan())])
    llm = make_llm(api_key=API_KEY, http_client=scripted.client)
    PlanAdapter(llm)(_subs_state("取消我的视频会员订阅"), _ctx())
    prompt = _system_prompt(scripted)
    assert "cancel_subscription 的 instruction_id 必须从已读数据" in prompt
    assert "cancelled_monthly_fees" in prompt
    assert "账单分析类问题" in prompt


def test_gather_prompt_contains_subs_bills_rules():
    scripted = ScriptedLLM([text_response("")])
    llm = make_llm(api_key=API_KEY, http_client=scripted.client)
    GatherAdapter(llm, read_executor=lambda name, args: None)(
        _subs_state("取消订阅"), _ctx()
    )
    prompt = _system_prompt(scripted)
    assert "list_subscriptions" in prompt
    assert "detect_subscriptions" in prompt
    assert "get_spending_summary" in prompt
    assert "detect_anomalies" in prompt
    assert "get_cards" in prompt
    assert "北京时间" in prompt


# ── cancel_subscription read-sourced si_ id passes ──


def test_cancel_subscription_read_sourced_si_passes():
    scripted = ScriptedLLM([tool_response("submit_action_plan", _cancel_plan())])
    llm = make_llm(api_key=API_KEY, http_client=scripted.client)
    draft = PlanAdapter(llm)(_subs_state("取消我的视频会员订阅"), _ctx())
    step = draft.steps[0]
    assert step.args.instruction_id == "si_sub1"
    sources = [s for s in step.arg_sources if s.param == "instruction_id"]
    assert len(sources) == 1
    assert sources[0].source.kind == "read"
    assert sources[0].source.ref == "src_sub"
    assert sources[0].source.pointer == "/items/0/instruction_id"


# ── derived-template schedule must not carry a literal amount source ──


def test_derived_schedule_rejects_literal_amount_source():
    scripted = ScriptedLLM(
        [tool_response("submit_action_plan", _x2_plan_with_literal_amount())]
    )
    llm = make_llm(api_key=API_KEY, http_client=scripted.client)
    with pytest.raises(ReferenceError) as exc_info:
        PlanAdapter(llm)(
            _subs_state("找出没用的订阅取消掉，省下来的钱每月1号自动转到我的储蓄账户"),
            _ctx(),
        )
    assert any("派生金额" in problem for problem in exc_info.value.problems)
