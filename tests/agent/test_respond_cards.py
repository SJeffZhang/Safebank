"""C1e respond card-facts tests (card tail bug fix, 2026-10-29).

The executed prompt must map card_id → last4 so the LLM never quotes the
card ID as if it were the tail.
"""

from __future__ import annotations

from types import SimpleNamespace

from services.agent.app.graph.state import ReadRecord
from services.agent.app.llm.respond import (
    _build_executed_user_prompt,
    _card_facts,
    _card_facts_block,
    _collect_card_facts,
)


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


class TestCardFactsDataShapes:
    def test_pydantic_data(self):
        """A read whose ``data`` is a pydantic model is walked via model_dump."""
        from pydantic import BaseModel

        class CardData(BaseModel):
            id: str
            last4: str

        facts: dict[str, str] = {}
        _collect_card_facts(CardData(id="crd_zs_1", last4="1234"), facts)
        assert facts == {"crd_zs_1": "1234"}

    def test_plain_dict_read_record(self):
        """A read record that is a plain dict (not ReadRecord) is handled."""
        state = {
            "reads": [
                {
                    "data": {"cards": [{"id": "crd_zs_1", "last4": "1234"}]},
                },
            ],
        }
        assert _card_facts(state) == {"crd_zs_1": "1234"}


class TestCardFactsBlock:
    def test_no_card_id_in_block(self):
        """The card-facts block must not re-emit the card ID."""
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
        block = _card_facts_block(state)
        assert block == "- 尾号1234"
        assert "crd_" not in block

    def test_sorted_deduplicated(self):
        """Multiple cards → sorted, de-duplicated last4 lines only."""
        state = {
            "reads": [
                ReadRecord(
                    source_ref="src_a",
                    tool="get_cards",
                    args={},
                    data={
                        "cards": [
                            {"id": "crd_2", "last4": "2222"},
                            {"id": "crd_1", "last4": "1111"},
                            {"id": "crd_3", "last4": "1111"},
                        ]
                    },
                ),
            ],
        }
        block = _card_facts_block(state)
        assert block == "- 尾号1111\n- 尾号2222"


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

    def test_prompt_has_no_card_id(self):
        """With no card ID in the step results, the prompt must be ID-free."""
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
                    "result": {},
                }
            ],
        }
        prompt = _build_executed_user_prompt(state, execution)
        assert "crd_" not in prompt
        assert "尾号1234" in prompt
