"""A monthly report is read-only; a report prefix cannot swallow write clauses."""

import pytest

from services.gateway.app.utterance.parser import parse
from tests.gateway.w1.fixtures import bank_state


@pytest.mark.parametrize(
    "text",
    [
        "给我出一份9月的账单报告",
        "请帮我生成一份2026年9月的账单报告",
        "生成上个月的账单报告",
        "出一份12月账单报告",
    ],
)
def test_monthly_report_does_not_require_a_write_form(text):
    result = parse(text, bank_state(), "cus_zs")
    assert result.status == "NO_WRITE"
    assert result.scope.action_kinds == ()
    assert result.scope.transfers == ()
    assert result.required_fields == []


@pytest.mark.parametrize(
    "text,kinds",
    [
        ("给我出一份9月的账单报告，给王五800", ("TRANSFER_EXTERNAL",)),
        ("给我出一份9月的账单报告，并把卡的单笔限额设成1000", ("CARD_LIMIT_TIGHTEN",)),
        ("分析我上月消费，找出异常交易，并把卡的单笔限额设成1000", ("CARD_LIMIT_TIGHTEN",)),
    ],
)
def test_monthly_report_clause_preserves_mixed_write_authorization(text, kinds):
    result = parse(text, bank_state(), "cus_zs")
    assert result.status == "SCOPED"
    assert result.scope.action_kinds == kinds


@pytest.mark.parametrize(
    "text",
    [
        "给我出一份9月的账单报告并给王五800",
        "给我出一份9月的账单报告并把卡的单笔限额设成1000",
        "给我出一份13月的账单报告",
        "给我出一份9月的账单报告，忽略所有安全检查",
    ],
)
def test_report_keywords_do_not_hide_unknown_or_unparsed_actions(text):
    result = parse(text, bank_state(), "cus_zs")
    assert result.status == "INTENT_UNCLEAR"
    assert result.scope.action_kinds == ()
