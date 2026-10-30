"""Lost card plans require current owned card reads, never guessed IDs."""

import pytest

from services.agent.app.graph.state import ReadRecord
from services.agent.app.llm.gather import GatherAdapter, ReadResult
from services.agent.app.llm.plan import ExhaustedClarify, PlanAdapter, ReferenceError
from tests.agent.llm_mock import ScriptedLLM, text_response, tool_response
from tests.agent.test_llm_adapters import _make_gather_state, build, ctx


def state(*, cards=None, text="我的卡丢了，帮我挂失"):
    current = _make_gather_state(intents=["card"], text=text)
    current["reads"] = [
        ReadRecord(
            source_ref="src_cards",
            tool="get_cards",
            data={"items": cards or [{"id": "crd_a", "current": True, "last4": "1234"}]},
        )
    ]
    return current


def proposal(*, with_source=True):
    return {
        "intents": ["card"],
        "steps": [
            {
                "step_id": "s1",
                "tool": "report_card_lost",
                "args": {"card_id": "crd_a", "replace": False},
                "depends_on": [],
                "arg_sources": (
                    [
                        {
                            "param": "card_id",
                            "source": {
                                "kind": "read",
                                "ref": "src_cards",
                                "pointer": "/items/0/id",
                            },
                        }
                    ]
                    if with_source
                    else []
                ),
            }
        ],
        "rationale": "挂失本人卡片",
    }


def test_lost_card_without_card_read_source_is_retried():
    scripted = ScriptedLLM(
        [
            tool_response("submit_action_plan", proposal(with_source=False)),
            tool_response("submit_action_plan", proposal()),
        ]
    )
    draft = PlanAdapter(build(scripted))(state(), ctx())
    assert len(scripted.requests) == 2
    assert draft.steps[0].arg_sources[0].source.ref == "src_cards"


def test_exhausted_missing_card_source_cannot_leave_planner():
    scripted = ScriptedLLM([tool_response("submit_action_plan", proposal(with_source=False))])
    with pytest.raises(ReferenceError):
        PlanAdapter(build(scripted), max_retries=0)(state(), ctx())


def test_lost_card_gather_reads_cards_even_if_model_omits_read():
    calls = []

    def executor(name, args):
        calls.append((name, args))
        return ReadResult(data={"items": []}, source_ref="src_cards")

    scripted = ScriptedLLM([text_response("done")])
    records = GatherAdapter(build(scripted), executor, supplement_transfer_reads=True)(
        state(), ctx()
    )
    assert calls == [("get_cards", {})]
    assert records[0].tool == "get_cards"


def test_ambiguous_multiple_cards_ask_instead_of_guessing():
    current = state(
        cards=[
            {"id": "crd_a", "current": True, "last4": "1234"},
            {"id": "crd_b", "current": True, "last4": "5678"},
        ]
    )
    scripted = ScriptedLLM([tool_response("submit_action_plan", proposal())])
    with pytest.raises(ExhaustedClarify):
        PlanAdapter(build(scripted))(current, ctx())


def test_card_id_cannot_be_laundered_through_other_read_tool():
    current = state()
    current["reads"][0] = ReadRecord(
        source_ref="src_cards", tool="get_transactions", data={"items": [{"id": "crd_a"}]}
    )
    scripted = ScriptedLLM([tool_response("submit_action_plan", proposal())])
    with pytest.raises(ReferenceError):
        PlanAdapter(build(scripted), max_retries=0)(current, ctx())


def test_unique_explicit_card_tail_can_continue_to_gateway_validation():
    current = state(
        cards=[
            {"id": "crd_a", "current": True, "last4": "1234"},
            {"id": "crd_b", "current": True, "last4": "5678"},
        ],
        text="挂失尾号1234的卡",
    )
    scripted = ScriptedLLM([tool_response("submit_action_plan", proposal())])
    assert PlanAdapter(build(scripted))(current, ctx()).steps[0].args.card_id == "crd_a"
