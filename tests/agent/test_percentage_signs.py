"""Signed percentage facts preserve direction; money and bp sources stay unchanged."""

import pytest

from services.agent.app.graph.state import ReadRecord
from services.agent.app.llm.respond import RespondAdapter
from services.agent.app.llm.verify import (
    OutputVerifier,
    build_fact_set,
    check_against_facts,
    extract_amounts,
    extract_percentages,
)
from tests.agent.test_bill_accuracy import NoInference, _context, _state


@pytest.mark.parametrize(
    "text,value",
    [
        ("环比91.46%", 91.46),
        ("环比+91.46%", 91.46),
        ("环比-91.46%", -91.46),
        ("环比−91.46%", -91.46),
        ("环比－91.46%", -91.46),
        ("环比＋91.46%", 91.46),
        ("变化0%", 0),
        ("变化+0.00%", 0),
        ("变化-0.00%", 0),
    ],
)
def test_percentage_extraction_preserves_explicit_direction(text, value):
    items = extract_percentages(text)
    assert len(items) == 1
    assert items[0]["value"] == value
    assert items[0]["raw"] == text[2:]


@pytest.mark.parametrize(
    "bp,text", [(-9146, "环比-91.46%"), (9146, "环比+91.46%"), (0, "环比0.00%")]
)
def test_signed_bp_fact_survives_the_existing_output_verifier(bp, text):
    state = {
        "reads": [
            ReadRecord(
                source_ref="src_percentage", tool="get_spending_summary", data={"mom_change_bp": bp}
            )
        ],
        "reply_draft": text,
    }
    assert check_against_facts(extract_percentages(text), build_fact_set(state)) == []
    assert OutputVerifier()(state, None) == text


@pytest.mark.parametrize(
    "bp,text", [(-9146, "环比+91.46%"), (-9146, "环比91.46%"), (9146, "环比-91.46%")]
)
def test_opposite_or_unsigned_absolute_value_is_not_allowed(bp, text):
    state = {
        "reads": [
            ReadRecord(
                source_ref="src_percentage", tool="get_spending_summary", data={"mom_change_bp": bp}
            )
        ],
        "reply_draft": text,
    }
    assert check_against_facts(extract_percentages(text), build_fact_set(state))
    assert "无法处理" in OutputVerifier()(state, None)


def test_adjacent_yuan_amount_remains_integer_cents_and_does_not_supply_percentage_sign():
    text = "消费550.50元，环比-91.46%；预留400.00元。"
    assert [item["cents"] for item in extract_amounts(text)] == [55050, 40000]
    assert [item["value"] for item in extract_percentages(text)] == [-91.46]


def test_actual_declining_monthly_report_keeps_negative_bp_direction_through_verification():
    state = _state("给我出一份10月的账单报告")
    state["reads"] = [
        ReadRecord(
            tool="get_profile",
            source_ref="src_profile",
            data={"sim_now": "2026-10-15T09:00:00+08:00"},
        ),
        ReadRecord(
            tool="get_spending_summary",
            source_ref="src_summary",
            args={"month": "2026-10", "account_id": None},
            data={
                "month": "2026-10",
                "total_cents": 55050,
                "previous_total_cents": 644800,
                "mom_change_cents": -589750,
                "mom_change_bp": -9146,
                "categories": [],
            },
        ),
        ReadRecord(
            tool="detect_anomalies",
            source_ref="src_anomalies",
            data={"items": []},
            args={
                "from_at": "2026-10-01T00:00:00+08:00",
                "to_at": "2026-11-01T00:00:00+08:00",
                "account_id": None,
            },
        ),
    ]
    draft = RespondAdapter(NoInference())(state, _context())
    assert "-91.46%" in draft
    state["reply_draft"] = draft
    assert OutputVerifier()(state, _context()) == draft


@pytest.mark.parametrize("text", ["环比- 91.46%", "环比−\t91.46%"])
def test_sign_whitespace_cannot_turn_a_negative_percentage_into_an_unsigned_tail(text):
    extracted = extract_percentages(text)
    assert len(extracted) == 1 and extracted[0]["value"] == -91.46
    assert extracted[0]["raw"] == text[2:]


@pytest.mark.parametrize("bp,valid", [(-9146, True), (9146, False)])
def test_spaced_negative_percentage_still_requires_a_negative_source(bp, valid):
    text = "环比- 91.46%"
    state = {
        "reads": [
            ReadRecord(
                source_ref="src_percentage", tool="get_spending_summary", data={"mom_change_bp": bp}
            )
        ],
        "reply_draft": text,
    }
    assert (check_against_facts(extract_percentages(text), build_fact_set(state)) == []) is valid
    final = OutputVerifier()(state, None)
    assert (final == text) is valid
