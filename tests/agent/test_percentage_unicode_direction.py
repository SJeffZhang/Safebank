"""Unicode signs and small bp facts must not erase negative/zero/positive direction."""

import pytest

from services.agent.app.llm.verify import OutputVerifier, check_against_facts, extract_percentages


@pytest.mark.parametrize("sign", ["-", "−", "－", "﹣", "⁻", "₋"])
@pytest.mark.parametrize("gap", ["\u00a0", "\u202f", "\u2003"])
def test_nfkc_compatible_single_minus_keeps_unicode_whitespace_and_original_span(sign, gap):
    text = f"环比{sign}{gap}91.46%"
    result = extract_percentages(text)
    assert len(result) == 1 and result[0]["value"] == -91.46
    assert result[0]["raw"] == text[2:]
    assert result[0]["span"] == (2, len(text))
    assert check_against_facts(result, {"percentages": [91.46]})
    assert check_against_facts(result, {"percentages": [-91.46]}) == []


@pytest.mark.parametrize("sign", ["＋", "﹢", "⁺"])
def test_compatible_plus_keeps_original_sign_span_with_unicode_gap(sign):
    text = f"环比{sign}\u202f91.46%"
    item = extract_percentages(text)[0]
    assert item["value"] == 91.46 and item["raw"] == text[2:]
    assert check_against_facts([item], {"percentages": [-91.46]})


@pytest.mark.parametrize(
    "bp,text,valid",
    [
        (1, "-0.01%", False),
        (-1, "+0.01%", False),
        (5, "-0.05%", False),
        (-5, "+0.05%", False),
        (0, "+0.01%", False),
        (0, "-0.01%", False),
        (1, "0%", False),
        (-1, "0%", False),
        (0, "-0.00%", True),
        (0, "+0.00%", True),
        (100, "+1.05%", True),
        (-100, "-1.05%", True),
    ],
)
def test_tolerance_requires_the_same_negative_zero_or_positive_direction(bp, text, valid):
    current = {"reply_draft": text, "reads": [{"data": {"mom_change_bp": bp}}]}
    final = OutputVerifier()(current, None)
    assert (final == text) is valid


@pytest.mark.parametrize("symbol", ["–", "—", "‑", "➖"])
def test_unsupported_dash_or_minus_cannot_disappear_and_borrow_a_positive_fact(symbol):
    text = f"环比{symbol}91.46%"
    extracted = extract_percentages(text)
    assert len(extracted) == 1
    assert check_against_facts(extracted, {"percentages": [91.46]})
    assert (
        OutputVerifier()({"reply_draft": text, "reads": [{"data": {"mom_change_bp": 9146}}]}, None)
        != text
    )


@pytest.mark.parametrize("bp", [9146, -9146])
def test_excessively_long_sign_gap_is_rejected_without_unbounded_normalization(bp):
    text = "环比-" + (" " * 40) + "91.46%"
    assert (
        OutputVerifier()({"reply_draft": text, "reads": [{"data": {"mom_change_bp": bp}}]}, None)
        != text
    )
