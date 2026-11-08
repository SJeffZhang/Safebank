"""Card clarification choices keep their gateway-provided last-four labels (Phase D D1).

G21 candidate labels are produced by L2 from L3 card data. A reply listing them
must not be scrubbed to 「该卡」; a tail that no candidate or read supports still is.
"""

from services.agent.app.llm.verify import OutputVerifier, build_fact_set


def _state(draft, candidates):
    return {
        "reply_draft": draft,
        "reads": [],
        "clarification": {"id": "clr_1", "candidates": candidates},
    }


def _card(entity_id, last4):
    return {"field": "card_id", "entity_id": entity_id, "label": "尾号" + last4}


CARDS = [_card("crd_a", "1234"), _card("crd_b", "5678"), _card("crd_c", "9012")]


def test_card_choice_labels_survive_verification():
    draft = "请选择尾号1234、尾号5678、尾号9012 以继续。"
    out = OutputVerifier()(_state(draft, CARDS), None)
    assert out == draft


def test_tail_outside_the_candidates_is_still_scrubbed():
    out = OutputVerifier()(_state("请选择尾号1234、尾号0000 以继续。", CARDS), None)
    assert "尾号1234" in out
    assert "0000" not in out


def test_only_card_field_candidates_with_exact_label_count():
    candidates = [
        {"field": "payee_id", "entity_id": "pye_a", "label": "尾号1111"},
        {"field": "card_id", "entity_id": "crd_a", "label": "备用卡 尾号2222"},
    ]
    facts = build_fact_set(_state("", candidates))
    assert facts["last4s"] == []


def test_without_clarification_tails_are_scrubbed_as_before():
    out = OutputVerifier()({"reply_draft": "请选择尾号1234 以继续。", "reads": []}, None)
    assert "1234" not in out
