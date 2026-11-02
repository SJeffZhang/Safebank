"""Resolved G3 X3 product choice still requires real read prerequisites."""

from copy import deepcopy

import pytest
from langchain_core.messages import AIMessage
from safebank_contracts.views import READ_RESULTS

from services.agent.app.llm.client import make_llm
from services.agent.app.llm.gather import GatherAdapter, ReadResult
from services.agent.app.llm.plan import PlanAdapter
from tests.agent.gateway.read_cases import READ_CASES
from tests.agent.llm_mock import ScriptedLLM, tool_response
from tests.agent.selection_fixtures import selected_state
from tests.agent.test_cross_scene_gather import context
from tests.agent.test_cross_scene_x4_gather import x4_executor


class StopModel:
    def bind_tools(self, _tools):
        return self

    def invoke(self, _messages):
        return AIMessage(content="done")


def x3_executor(calls):
    base = x4_executor(calls)

    def read(tool, args):
        if tool not in {"upcoming_charges", "get_risk_profile", "get_product"}:
            return base(tool, args)
        calls.append((tool, deepcopy(args)))
        data = deepcopy(READ_CASES[tool][1])
        if tool == "get_product":
            data.update(id=args["product_id"], risk_level=2, risk_label="稳健")
        if tool == "get_risk_profile":
            data.update(
                assessment_id="ras_current",
                level=3,
                is_valid=True,
                assessed_at="2026-10-15T01:00:00Z",
                valid_until="2027-10-15",
            )
        if tool == "upcoming_charges":
            data["by_account"] = [
                {"account_id": "acc_zs_chk", "reserve_cents": 0, "max_outflow_cents": 600000}
            ]
        READ_RESULTS[tool].model_validate(data)
        return ReadResult(
            data=data,
            source_ref=f"src_actual_{tool}",
            versions={"cus_zs": 7},
            observed_at="2026-10-15T01:00:00Z",
            untrusted_paths=("/name", "/risk_disclosure") if tool == "get_product" else (),
        )

    return read


def test_selected_x3_stopped_model_reads_required_context_without_product_selection():
    calls = []
    current = selected_state()
    before = deepcopy(current)
    records = GatherAdapter(
        StopModel(), x3_executor(calls), fetch_business_date=True, supplement_transfer_reads=True
    )(current, context())
    assert calls == [
        ("get_profile", {}),
        ("get_accounts", {"history_days": 0}),
        ("upcoming_charges", {"days": 7, "account_id": None}),
        ("get_risk_profile", {}),
        ("get_product", {"product_id": "prd_a"}),
    ]
    assert [r.source_ref for r in records] == [
        "src_actual_get_profile",
        "src_actual_get_accounts",
        "src_actual_upcoming_charges",
        "src_actual_get_risk_profile",
        "src_actual_get_product",
    ]
    assert records[-1].data["id"] == "prd_a" and "<untrusted_data" in records[-1].data["name"]
    assert all(r.versions == {"cus_zs": 7} for r in records)
    assert current == before


def test_selected_x3_gather_provides_real_sources_for_existing_derived_plan_validation():
    from services.agent.app.llm.understand import UnderstandAdapter
    from tests.agent.test_selection_context import ContextUnderstanding

    current = selected_state()
    current["understanding"] = None
    current["understanding"] = UnderstandAdapter(ContextUnderstanding())(current, context())
    current["reads"] = GatherAdapter(
        StopModel(), x3_executor([]), fetch_business_date=True, supplement_transfer_reads=True
    )(current, context())
    proposal = _selected_product_plan()
    scripted = ScriptedLLM([tool_response("submit_action_plan", proposal)])
    draft = PlanAdapter(make_llm(api_key="synthetic", http_client=scripted.client), max_retries=0)(
        current, context()
    )
    assert draft.steps[0].args.product_id == "prd_a"
    assert draft.steps[0].arg_sources[-1].source.template == "available_less_7d_charges"
    assert current["parse_status"] == "SCOPED" and current["understanding"].needs_write is True


@pytest.mark.parametrize(
    "change",
    [
        {"parse_status": "CLARIFYING", "clarification": {"required_fields": ["product_id"]}},
        {"parse_status": "NO_WRITE"},
        {"clarification_id": "clr_open"},
        {"parent_ids": ()},
    ],
)
def test_x3_does_not_read_selected_product_before_actual_clarification_is_complete(change):
    current = selected_state()
    current.update(change)
    calls = []
    records = GatherAdapter(
        StopModel(), x3_executor(calls), fetch_business_date=True, supplement_transfer_reads=True
    )(current, context())
    assert records == [] and all(
        tool not in {"get_product", "upcoming_charges", "get_risk_profile"} for tool, _ in calls
    )


def test_x3_gather_does_not_override_a_read_only_understanding():
    from services.agent.app.graph.state import Understanding

    current = selected_state()
    current["understanding"] = Understanding(intents=["query"], needs_write=False)
    before = deepcopy(current)
    calls = []
    records = GatherAdapter(
        StopModel(), x3_executor(calls), fetch_business_date=True, supplement_transfer_reads=True
    )(current, context())
    assert records == [] and current == before
    assert all(tool != "get_product" for tool, _ in calls)


@pytest.mark.parametrize(
    "failed_tool",
    ["get_profile", "get_accounts", "upcoming_charges", "get_risk_profile", "get_product"],
)
def test_x3_required_read_failures_do_not_use_model_or_choose_another_product(failed_tool):
    base = x3_executor([])

    class NoInference(StopModel):
        def invoke(self, _messages):
            raise AssertionError("must not infer after a required read fails")

    def read(tool, args):
        if tool == failed_tool:
            raise RuntimeError("dependency unavailable")
        return base(tool, args)

    with pytest.raises(RuntimeError, match="dependency unavailable"):
        GatherAdapter(
            NoInference(), read, fetch_business_date=True, supplement_transfer_reads=True
        )(selected_state(), context())


@pytest.mark.parametrize("status", [402, 403, 429])
def test_x3_profile_limits_propagate_without_retry_or_inference(status):
    class ProviderLimit(Exception):
        status_code = status

    calls = []

    def read(tool, args):
        calls.append((tool, args))
        raise ProviderLimit()

    with pytest.raises(ProviderLimit):
        GatherAdapter(StopModel(), read, fetch_business_date=True, supplement_transfer_reads=True)(
            selected_state(), context()
        )
    assert calls == [("get_profile", {})]


def test_x3_preserves_an_invalid_assessment_as_data_for_l2_without_marking_it_valid():
    base = x3_executor([])

    def read(tool, args):
        result = base(tool, args)
        if tool == "get_risk_profile":
            return result.model_copy(
                update={"data": dict(result.data, level=None, is_valid=False, valid_until=None)}
            )
        return result

    current = selected_state()
    before = deepcopy(current)
    records = GatherAdapter(
        StopModel(), read, fetch_business_date=True, supplement_transfer_reads=True
    )(current, context())
    profile = next(r for r in records if r.tool == "get_risk_profile")
    assert profile.data["is_valid"] is False and profile.data["level"] is None
    assert current == before and "decision" not in current and "execution" not in current


def test_x3_does_not_accept_a_product_read_for_a_different_selected_product():
    from services.agent.app.llm._common import LlmToolError

    base = x3_executor([])

    def read(tool, args):
        result = base(tool, args)
        return (
            result.model_copy(update={"data": dict(result.data, id="prd_other")})
            if tool == "get_product"
            else result
        )

    with pytest.raises(LlmToolError, match="G20 selection"):
        GatherAdapter(StopModel(), read, fetch_business_date=True, supplement_transfer_reads=True)(
            selected_state(), context()
        )


def test_x3_scope_amount_never_becomes_a_read_argument_or_calculated_fund_amount():
    current = selected_state()
    current["hints"][0]["scope"]["fields"]["amount_cents"] = 99999999
    before = deepcopy(current)
    calls = []
    records = GatherAdapter(
        StopModel(), x3_executor(calls), fetch_business_date=True, supplement_transfer_reads=True
    )(current, context())
    assert all("amount_cents" not in args for _, args in calls)
    assert (
        next(r for r in records if r.tool == "upcoming_charges").data["by_account"][0][
            "max_outflow_cents"
        ]
        == 600000
    )
    assert current == before


def _selected_product_plan():
    return {
        "intents": ["subscribe_product"],
        "rationale": "按用户已选择产品预留扣费后申购",
        "steps": [
            {
                "step_id": "invest",
                "tool": "subscribe_product",
                "args": {"account_id": "acc_zs_chk", "product_id": "prd_a", "amount_cents": 600000},
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
                            "ref": "src_actual_get_product",
                            "pointer": "/id",
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


def test_compiled_g20_ingest_selected_x3_reaches_one_plan_and_waits_for_real_approval():
    from services.agent.app.llm.understand import UnderstandAdapter
    from tests.agent.gateway.transport import json_response
    from tests.agent.routing.test_graph_compiled import (
        _g4_require_approval,
        _gateway_graph,
        _invoke,
    )
    from tests.agent.test_selection_context import ContextUnderstanding

    selected = selected_state()
    scope = selected["hints"][0]["scope"]
    g20 = {
        "id": "utt_selected",
        "kind": "action",
        "text": None,
        "scope": scope,
        "parse_status": "SCOPED",
        "echoes_untrusted": False,
        "parent_ids": ["utt_original_x3"],
        "clarification_id": None,
        "created_at_wall": "2026-10-31T00:00:00Z",
        "expires_at_wall": "2026-10-31T00:05:00Z",
    }
    calls = []
    scripted = ScriptedLLM([tool_response("submit_action_plan", _selected_product_plan())])
    graph, _, _ = _gateway_graph(
        [
            json_response(200, {"items": [g20]}),
            json_response(200, {"clarification": None}),
            json_response(200, dict(_g4_require_approval("pln_x3", "apv_x3"), risk="RED")),
        ],
        understand=UnderstandAdapter(ContextUnderstanding()),
        gather=GatherAdapter(
            StopModel(),
            x3_executor(calls),
            fetch_business_date=True,
            supplement_transfer_reads=True,
        ),
        plan=PlanAdapter(make_llm(api_key="synthetic", http_client=scripted.client)),
    )
    result = _invoke(graph, utterance_ids=("utt_selected",), session="ses_cross", customer="cus_zs")
    assert result["understanding"].needs_write is True
    assert result["utterances"][0].kind.value == "action" and result["utterances"][0].text == ""
    assert result["parent_ids"] == ("utt_original_x3",)
    assert result["hints"][0]["scope"]["fields"]["product_id"] == "prd_a"
    assert [r.tool for r in result["reads"]] == [
        "get_profile",
        "get_accounts",
        "upcoming_charges",
        "get_risk_profile",
        "get_product",
    ]
    assert result["plan_draft"].utterance_ids == ("utt_selected",)
    assert result["plan_draft"].steps[0].args.product_id == "prd_a"
    assert result["decision"]["decision"] == "REQUIRE_APPROVAL"
    assert result["decision"]["approval_id"] == "apv_x3"
    assert result["__interrupt__"][0].value["approval_id"] == "apv_x3"
    assert result.get("execution") is None
