"""Regression cases supplied by independent review, not matrix coverage."""

import pytest

from services.gateway.app.utterance.parser import parse
from tests.gateway.w1.fixtures import bank_state


@pytest.mark.parametrize(
    "text",
    [
        "不需要给王五800",
        "等工资到账后给王五800",
        "假设给王五800",
        "给王五800，赵六欠我900",
        "如果发奖金，给王五800",
        "可以考虑给王五800",
        "我觉得给王五800",
        "给王五800之后再说",
        "王五3000，赵六已经收到4000",
    ],
)
def test_r1_each_whole_clause_must_be_a_supported_command(text):
    result = parse(text, bank_state(), "cus_zs")
    assert result.status != "SCOPED"
    assert not result.scope.transfers


@pytest.mark.parametrize(
    "money,cents",
    [
        ("800分", 800),
        ("800角", 8000),
        ("800元", 80000),
        ("800", 80000),
        ("0.01元", 1),
        ("0.1角", 1),
        ("1分", 1),
        ("人民币8.05元", 805),
    ],
)
def test_r2_explicit_money_units_are_exact(money, cents):
    result = parse("给王五" + money, bank_state(), "cus_zs")
    assert result.status == "SCOPED"
    assert result.scope.transfers[0].amount_cents == cents


@pytest.mark.parametrize(
    "money", ["0.1分", "0.01角", "800厘", "800美元", "800港元", "800元人民币以外", "800元吗"]
)
def test_r2_unsupported_or_fractional_cents_never_authorize(money):
    result = parse("给王五" + money, bank_state(), "cus_zs")
    assert result.status != "SCOPED"
    assert not result.scope.transfers


@pytest.mark.parametrize("source", ["储蓄", "储蓄账户", "我的储蓄", "我的储蓄账户"])
def test_r3_explicit_source_variants_never_default_to_checking(source):
    result = parse(f"从{source}给王五800", bank_state(), "cus_zs")
    assert result.status == "SCOPED"
    assert result.scope.from_account_id == "acc_save"
    assert result.scope.transfers[0].from_account_id == "acc_save"


@pytest.mark.parametrize("source", ["我的工资账户", "未知账户", "我的储蓄账户的朋友账户"])
def test_r3_unknown_explicit_source_cannot_be_defaulted(source):
    assert parse(f"从{source}给王五800", bank_state(), "cus_zs").status != "SCOPED"


@pytest.mark.parametrize("target", ["王五同事", "王五的朋友", "王五一号", "王五同事王武"])
def test_r4_full_target_slot_is_not_a_name_substring(target):
    result = parse(f"给{target}800", bank_state(), "cus_zs")
    assert result.status != "SCOPED"
    assert not result.scope.transfers


@pytest.mark.parametrize(
    "text", ["给王吾800，给赵柳900", "给王五800，给王吾900", "给王吾800，给王五900"]
)
def test_r5_multiple_transfer_slots_with_ambiguity_require_complete_form(text):
    result = parse(text, bank_state(), "cus_zs")
    assert result.status == "INTENT_UNCLEAR"
    assert not result.required_fields
    assert not result.candidates
    assert not result.scope.transfers


def test_r6_empty_form_required_is_not_a_slot_answer():
    from services.gateway.app.errors import GatewayError
    from services.gateway.app.utterance.service import clarification_fields

    clarification = {
        "kind": "FORM_REQUIRED",
        "status": "OPEN",
        "required_fields": [],
        "candidate_refs": [],
        "partial_scope": {"action_kinds": []},
    }
    with pytest.raises(GatewayError):
        clarification_fields(clarification, [])


def test_r6_merging_slot_cannot_overwrite_transfer_or_leave_missing_time():
    from services.gateway.app.errors import GatewayError
    from services.gateway.app.utterance.service import merge_scope

    partial = parse("明天给王五800", bank_state(), "cus_zs").scope.model_dump(mode="json")
    partial["fields"]["amount_cents"] = 90000
    clarification = {
        "kind": "SLOTS",
        "status": "OPEN",
        "required_fields": ["payee_id"],
        "candidate_refs": [{"field": "payee_id", "entity_id": "pye_ww", "label": "王五"}],
        "partial_scope": partial,
    }
    with pytest.raises(GatewayError):
        merge_scope(clarification, {"payee_id": "pye_ww"}, bank_state())


def test_r6_product_slot_does_not_authorize_a_missing_amount():
    from services.gateway.app.errors import GatewayError
    from services.gateway.app.utterance.service import merge_scope

    clarification = {
        "kind": "SLOTS",
        "status": "OPEN",
        "required_fields": ["product_id"],
        "candidate_refs": [{"field": "product_id", "entity_id": "prd_a", "label": "稳盈短债"}],
        "partial_scope": {
            "action_kinds": ["FUND_SUBSCRIBE"],
            "from_account_id": "acc_zs",
            "fields": {"account_id": "acc_zs"},
        },
    }
    with pytest.raises(GatewayError):
        merge_scope(clarification, {"product_id": "prd_a"}, bank_state())


@pytest.mark.parametrize("text", ["改成是什么意思", "改成是什么意思？", "改成是什么意思?"])
def test_r7_metalinguistic_query_is_not_a_correction(text):
    from services.gateway.app.utterance.parser import is_correction

    assert not is_correction(text)


def test_r6_schedule_slot_cannot_promote_zero_amount_to_complete_scope():
    from services.gateway.app.errors import GatewayError
    from services.gateway.app.utterance.service import merge_scope

    clarification = {
        "kind": "SLOTS",
        "status": "OPEN",
        "required_fields": ["from_account_id"],
        "candidate_refs": [{"field": "from_account_id", "entity_id": "acc_zs", "label": "活期"}],
        "partial_scope": {
            "action_kinds": ["SCHEDULE_CREATE"],
            "period": "ONCE",
            "transfers": [
                {"target_fingerprint": "fp_ww", "amount_cents": 0, "from_account_id": None}
            ],
            "fields": {"first_run_at": "2026-10-16T09:00:00+08:00"},
        },
    }
    with pytest.raises(GatewayError):
        merge_scope(clarification, {"from_account_id": "acc_zs"}, bank_state())
