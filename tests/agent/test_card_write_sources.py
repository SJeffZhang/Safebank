"""Card write plans cite a real get_cards read for card_id (Phase D D2).

「冻结尾号1234的卡」: the typed tail only identifies the card; the card id itself must
come from the owned card read, and that read is supplied even when the model's own
get_cards attempt failed.
"""

import pytest
from safebank_contracts.codes import Code

from services.agent.app.gateway.errors import GatewayResponseError
from services.agent.app.graph.state import ReadRecord
from services.agent.app.llm.gather import GatherAdapter, ReadResult
from services.agent.app.llm.plan import PlanAdapter, ReferenceError
from tests.agent.llm_mock import ScriptedLLM, text_response, tool_response
from tests.agent.test_llm_adapters import _make_gather_state, build, ctx

TEXT = "冻结尾号1234的卡"


def state(text=TEXT, tool="get_cards"):
    current = _make_gather_state(intents=["card"], text=text)
    current["reads"] = [
        ReadRecord(
            source_ref="src_cards",
            tool=tool,
            data={"items": [{"id": "crd_a", "current": True, "last4": "1234"}]},
        )
    ]
    return current


CASES = {
    "freeze_card": ({}, []),
    "unfreeze_card": ({}, []),
    "update_card_limits": (
        {"single_limit_cents": 100000, "daily_limit_cents": None},
        [("single_limit_cents", [7, 11])],
    ),
    "update_card_controls": (
        {"online": "OFF", "overseas": "KEEP", "atm": "KEEP", "quickpass": "KEEP"},
        [("online", [0, 2])],
    ),
}


def proposal(tool, *, card_source):
    extra_args, extra_sources = CASES[tool]
    return {
        "intents": ["card"],
        "steps": [
            {
                "step_id": "s1",
                "tool": tool,
                "args": {"card_id": "crd_a", **extra_args},
                "depends_on": [],
                "arg_sources": [{"param": "card_id", "source": card_source}]
                + [
                    {
                        "param": param,
                        "source": {"kind": "utterance", "ref": "utt_a", "span": span},
                    }
                    for param, span in extra_sources
                ],
            }
        ],
        "rationale": "卡片操作",
    }


READ_SOURCE = {"kind": "read", "ref": "src_cards", "pointer": "/items/0/id"}
TAIL_SPAN = {"kind": "utterance", "ref": "utt_a", "span": [4, 8]}


def test_tail_span_as_card_id_source_is_retried_with_feedback():
    scripted = ScriptedLLM(
        [
            tool_response("submit_action_plan", proposal("freeze_card", card_source=TAIL_SPAN)),
            tool_response("submit_action_plan", proposal("freeze_card", card_source=READ_SOURCE)),
        ]
    )
    draft = PlanAdapter(build(scripted))(state(), ctx())
    assert len(scripted.requests) == 2
    assert draft.steps[0].arg_sources[0].source.ref == "src_cards"
    feedback = str(scripted.requests[1])
    assert "get_cards" in feedback


@pytest.mark.parametrize("tool", sorted(CASES))
def test_every_card_write_needs_a_card_read_source(tool):
    current = state(text="冻结尾号1234的卡")
    scripted = ScriptedLLM(
        [tool_response("submit_action_plan", proposal(tool, card_source=TAIL_SPAN))]
    )
    with pytest.raises(ReferenceError, match="get_cards"):
        PlanAdapter(build(scripted), max_retries=0)(current, ctx())


def test_card_id_from_another_read_tool_is_rejected():
    scripted = ScriptedLLM(
        [tool_response("submit_action_plan", proposal("freeze_card", card_source=READ_SOURCE))]
    )
    with pytest.raises(ReferenceError, match="get_cards"):
        PlanAdapter(build(scripted), max_retries=0)(state(tool="get_transactions"), ctx())


def test_card_read_source_is_accepted():
    scripted = ScriptedLLM(
        [tool_response("submit_action_plan", proposal("freeze_card", card_source=READ_SOURCE))]
    )
    draft = PlanAdapter(build(scripted))(state(), ctx())
    assert draft.steps[0].args.card_id == "crd_a"


def _executor(calls):
    def executor(name, args):
        calls.append((name, args))
        if args.get("account_id"):
            raise GatewayResponseError(
                status=404, code=Code.NOT_FOUND, details={}, trace_id="", decision=None
            )
        return ReadResult(data={"items": [{"id": "crd_a"}]}, source_ref="src_gather_cards")

    return executor


@pytest.mark.parametrize(
    "text", ["冻结尾号1234的卡", "解冻我的卡", "把卡的单笔限额调到1000元", "关闭境外支付"]
)
def test_failed_model_card_read_is_backed_by_owned_card_read(text):
    calls = []
    scripted = ScriptedLLM(
        [
            tool_response("get_cards", {"account_id": "acc_invented"}),
            text_response("done"),
        ]
    )
    records = GatherAdapter(build(scripted), _executor(calls), supplement_transfer_reads=True)(
        state(text=text), ctx()
    )
    assert calls == [
        ("get_cards", {"account_id": "acc_invented"}),
        ("get_cards", {"account_id": None}),
    ]
    assert [r.tool for r in records] == ["get_cards"]


def test_no_card_read_for_unrelated_text():
    calls = []
    scripted = ScriptedLLM([text_response("done")])
    GatherAdapter(build(scripted), _executor(calls), supplement_transfer_reads=True)(
        state(text="查一下我的余额"), ctx()
    )
    assert calls == []


def test_successful_model_card_read_is_not_repeated():
    calls = []
    scripted = ScriptedLLM(
        [tool_response("get_cards", {"account_id": None}), text_response("done")]
    )
    GatherAdapter(build(scripted), _executor(calls), supplement_transfer_reads=True)(state(), ctx())
    assert calls == [("get_cards", {"account_id": None})]
