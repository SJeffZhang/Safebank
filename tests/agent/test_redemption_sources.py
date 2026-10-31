"""A holding bounds redemption; only the customer's words select its quantity."""

from copy import deepcopy
from decimal import Decimal

import pytest
from safebank_contracts.plan import PlanBody
from safebank_contracts.schema import from_strict_plan

from services.agent.app.llm.client import make_llm
from services.agent.app.llm.plan import PlanAdapter
from tests.agent.llm_mock import ScriptedLLM, tool_response
from tests.agent.test_funds_prompt import (
    _accounts_read,
    _ctx,
    _funds_state,
    _holdings_read,
    _redeem_plan,
)


def state():
    return _funds_state(
        "赎回安心货币1000份", [_accounts_read(), _holdings_read("5000.0000")], ["redeem_product"]
    )


def proposal():
    p = _redeem_plan({"kind": "utterance", "ref": "utt_typed", "span": [6, 10]})
    p["steps"][0]["args"]["units"] = "1000.0000"
    p["steps"][0]["arg_sources"] += [
        {
            "param": "product_id",
            "source": {"kind": "read", "ref": "src_holdings", "pointer": "/holdings/0/product_id"},
        },
        {
            "param": "account_id",
            "source": {"kind": "read", "ref": "src_accounts", "pointer": "/accounts/0/id"},
        },
    ]
    return p


def test_partial_redemption_uses_user_units_not_whole_holding():
    scripted = ScriptedLLM([tool_response("submit_action_plan", proposal())])
    draft = PlanAdapter(make_llm(api_key="test-key", http_client=scripted.client))(state(), _ctx())
    assert draft.steps[0].args.units == Decimal("1000.0000")


def test_whole_holding_read_cannot_replace_user_partial_quantity():
    bad = deepcopy(proposal())
    bad["steps"][0]["args"]["units"] = "5000.0000"
    bad["steps"][0]["arg_sources"][0]["source"] = {
        "kind": "read",
        "ref": "src_holdings",
        "pointer": "/holdings/0/units",
    }
    scripted = ScriptedLLM(
        [tool_response("submit_action_plan", bad), tool_response("submit_action_plan", proposal())]
    )
    draft = PlanAdapter(make_llm(api_key="test-key", http_client=scripted.client))(state(), _ctx())
    assert draft.steps[0].args.units == Decimal("1000.0000")
    assert len(scripted.requests) == 2


def test_strict_integer_units_convert_without_float():
    p = proposal()
    p["steps"][0]["args"]["units"] = 1000
    body = PlanBody.model_validate(from_strict_plan(p))
    assert body.steps[0].args.units == Decimal("1000.0000")


@pytest.mark.parametrize("value", [1000.0, True, False])
def test_strict_units_reject_float_and_bool(value):
    p = proposal()
    p["steps"][0]["args"]["units"] = value
    with pytest.raises(ValueError):
        PlanBody.model_validate(from_strict_plan(p))
