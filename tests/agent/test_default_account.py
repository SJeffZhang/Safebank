"""L1 default source-account rule (PRD §7.6).

When the user names no source account, a transfer must use the customer's
single CHECKING account — never a SAVINGS guess.  When the user names a source
account ("从储蓄…"), the model's pick is kept.  Zero or multiple CHECKING
accounts cannot be resolved deterministically, so the plan node must raise
``ExhaustedClarify`` instead of guessing.
"""

from __future__ import annotations

import pytest

from services.agent.app.graph.state import AgentContext, ReadRecord, Understanding, UtteranceRef
from services.agent.app.llm.client import make_llm
from services.agent.app.llm.plan import ExhaustedClarify, PlanAdapter
from tests.agent.llm_mock import ScriptedLLM, tool_response

API_KEY = "test-key"

ONE_CHECKING = [
    {"id": "acc_zs_chk", "type": "CHECKING", "display_name": "活期账户"},
    {"id": "acc_zs_sav", "type": "SAVINGS", "display_name": "储蓄账户"},
]


def _ctx() -> AgentContext:
    return AgentContext(assistant_token="SECRET_TOKEN", customer_id="cus_a", session_id="ses_a")


def _transfer_plan(from_account_id: str = "acc_zs_sav") -> dict:
    return {
        "intents": ["transfer"],
        "steps": [
            {
                "step_id": "s1",
                "tool": "transfer",
                "args": {
                    "from_account_id": from_account_id,
                    "payee_id": {"kind": "id", "id": "pye_ww"},
                    "to_account_id": {"kind": "none"},
                    "amount_cents": 10000,
                    "memo": "__NULL__",
                },
                "depends_on": [],
                "arg_sources": [],
            }
        ],
        "rationale": "test",
    }


def _state(utterance_text: str, accounts: list[dict]) -> dict:
    return {
        "customer_id": "cus_a",
        "session_id": "ses_a",
        "utterances": [
            UtteranceRef(id="utt_typed", kind="typed", text=utterance_text),
        ],
        "understanding": Understanding(intents=["transfer"], needs_write=True),
        "reads": [
            ReadRecord(
                source_ref="src_acc",
                tool="get_accounts",
                args={},
                data={"accounts": accounts},
            )
        ],
    }


def _accounts_read(source_ref: str, accounts: list[dict]) -> ReadRecord:
    return ReadRecord(
        source_ref=source_ref,
        tool="get_accounts",
        args={},
        data={"accounts": accounts},
    )


def _state_with(utterances: list[UtteranceRef], reads: list[ReadRecord]) -> dict:
    return {
        "customer_id": "cus_a",
        "session_id": "ses_a",
        "utterances": utterances,
        "understanding": Understanding(intents=["transfer"], needs_write=True),
        "reads": reads,
    }


# ── RED: unspecified source → CHECKING, named source → kept, ambiguity → clarify ──


def test_unspecified_source_uses_checking_account():
    scripted = ScriptedLLM([tool_response("submit_action_plan", _transfer_plan("acc_zs_sav"))])
    llm = make_llm(api_key=API_KEY, http_client=scripted.client)
    adapter = PlanAdapter(llm)
    draft = adapter(_state("给王五转100元", ONE_CHECKING), _ctx())
    step = draft.steps[0]
    assert step.args.from_account_id == "acc_zs_chk"
    sources = [s for s in step.arg_sources if s.param == "from_account_id"]
    assert len(sources) == 1
    assert sources[0].source.kind == "read"
    assert sources[0].source.ref == "src_acc"
    assert sources[0].source.pointer == "/accounts/0/id"


def test_named_savings_source_keeps_savings():
    scripted = ScriptedLLM([tool_response("submit_action_plan", _transfer_plan("acc_zs_sav"))])
    llm = make_llm(api_key=API_KEY, http_client=scripted.client)
    adapter = PlanAdapter(llm)
    draft = adapter(_state("从储蓄账户给王五转100元", ONE_CHECKING), _ctx())
    assert draft.steps[0].args.from_account_id == "acc_zs_sav"


def test_two_checking_accounts_raises_clarify():
    accounts = [
        {"id": "acc_zs_chk1", "type": "CHECKING"},
        {"id": "acc_zs_chk2", "type": "CHECKING"},
        {"id": "acc_zs_sav", "type": "SAVINGS"},
    ]
    scripted = ScriptedLLM([tool_response("submit_action_plan", _transfer_plan("acc_zs_sav"))])
    llm = make_llm(api_key=API_KEY, http_client=scripted.client)
    adapter = PlanAdapter(llm)
    with pytest.raises(ExhaustedClarify) as exc_info:
        adapter(_state("给王五转100元", accounts), _ctx())
    assert "from_account_id" in exc_info.value.fields
    assert "付款账户" in exc_info.value.labels


# ── RED (round 2): "named source" must be current-turn + source-anchored ──


def _assert_override_to_checking(draft, checking_id: str = "acc_zs_chk") -> None:
    step = draft.steps[0]
    assert step.args.from_account_id == checking_id
    sources = [s for s in step.arg_sources if s.param == "from_account_id"]
    assert len(sources) == 1
    assert sources[0].source.kind == "read"


def test_target_mention_is_not_named_source():
    scripted = ScriptedLLM([tool_response("submit_action_plan", _transfer_plan("acc_zs_sav"))])
    llm = make_llm(api_key=API_KEY, http_client=scripted.client)
    adapter = PlanAdapter(llm)
    draft = adapter(_state("转到他的活期", ONE_CHECKING), _ctx())
    _assert_override_to_checking(draft)


def test_payee_name_keyword_is_not_named_source():
    scripted = ScriptedLLM([tool_response("submit_action_plan", _transfer_plan("acc_zs_sav"))])
    llm = make_llm(api_key=API_KEY, http_client=scripted.client)
    adapter = PlanAdapter(llm)
    draft = adapter(_state("转给储蓄客户王五", ONE_CHECKING), _ctx())
    _assert_override_to_checking(draft)


def test_negation_is_not_named_source():
    scripted = ScriptedLLM([tool_response("submit_action_plan", _transfer_plan("acc_zs_sav"))])
    llm = make_llm(api_key=API_KEY, http_client=scripted.client)
    adapter = PlanAdapter(llm)
    draft = adapter(_state("不要用活期", ONE_CHECKING), _ctx())
    _assert_override_to_checking(draft)


def test_earlier_turn_keyword_is_not_named_source():
    scripted = ScriptedLLM([tool_response("submit_action_plan", _transfer_plan("acc_zs_sav"))])
    llm = make_llm(api_key=API_KEY, http_client=scripted.client)
    adapter = PlanAdapter(llm)
    utterances = [
        UtteranceRef(id="utt_prev", kind="typed", text="我活期余额多少"),
        UtteranceRef(id="utt_typed", kind="typed", text="给王五转100元"),
    ]
    draft = adapter(_state_with(utterances, [_accounts_read("src_acc", ONE_CHECKING)]), _ctx())
    _assert_override_to_checking(draft)


def test_from_savings_with_amount_is_named_source():
    scripted = ScriptedLLM([tool_response("submit_action_plan", _transfer_plan("acc_zs_sav"))])
    llm = make_llm(api_key=API_KEY, http_client=scripted.client)
    adapter = PlanAdapter(llm)
    draft = adapter(_state("从储蓄转500给王五", ONE_CHECKING), _ctx())
    assert draft.steps[0].args.from_account_id == "acc_zs_sav"


def test_duplicate_checking_reads_do_not_clarify():
    scripted = ScriptedLLM([tool_response("submit_action_plan", _transfer_plan("acc_zs_sav"))])
    llm = make_llm(api_key=API_KEY, http_client=scripted.client)
    adapter = PlanAdapter(llm)
    reads = [
        _accounts_read("src_acc_1", ONE_CHECKING),
        _accounts_read("src_acc_2", ONE_CHECKING),
    ]
    draft = adapter(
        _state_with([UtteranceRef(id="utt_typed", kind="typed", text="给王五转100元")], reads),
        _ctx(),
    )
    _assert_override_to_checking(draft)
