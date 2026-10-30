"""A planner may not reinterpret is_idle=false from zero usage evidence."""

from copy import deepcopy

import pytest

from services.agent.app.graph.state import ReadRecord
from services.agent.app.llm.client import make_llm
from services.agent.app.llm.plan import PlanAdapter, ReferenceError
from tests.agent.llm_mock import ScriptedLLM, tool_response
from tests.agent.test_plan_e2e_fixes import (
    _good_cancel_sub_derived_amount,
    _subscription_schedule_state,
    ctx,
)


def state():
    current = _subscription_schedule_state()
    current["reads"][1] = ReadRecord(
        source_ref="src_sub",
        tool="detect_subscriptions",
        args={"lookback_days": 180},
        data={
            "items": [
                {
                    "instruction_id": "si_zs_gym",
                    "is_idle": True,
                    "amount_cents": 19900,
                    "period": "MONTHLY",
                },
                {
                    "instruction_id": "si_zs_music",
                    "is_idle": False,
                    "amount_cents": 1500,
                    "period": "MONTHLY",
                    "usage_evidence": {"recent_usage_count": 0},
                },
            ]
        },
    )
    return current


def proposal(*, overbroad=False):
    plan = deepcopy(_good_cancel_sub_derived_amount())
    plan["steps"][1]["arg_sources"][0]["source"]["inputs"] = [
        {"kind": "step", "ref": "s1"},
        {"kind": "read", "ref": "src_sub", "pointer": "/items/0/amount_cents"},
    ]
    if overbroad:
        extra = deepcopy(plan["steps"][0])
        extra["step_id"] = "cancel_active"
        extra["args"]["instruction_id"] = "si_zs_music"
        plan["steps"].insert(1, extra)
        plan["steps"][-1]["depends_on"].append("cancel_active")
        plan["steps"][-1]["args"]["amount_cents"] += 1500
    return plan


def test_overbroad_idle_cancellation_is_replanned_before_gateway_submission():
    scripted = ScriptedLLM(
        [
            tool_response("submit_action_plan", proposal(overbroad=True)),
            tool_response("submit_action_plan", proposal()),
        ]
    )
    draft = PlanAdapter(make_llm(api_key="test-key", http_client=scripted.client))(state(), ctx())
    assert [s.args.instruction_id for s in draft.steps if s.tool == "cancel_subscription"] == [
        "si_zs_gym"
    ]
    assert draft.steps[-1].args.amount_cents == 19900
    assert len(scripted.requests) == 2


def test_exhausted_overbroad_plan_never_leaves_the_adapter():
    scripted = ScriptedLLM([tool_response("submit_action_plan", proposal(overbroad=True))])
    with pytest.raises(ReferenceError):
        PlanAdapter(make_llm(api_key="test-key", http_client=scripted.client), max_retries=0)(
            state(), ctx()
        )


def test_missing_idle_boolean_cannot_be_inferred_from_usage_count():
    current = state()
    current["reads"][1].data["items"][0].pop("is_idle")
    scripted = ScriptedLLM([tool_response("submit_action_plan", proposal())])
    with pytest.raises(ReferenceError):
        PlanAdapter(make_llm(api_key="test-key", http_client=scripted.client), max_retries=0)(
            current, ctx()
        )
