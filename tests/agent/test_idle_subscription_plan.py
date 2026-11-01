"""A planner may not reinterpret is_idle=false from zero usage evidence."""

from copy import deepcopy

import pytest

from services.agent.app.graph.state import ReadRecord
from services.agent.app.llm.client import make_llm
from services.agent.app.llm.plan import PlanAdapter, ReferenceError
from tests.agent.llm_mock import ScriptedLLM, tool_response
from tests.agent.subscription_fixtures import detected_subscription
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
                detected_subscription("si_zs_gym", 19900, is_idle=True, merchant_name="阳光健身"),
                detected_subscription("si_zs_music", 1500, is_idle=False, merchant_name="乐听音乐"),
            ]
        },
    )
    return current


def proposal(*, overbroad=False):
    plan = deepcopy(_good_cancel_sub_derived_amount())
    next(s for s in plan["steps"][1]["arg_sources"] if s["param"] == "amount_cents")["source"][
        "inputs"
    ] = [
        {"kind": "step", "ref": "s1"},
        {"kind": "read", "ref": "src_sub", "pointer": "/items/0/typical_amount_cents"},
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


@pytest.mark.parametrize("missing", ["period", "first_run_at"])
def test_subscription_savings_missing_calendar_source_never_leaves_adapter(missing):
    plan = proposal()
    plan["steps"][-1]["arg_sources"] = [
        s for s in plan["steps"][-1]["arg_sources"] if s["param"] != missing
    ]
    scripted = ScriptedLLM([tool_response("submit_action_plan", plan)])
    with pytest.raises(ReferenceError):
        PlanAdapter(make_llm(api_key="test-key", http_client=scripted.client), max_retries=0)(
            state(), ctx()
        )
