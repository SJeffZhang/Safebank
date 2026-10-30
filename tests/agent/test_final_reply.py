"""SSE reply must be the verified final message, not the raw draft."""

from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

from services.agent.app.main import _final_reply


def _state(values, next_=()):
    return SimpleNamespace(values=values, next=next_)


def test_finished_run_returns_verified_message():
    values = {"reply_draft": "卡号6222021234567890", "messages": [AIMessage(content="卡号****7890")]}
    assert _final_reply(_state(values)) == "卡号****7890"


def test_interrupted_run_ignores_previous_turn_message():
    values = {"reply_draft": "", "messages": [AIMessage(content="上一轮的回复")]}
    assert _final_reply(_state(values, next_=("await_approval",))) == ""


def test_falls_back_to_draft_without_ai_message():
    values = {"reply_draft": "草稿", "messages": [HumanMessage(content="hi")]}
    assert _final_reply(_state(values)) == "草稿"


def test_empty_state():
    assert _final_reply(None) == ""
