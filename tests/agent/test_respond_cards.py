"""C1e respond card-facts tests (card tail bug fix, 2026-10-29).

The executed prompt must map card_id → last4 so the LLM never quotes the
card ID as if it were the tail.
"""

from __future__ import annotations

from types import SimpleNamespace

from services.agent.app.graph.state import ReadRecord
from services.agent.app.llm.respond import _build_executed_user_prompt, _card_facts


class TestCardFacts:
    def test_maps_card_id_to_last4(self):
        state = {
            "reads": [
                ReadRecord(
                    source_ref="src_a",
                    tool="get_cards",
                    args={},
                    data={"cards": [{"id": "crd_zs_1", "last4": "1234"}]},
                ),
            ],
        }
        assert _card_facts(state) == {"crd_zs_1": "1234"}

    def test_card_id_key_variant(self):
        state = {
            "reads": [
                ReadRecord(
                    source_ref="src_a",
                    tool="get_cards",
                    args={},
                    data={"items": [{"card_id": "crd_zs_1", "last4": "1234"}]},
                ),
            ],
        }
        assert _card_facts(state) == {"crd_zs_1": "1234"}

    def test_no_cards(self):
        assert _card_facts({"reads": []}) == {}


class TestExecutedPrompt:
    def test_prompt_contains_card_facts(self):
        state = {
            "reads": [
                ReadRecord(
                    source_ref="src_a",
                    tool="get_cards",
                    args={},
                    data={"cards": [{"id": "crd_zs_1", "last4": "1234"}]},
                ),
            ],
            "utterances": [SimpleNamespace(text="解冻尾号 zs_1 的卡")],
        }
        execution = {
            "status": "EXECUTED",
            "steps": [
                {
                    "step_id": "s1",
                    "tool": "unfreeze_card",
                    "status": "SUCCESS",
                    "result": {"card_id": "crd_zs_1"},
                }
            ],
        }
        prompt = _build_executed_user_prompt(state, execution)
        assert "1234" in prompt
        assert "尾号1234" in prompt
