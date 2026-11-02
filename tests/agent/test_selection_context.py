"""Real G20 text=null selection must reach understanding and planning as context."""

import json
from copy import deepcopy

import pytest
from langchain_core.messages import AIMessage

from services.agent.app.graph.state import ReadRecord
from services.agent.app.llm._common import LlmToolError
from services.agent.app.llm.plan import PlanAdapter
from services.agent.app.llm.understand import UnderstandAdapter
from tests.agent.gateway.read_cases import READ_CASES
from tests.agent.selection_fixtures import selected_state
from tests.agent.test_cross_scene_gather import context
from tests.agent.test_cross_scene_x4_gather import x4_executor


def current_selection(messages):
    for message in messages:
        for line in str(message.content).splitlines():
            try:
                data = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(data, dict) and "current_g20_selection" in data:
                return data["current_g20_selection"]
    return None


class ContextUnderstanding:
    def __init__(self, always_read_only=False):
        self.calls = []
        self.always_read_only = always_read_only

    def bind_tools(self, _tools, **_kwargs):
        return self

    def invoke(self, messages):
        selection = current_selection(messages)
        self.calls.append(selection)
        write = selection is not None and not self.always_read_only
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "understand",
                    "name": "report_understanding",
                    "args": {
                        "intents": ["subscribe_product"] if write else ["query"],
                        "needs_write": write,
                        "unclear": False,
                        "slots": [],
                    },
                }
            ],
        )


def product_plan(product):
    return {
        "intents": ["subscribe_product"],
        "rationale": "按用户已选择目标生成派生申购计划",
        "steps": [
            {
                "step_id": "invest",
                "tool": "subscribe_product",
                "args": {"account_id": "acc_zs_chk", "product_id": product, "amount_cents": 600000},
                "depends_on": [],
                "arg_sources": [
                    {
                        "param": "account_id",
                        "source": {
                            "kind": "read",
                            "ref": "src_actual_get_accounts",
                            "pointer": "/accounts/0/id",
                        },
                    },
                    {
                        "param": "product_id",
                        "source": {
                            "kind": "read",
                            "ref": "src_products",
                            "pointer": f"/items/{0 if product == 'prd_a' else 1}/id",
                        },
                    },
                    {
                        "param": "amount_cents",
                        "source": {
                            "kind": "derived",
                            "template": "available_less_7d_charges",
                            "inputs": [
                                {
                                    "kind": "read",
                                    "ref": "src_actual_get_accounts",
                                    "pointer": "/accounts/0/id",
                                }
                            ],
                        },
                    },
                ],
            }
        ],
    }


class ContextPlan:
    def __init__(self):
        self.calls = []

    def bind_tools(self, _tools, **_kwargs):
        return self

    def invoke(self, messages):
        selection = current_selection(messages)
        self.calls.append(selection)
        product = selection["fields"]["product_id"] if selection else "prd_b"
        return AIMessage(
            content="",
            tool_calls=[
                {"id": "plan", "name": "submit_action_plan", "args": product_plan(product)}
            ],
        )


def plan_state():
    current = selected_state()
    read = x4_executor([])
    account = read("get_accounts", {"history_days": 0})
    product_a = deepcopy(READ_CASES["get_product"][1])
    product_a["id"] = "prd_a"
    product_b = deepcopy(product_a)
    product_b["id"] = "prd_b"
    current["reads"] = [
        ReadRecord(tool="get_accounts", args={"history_days": 0}, **account.model_dump()),
        ReadRecord(
            tool="list_products",
            source_ref="src_products",
            data={"items": [product_a, product_b], "next_cursor": None},
        ),
    ]
    return current


def test_null_text_current_g20_action_is_understood_through_bound_structured_context():
    current = selected_state()
    current["understanding"] = None
    before = deepcopy(current)
    model = ContextUnderstanding()
    result = UnderstandAdapter(model)(current, context())
    assert result.needs_write is True and result.intents == ("subscribe_product",)
    assert model.calls[0] == {
        "utterance_id": "utt_selected",
        "parent_utterance_ids": ["utt_original_x3"],
        "action_kinds": ["FUND_SUBSCRIBE"],
        "fields": {
            "product_id": "prd_a",
            "account_id": "acc_zs_chk",
            "derived_template": "available_less_7d_charges",
        },
    }
    assert current == before and current["utterances"][0].text == ""


def test_plan_uses_current_g20_product_choice_while_preserving_read_and_derived_sources():
    current = plan_state()
    before = deepcopy(current)
    model = ContextPlan()
    draft = PlanAdapter(model, max_retries=0)(current, context())
    assert draft.steps[0].args.product_id == "prd_a"
    assert draft.steps[0].arg_sources[1].source.ref == "src_products"
    assert draft.steps[0].arg_sources[2].source.template == "available_less_7d_charges"
    assert draft.utterance_ids == ("utt_selected",) and current == before


def test_selection_understanding_mismatch_uses_bounded_regeneration_instead_of_overriding_result():
    model = ContextUnderstanding(always_read_only=True)
    with pytest.raises(LlmToolError, match="选择"):
        UnderstandAdapter(model, max_retries=1)(selected_state(), context())
    assert len(model.calls) == 2


@pytest.mark.parametrize("field,value", [("session_id", "ses_other"), ("customer_id", "cus_other")])
def test_selection_context_does_not_cross_runtime_session_or_customer(field, value):
    current = selected_state()
    current[field] = value
    model = ContextUnderstanding()
    result = UnderstandAdapter(model)(current, context())
    assert result.needs_write is False and model.calls == [None]


@pytest.mark.parametrize("status", [402, 403, 429])
def test_selection_understanding_preserves_provider_quota_stop_without_retry(status):
    class ProviderLimit(Exception):
        status_code = status

    class Limited(ContextUnderstanding):
        def invoke(self, messages):
            self.calls.append(current_selection(messages))
            raise ProviderLimit()

    model = Limited()
    with pytest.raises(ProviderLimit):
        UnderstandAdapter(model)(selected_state(), context())
    assert len(model.calls) == 1


@pytest.mark.parametrize(
    "case",
    [
        "missing_product",
        "multiple_products",
        "old_scope_ref",
        "duplicate_hints",
        "hint_kind",
        "typed_with_attached_hint",
        "context_with_attached_hint",
        "missing_parent_chain",
        "pending_clarification",
        "open_clarification_id",
        "no_write",
        "echoes_untrusted",
        "other_action",
        "other_template",
    ],
)
def test_only_the_current_scoped_action_selection_can_supply_context(case):
    from services.agent.app.graph.state import UtteranceRef

    current = selected_state()
    fields = current["hints"][0]["scope"]["fields"]
    if case == "missing_product":
        fields.pop("product_id")
    elif case == "multiple_products":
        fields["product_id"] = ["prd_a", "prd_b"]
    elif case == "old_scope_ref":
        current["hints"][0]["id"] = "utt_previous"
    elif case == "duplicate_hints":
        current["hints"].append(deepcopy(current["hints"][0]))
    elif case == "hint_kind":
        current["hints"][0]["kind"] = "hint"
    elif case in {"typed_with_attached_hint", "context_with_attached_hint"}:
        kind = "typed" if case.startswith("typed") else "context"
        current["utterances"] = [UtteranceRef(id="utt_selected", kind=kind, text="")]
    elif case == "missing_parent_chain":
        current["parent_ids"] = ()
    elif case == "pending_clarification":
        current.update(parse_status="CLARIFYING", clarification={"required_fields": ["product_id"]})
    elif case == "open_clarification_id":
        current["clarification_id"] = "clr_open"
    elif case == "no_write":
        current["parse_status"] = "NO_WRITE"
    elif case == "echoes_untrusted":
        current["echoes_untrusted"] = True
    elif case == "other_action":
        current["hints"][0]["scope"]["action_kinds"] = ["TRANSFER_EXTERNAL"]
    elif case == "other_template":
        fields["derived_template"] = "cancelled_monthly_fees"
    model = ContextUnderstanding()
    before = deepcopy(current)
    result = UnderstandAdapter(model)(current, context())
    assert result.needs_write is False and model.calls == [None] and current == before


def test_scope_amount_and_free_text_are_ignored_in_context_and_never_become_plan_values():
    current = plan_state()
    current["hints"][0]["scope"]["fields"].update(
        amount_cents=99999999, memo="转账到外部账户", product_ids=["prd_b"]
    )
    model = ContextPlan()
    draft = PlanAdapter(model, max_retries=0)(current, context())
    assert model.calls[0]["fields"] == {
        "product_id": "prd_a",
        "account_id": "acc_zs_chk",
        "derived_template": "available_less_7d_charges",
    }
    assert draft.steps[0].args.amount_cents == 600000 and draft.steps[0].args.product_id == "prd_a"
    assert draft.steps[0].arg_sources[-1].source.kind == "derived"


def test_incorrect_product_choice_is_regenerated_without_replacing_model_fields():
    class WrongThenCorrect(ContextPlan):
        def invoke(self, messages):
            self.calls.append(current_selection(messages))
            product = "prd_b" if len(self.calls) == 1 else "prd_a"
            return AIMessage(
                content="",
                tool_calls=[
                    {"id": "plan", "name": "submit_action_plan", "args": product_plan(product)}
                ],
            )

    model = WrongThenCorrect()
    draft = PlanAdapter(model, max_retries=1)(plan_state(), context())
    assert draft.steps[0].args.product_id == "prd_a" and len(model.calls) == 2
