"""L1 prompt + local validation for the wealth-management (理财) scenario.

L3 subscribe_product / redeem_product / get_holdings are being implemented in
parallel; the contracts already define them and L1 builds its tools from
``safebank_contracts.tools``.  These tests pin the L1 behaviour:

- ``subscribe_product.product_id`` must be read-sourced from list_products /
  get_product; amount is from the user's words or the ``available_less_7d_charges``
  derived template; suitability is never decided by L1 (the gateway does).
- ``redeem_product.units`` comes from explicit user quantity; holdings are availability data.
- Product questions are read-only.
- X-3 "把下周要扣的会员费留够，剩下的钱买稳健型理财": when more than one
  稳健/平衡 product exists, L1 must surface a clarification for ``product_id``
  instead of silently picking one.
"""

from __future__ import annotations

import pytest

from services.agent.app.graph.state import AgentContext, ReadRecord, Understanding, UtteranceRef
from services.agent.app.llm.client import make_llm
from services.agent.app.llm.gather import GatherAdapter
from services.agent.app.llm.plan import ExhaustedClarify, PlanAdapter, ReferenceError
from tests.agent.llm_mock import ScriptedLLM, text_response, tool_response

API_KEY = "test-key"


def _ctx() -> AgentContext:
    return AgentContext(assistant_token="SECRET_TOKEN", customer_id="cus_a", session_id="ses_a")


def _accounts_read() -> ReadRecord:
    return ReadRecord(
        source_ref="src_accounts",
        tool="get_accounts",
        args={"history_days": 0},
        data={"accounts": [{"id": "acc_zs_chk", "type": "CHECKING", "balance_cents": 600000}]},
    )


def _products_read(*products: dict) -> ReadRecord:
    return ReadRecord(
        source_ref="src_products",
        tool="list_products",
        args={},
        data={"items": list(products), "next_cursor": None},
    )


def _holdings_read(units: str = "10.0000") -> ReadRecord:
    return ReadRecord(
        source_ref="src_holdings",
        tool="get_holdings",
        args={},
        data={"holdings": [{"product_id": "prd_1", "units": units}], "orders": []},
    )


def _funds_state(utterance_text: str, reads: list, intents: list) -> dict:
    return {
        "customer_id": "cus_a",
        "session_id": "ses_a",
        "utterances": [UtteranceRef(id="utt_typed", kind="typed", text=utterance_text)],
        "understanding": Understanding(intents=intents, needs_write=True),
        "reads": reads,
    }


def _subscribe_plan(amount_source: dict | None = None) -> dict:
    sources = [
        {
            "param": "account_id",
            "source": {"kind": "read", "ref": "src_accounts", "pointer": "/accounts/0/id"},
        },
        {
            "param": "product_id",
            "source": {"kind": "read", "ref": "src_products", "pointer": "/items/0/id"},
        },
    ]
    if amount_source is not None:
        sources.append({"param": "amount_cents", "source": amount_source})
    return {
        "intents": ["subscribe_product"],
        "steps": [
            {
                "step_id": "s1",
                "tool": "subscribe_product",
                "args": {
                    "account_id": "acc_zs_chk",
                    "product_id": "prd_1",
                    "amount_cents": 80000,
                },
                "depends_on": [],
                "arg_sources": sources,
            }
        ],
        "rationale": "test",
    }


def _redeem_plan(units_source: dict) -> dict:
    return {
        "intents": ["redeem_product"],
        "steps": [
            {
                "step_id": "s1",
                "tool": "redeem_product",
                "args": {"account_id": "acc_zs_chk", "product_id": "prd_1", "units": "10.0000"},
                "depends_on": [],
                "arg_sources": [{"param": "units", "source": units_source}],
            }
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


def test_plan_prompt_contains_funds_rules():
    scripted = ScriptedLLM([tool_response("submit_action_plan", _subscribe_plan())])
    llm = make_llm(api_key=API_KEY, http_client=scripted.client)
    state = _funds_state(
        "买800元稳健型理财",
        [_accounts_read(), _products_read({"id": "prd_1", "risk_level": 2, "risk_label": "稳健"})],
        ["subscribe_product"],
    )
    PlanAdapter(llm)(state, _ctx())
    prompt = _system_prompt(scripted)
    assert "subscribe_product 的 product_id 必须从已读产品数据" in prompt
    assert "redeem_product 的 units" in prompt
    assert "不要自行判断适当性" in prompt
    assert "available_less_7d_charges" in prompt
    assert "不要自行挑一个产品" in prompt


def test_gather_prompt_contains_funds_rules():
    scripted = ScriptedLLM([text_response("")])
    llm = make_llm(api_key=API_KEY, http_client=scripted.client)
    adapter = GatherAdapter(llm, read_executor=lambda name, args: None)
    adapter(_funds_state("买理财", [], []), _ctx())
    prompt = _system_prompt(scripted)
    assert "list_products" in prompt
    assert "get_holdings" in prompt
    assert "get_risk_profile" in prompt
    assert "upcoming_charges" in prompt


# ── subscribe product_id read-sourced passes ──


def test_subscribe_read_sourced_product_id_passes():
    amount_source = {"kind": "utterance", "ref": "utt_typed", "span": [1, 6]}
    scripted = ScriptedLLM([tool_response("submit_action_plan", _subscribe_plan(amount_source))])
    llm = make_llm(api_key=API_KEY, http_client=scripted.client)
    draft = PlanAdapter(llm)(
        _funds_state(
            "买800元稳健型理财",
            [
                _accounts_read(),
                _products_read({"id": "prd_1", "risk_level": 2, "risk_label": "稳健"}),
            ],
            ["subscribe_product"],
        ),
        _ctx(),
    )
    step = draft.steps[0]
    assert step.tool == "subscribe_product"
    product_sources = [s for s in step.arg_sources if s.param == "product_id"]
    assert len(product_sources) == 1
    assert product_sources[0].source.kind == "read"
    assert product_sources[0].source.ref == "src_products"


# ── redeem quantity spans must be exact ──


def test_redeem_invented_units_rejected():
    units_source = {"kind": "utterance", "ref": "utt_typed", "span": [0, 4]}
    plan = _redeem_plan(units_source)
    scripted = ScriptedLLM(
        [tool_response("submit_action_plan", plan), tool_response("submit_action_plan", plan)]
    )
    llm = make_llm(api_key=API_KEY, http_client=scripted.client)
    with pytest.raises(ReferenceError) as exc_info:
        PlanAdapter(llm)(
            _funds_state("赎回10份", [_accounts_read(), _holdings_read()], ["redeem_product"]),
            _ctx(),
        )
    assert any("赎回份额" in problem for problem in exc_info.value.problems)


# ── X-3 ambiguous 稳健型 → clarify product_id ──


def test_x3_multiple_steady_products_clarifies():
    products = _products_read(
        {"id": "prd_1", "risk_level": 2, "risk_label": "稳健"},
        {"id": "prd_2", "risk_level": 2, "risk_label": "稳健"},
    )
    plan = _subscribe_plan()
    scripted = ScriptedLLM([tool_response("submit_action_plan", plan)])
    llm = make_llm(api_key=API_KEY, http_client=scripted.client)
    with pytest.raises(ExhaustedClarify) as exc_info:
        PlanAdapter(llm)(
            _funds_state(
                "把下周要扣的会员费留够，剩下的钱买稳健型理财",
                [products],
                ["subscribe_product"],
            ),
            _ctx(),
        )
    assert "product_id" in exc_info.value.fields
    assert "理财产品" in exc_info.value.labels
