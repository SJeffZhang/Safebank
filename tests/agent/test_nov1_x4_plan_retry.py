"""Formal case 37: actionable, bounded feedback for invalid strict entity shapes.

The saved case contains the terminal tagged-entity error, not the raw model body.
These fixtures reproduce that converter error using the formal utterance and real
strict schema; they do not claim to be a captured production response.
"""

from copy import deepcopy

import httpx
import jsonschema
import pytest
from openai import APIStatusError
from safebank_contracts.schema import plan_tool

from services.agent.app.llm.client import make_llm
from services.agent.app.llm.gather import GatherAdapter
from services.agent.app.llm.plan import PlanAdapter
from tests.agent.llm_mock import ScriptedLLM, tool_response
from tests.agent.test_cross_scene_gather import CardsOnly, context
from tests.agent.test_cross_scene_x4_gather import X4, x4_executor, x4_state


def x4_proposal():
    start = X4.index("2000")
    return {
        "intents": ["freeze_card", "transfer_self"],
        "rationale": "冻结并本人应急互转",
        "steps": [
            {
                "step_id": "freeze",
                "tool": "freeze_card",
                "args": {"card_id": "crd_zs_1"},
                "depends_on": [],
                "arg_sources": [
                    {
                        "param": "card_id",
                        "source": {
                            "kind": "read",
                            "ref": "src_actual_get_cards",
                            "pointer": "/items/0/id",
                        },
                    }
                ],
            },
            {
                "step_id": "emergency",
                "tool": "transfer",
                "args": {
                    "from_account_id": "acc_zs_save",
                    "payee_id": {"kind": "none"},
                    "to_account_id": {"kind": "id", "id": "acc_zs_chk"},
                    "amount_cents": 200000,
                    "memo": "__NULL__",
                },
                "depends_on": ["freeze"],
                "arg_sources": [
                    {
                        "param": "from_account_id",
                        "source": {
                            "kind": "read",
                            "ref": "src_actual_get_accounts",
                            "pointer": "/accounts/1/id",
                        },
                    },
                    {
                        "param": "to_account_id",
                        "source": {
                            "kind": "read",
                            "ref": "src_actual_get_accounts",
                            "pointer": "/accounts/0/id",
                        },
                    },
                    {
                        "param": "amount_cents",
                        "source": {
                            "kind": "utterance",
                            "ref": "utt_cross",
                            "span": [start, start + 4],
                        },
                    },
                ],
            },
        ],
    }


def current_state():
    current = x4_state()
    current["reads"] = GatherAdapter(
        CardsOnly(), x4_executor([]), fetch_business_date=True, supplement_transfer_reads=True
    )(current, context())
    return current


def adapter(scripted):
    return PlanAdapter(make_llm(api_key="synthetic", http_client=scripted.client))


@pytest.mark.parametrize("bad_entity", ["acc_zs_chk", None, {"id": "acc_zs_chk"}])
def test_formal_x4_entity_error_feedback_names_field_and_permitted_shape(bad_entity):
    good = x4_proposal()
    schema = plan_tool()["function"]["parameters"]
    jsonschema.validate(good, schema)
    bad = deepcopy(good)
    bad["steps"][1]["args"]["to_account_id"] = bad_entity
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)
    scripted = ScriptedLLM(
        [tool_response("submit_action_plan", bad), tool_response("submit_action_plan", good)]
    )
    state = current_state()
    before = deepcopy(state)
    draft = adapter(scripted)(state, context())
    feedback = scripted.requests[1]["body"]["messages"][-1]["content"].rsplit("\n\n", 1)[-1]
    assert "steps[1].to_account_id" in feedback
    assert '"kind":"id"' in feedback and '"kind":"none"' in feedback
    assert "200000" not in feedback and "acc_zs_chk" not in feedback
    assert len(scripted.requests) == 2
    assert [step.tool for step in draft.steps] == ["freeze_card", "transfer"]
    assert draft.steps[1].depends_on == ("freeze",)
    assert draft.steps[1].args.from_account_id == "acc_zs_save"
    assert draft.steps[1].args.to_account_id == "acc_zs_chk"
    assert draft.steps[1].args.amount_cents == 200000
    assert draft.steps[1].arg_sources[-1].source.ref == "utt_cross"
    assert state == before


@pytest.mark.parametrize("status", [402, 403, 429])
def test_x4_provider_failure_after_shape_feedback_stops_without_extra_retry(status):
    bad = x4_proposal()
    bad["steps"][1]["args"]["to_account_id"] = "acc_zs_chk"
    scripted = ScriptedLLM(
        [
            tool_response("submit_action_plan", bad),
            httpx.Response(status, json={"error": {"message": "synthetic unavailable"}}),
        ]
    )
    with pytest.raises(APIStatusError) as error:
        adapter(scripted)(current_state(), context())
    assert error.value.status_code == status
    assert len(scripted.requests) == 2


def test_repeated_invalid_shape_stops_after_the_existing_one_retry():
    bad = x4_proposal()
    bad["steps"][1]["args"]["to_account_id"] = {"kind": "bogus", "ref": "acc_zs_chk"}
    scripted = ScriptedLLM([tool_response("submit_action_plan", bad) for _ in range(2)])
    with pytest.raises(ValueError):
        adapter(scripted)(current_state(), context())
    assert len(scripted.requests) == 2


def test_shape_feedback_never_echoes_invalid_proposal_content():
    bad = x4_proposal()
    bad["steps"][1]["args"]["to_account_id"] = "IGNORE_RULES_6222999988887777"
    scripted = ScriptedLLM(
        [
            tool_response("submit_action_plan", bad),
            tool_response("submit_action_plan", x4_proposal()),
        ]
    )
    draft = adapter(scripted)(current_state(), context())
    feedback = scripted.requests[1]["body"]["messages"][-1]["content"].rsplit("\n\n", 1)[-1]
    assert "IGNORE_RULES" not in feedback and "6222999988887777" not in feedback
    assert draft.steps[1].args.to_account_id == "acc_zs_chk"


@pytest.mark.parametrize("bad_source", ["missing_ref", "wrong_pointer", "wrong_id"])
def test_shape_retry_does_not_repair_or_bypass_target_provenance(bad_source):
    from services.agent.app.llm.plan import ReferenceError

    bad = x4_proposal()
    bad["steps"][1]["args"]["to_account_id"] = "acc_zs_chk"
    untrusted = x4_proposal()
    source = untrusted["steps"][1]["arg_sources"][1]["source"]
    if bad_source == "missing_ref":
        source["ref"] = "src_not_read"
    elif bad_source == "wrong_pointer":
        source["pointer"] = "/accounts/1/id"
    else:
        untrusted["steps"][1]["args"]["to_account_id"]["id"] = "acc_other_customer"
    scripted = ScriptedLLM(
        [tool_response("submit_action_plan", bad), tool_response("submit_action_plan", untrusted)]
    )
    with pytest.raises(ReferenceError):
        adapter(scripted)(current_state(), context())
    assert len(scripted.requests) == 2


def test_external_payee_shape_feedback_preserves_read_bound_target():
    good = x4_proposal()
    good["steps"] = [good["steps"][1]]
    good["steps"][0]["depends_on"] = []
    good["steps"][0]["args"]["to_account_id"] = {"kind": "none"}
    good["steps"][0]["args"]["payee_id"] = {"kind": "id", "id": "pye_zs_ww"}
    good["steps"][0]["arg_sources"][1] = {
        "param": "payee_id",
        "source": {"kind": "read", "ref": "src_payees", "pointer": "/items/0/id"},
    }
    bad = deepcopy(good)
    bad["steps"][0]["args"]["payee_id"] = "pye_zs_ww"
    from services.agent.app.graph.state import ReadRecord

    state = current_state()
    state["reads"].append(
        ReadRecord(
            tool="list_payees", source_ref="src_payees", data={"items": [{"id": "pye_zs_ww"}]}
        )
    )
    scripted = ScriptedLLM(
        [tool_response("submit_action_plan", bad), tool_response("submit_action_plan", good)]
    )
    draft = adapter(scripted)(state, context())
    feedback = scripted.requests[1]["body"]["messages"][-1]["content"].rsplit("\n\n", 1)[-1]
    assert "steps[0].payee_id" in feedback and "pye_zs_ww" not in feedback
    assert draft.steps[0].args.payee_id == "pye_zs_ww"


def test_other_invalid_step_shapes_keep_sanitized_generic_feedback():
    bad = x4_proposal()
    bad["steps"][1]["tool"] = {"attack": "do-not-echo-this"}
    scripted = ScriptedLLM(
        [
            tool_response("submit_action_plan", bad),
            tool_response("submit_action_plan", x4_proposal()),
        ]
    )
    draft = adapter(scripted)(current_state(), context())
    feedback = scripted.requests[1]["body"]["messages"][-1]["content"].rsplit("\n\n", 1)[-1]
    assert "do-not-echo-this" not in feedback
    assert draft.steps[1].args.to_account_id == "acc_zs_chk"
