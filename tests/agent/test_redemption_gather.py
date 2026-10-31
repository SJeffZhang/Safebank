"""Redemption planning receives actual holdings, never invented read references."""

import pytest

from services.agent.app.llm.client import make_llm
from services.agent.app.llm.gather import GatherAdapter, ReadResult
from tests.agent.llm_mock import ScriptedLLM, text_response, tool_response
from tests.agent.test_funds_prompt import _ctx, _funds_state

PRODUCT_ARGS = {"max_risk_level": None, "product_type": None, "cursor": None, "limit": 50}
HOLDING_ARGS = {"account_id": None, "product_id": None}


def redemption_state():
    return _funds_state("赎回安心货币1000份", [], ["redeem_product"])


def adapter(scripted, executor):
    return GatherAdapter(
        make_llm(api_key="test-key", http_client=scripted.client),
        executor,
        supplement_transfer_reads=True,
    )


def result_for(tool):
    return ReadResult(
        source_ref="src_actual_" + tool,
        data={
            "get_accounts": {"accounts": [{"id": "acc_ls_chk", "type": "CHECKING"}]},
            "list_products": {"items": [{"id": "prd_mmf", "name": "安心货币"}]},
            "get_holdings": {
                "holdings": [
                    {"account_id": "acc_ls_chk", "product_id": "prd_mmf", "units": "10000.0000"}
                ],
                "orders": [],
            },
        }[tool],
    )


def test_redemption_loads_missing_holdings_and_product_context():
    executed = []

    def execute(tool, args):
        executed.append((tool, args))
        return result_for(tool)

    scripted = ScriptedLLM(
        [tool_response("get_accounts", {"history_days": 0}), text_response("done")]
    )
    reads = adapter(scripted, execute)(redemption_state(), _ctx())
    assert executed == [
        ("get_accounts", {"history_days": 0}),
        ("list_products", PRODUCT_ARGS),
        ("get_holdings", HOLDING_ARGS),
    ]
    assert {read.source_ref for read in reads} == {
        "src_actual_get_accounts",
        "src_actual_list_products",
        "src_actual_get_holdings",
    }
    assert reads[-1].data["holdings"][0]["units"] == "10000.0000"


def test_redemption_does_not_repeat_successful_reads():
    executed = []

    def execute(tool, args):
        executed.append((tool, args))
        return result_for(tool)

    scripted = ScriptedLLM(
        [
            tool_response("get_holdings", HOLDING_ARGS),
            tool_response("list_products", PRODUCT_ARGS),
            text_response("done"),
        ]
    )
    reads = adapter(scripted, execute)(redemption_state(), _ctx())
    assert len(reads) == 3
    assert [name for name, _ in executed].count("get_holdings") == 1
    assert [name for name, _ in executed].count("list_products") == 1
    assert [name for name, _ in executed].count("get_accounts") == 1


def test_redemption_required_read_failure_propagates():
    def execute(tool, args):
        if tool == "get_holdings":
            raise RuntimeError("holdings unavailable")
        return result_for(tool)

    scripted = ScriptedLLM([text_response("done")])
    with pytest.raises(RuntimeError, match="holdings unavailable"):
        adapter(scripted, execute)(redemption_state(), _ctx())


def test_readonly_product_question_does_not_force_redemption_context():
    executed = []
    state = redemption_state()
    state["understanding"] = state["understanding"].model_copy(update={"needs_write": False})
    scripted = ScriptedLLM([text_response("done")])
    reads = adapter(scripted, lambda *args: executed.append(args))(state, _ctx())
    assert reads == []
    assert executed == []
