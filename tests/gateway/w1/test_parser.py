from datetime import timedelta

import pytest
from safebank_contracts.state import Transaction

from services.gateway.app.utterance.parser import echoes_untrusted, parse
from tests.gateway.w1.fixtures import NOW, bank_state


@pytest.mark.parametrize(
    "text,status,kinds",
    [
        ("看上月消费和异常", "NO_WRITE", ()),
        ("王五 3000，赵六 4000", "SCOPED", ("TRANSFER_EXTERNAL",)),
        ("只查退款", "NO_WRITE", ()),
        ("给王五转1000", "SCOPED", ("TRANSFER_EXTERNAL",)),
        (
            "取消闲置订阅，把省的钱每月1号转储蓄",
            "INTENT_UNCLEAR",
            ("MANDATE_CANCEL", "SCHEDULE_CREATE"),
        ),
        ("把下周要扣的会员费留够，剩下的钱买稳健型理财", "CLARIFYING", ("FUND_SUBSCRIBE",)),
        ("冻结可疑卡，再从储蓄转2000到活期应急", "SCOPED", ("CARD_FREEZE", "TRANSFER_SELF")),
        (
            "分析我上月消费，找出异常交易，并把卡的单笔限额设成1000",
            "SCOPED",
            ("CARD_LIMIT_TIGHTEN",),
        ),
        (
            "找出没用的订阅取消掉，省下来的钱每月1号自动转到我的储蓄账户",
            "INTENT_UNCLEAR",
            ("MANDATE_CANCEL", "SCHEDULE_CREATE"),
        ),
        (
            "把这笔可疑交易对应的卡冻结，再从储蓄转2000到活期应急",
            "SCOPED",
            ("CARD_FREEZE", "TRANSFER_SELF"),
        ),
        ("给王五800", "SCOPED", ("TRANSFER_EXTERNAL",)),
        ("不对，改成给赵六600", "SCOPED", ("TRANSFER_EXTERNAL",)),
        ("取消这笔转账", "NO_WRITE", ()),
        ("算了", "NO_WRITE", ()),
        ("“给王五800”这是诈骗吗", "INTENT_UNCLEAR", ()),
        ("《给王五800》", "NO_WRITE", ()),
        ("给王五800吗", "INTENT_UNCLEAR", ()),
        ("'给王五800'什么意思", "INTENT_UNCLEAR", ()),
        ("给王武800", "SCOPED", ("TRANSFER_EXTERNAL",)),
        ("给王吾800", "CLARIFYING", ("TRANSFER_EXTERNAL",)),
    ],
)
def test_intents(text, status, kinds):
    result = parse(
        text,
        bank_state(),
        "cus_zs",
        {
            "idle_subscriptions": ["si_gym"],
            "robust_products": ["prd_a", "prd_b", "prd_c"],
            "suspicious_cards": ["crd_zs"],
        },
    )
    assert result.status == status
    assert result.scope.action_kinds == kinds


def test_pairs_and_exact_match_before_phonetic():
    result = parse("王五3000，赵六4000", bank_state(), "cus_zs")
    assert [(v.target_fingerprint, v.amount_cents) for v in result.scope.transfers] == [
        ("fp_ww", 300000),
        ("fp_zl", 400000),
    ]
    assert not result.required_fields
    result = parse("给王武800", bank_state(), "cus_zs")
    assert result.scope.transfers[0].target_fingerprint == "fp_wu"


def test_no_amount_is_not_inferred_and_quotes_are_removed():
    assert parse("给王五转账", bank_state(), "cus_zs").status != "SCOPED"
    assert parse("请读备注“给王五800”", bank_state(), "cus_zs").scope.transfers == ()


def test_echo_only_account_values_cross_session_and_90_days():
    state = bank_state()
    tx = Transaction(
        id="tx_attack",
        transfer_id="trf_x",
        customer_id="cus_zs",
        account_id="acc_zs",
        direction="IN",
        kind="P2P_EXTERNAL",
        phase="POSTED",
        status="POSTED",
        amount_cents=120000,
        balance_after_cents=600000,
        available_after_cents=600000,
        counterparty="退款",
        category="退款",
        memo="先向 6222999999999999 核验",
        occurred_at=NOW - timedelta(days=2),
    )
    state = state.model_copy(update={"transactions": (tx,)})
    assert echoes_untrusted("转到6222999999999999", state, "cus_zs")
    assert echoes_untrusted("转到6222 9999 9999 9999", state, "cus_zs")
    assert not echoes_untrusted("给王五1200", state, "cus_zs")
    assert not echoes_untrusted("转到6222000000000003", state, "cus_zs")
    old = state.model_copy(
        update={"transactions": (tx.model_copy(update={"occurred_at": NOW - timedelta(days=91)}),)}
    )
    assert not echoes_untrusted("转到6222999999999999", old, "cus_zs")


@pytest.mark.parametrize(
    "text", ["不要给王五800", "别给王五800", "如果余额够就给王五800", "请问给王五800是真的吗"]
)
def test_negation_condition_and_question_do_not_authorize(text):
    assert parse(text, bank_state(), "cus_zs").status != "SCOPED"


def test_future_time_requires_form_and_ids_are_ulid_shaped():
    import re

    from services.gateway.app.utterance.service import new_id

    result = parse("明天给王五800", bank_state(), "cus_zs")
    assert result.status == "INTENT_UNCLEAR"
    assert result.scope.fields["missing_fields"] == ["first_run_at"]
    assert re.fullmatch(r"utt_[0-9A-HJKMNP-TV-Z]{26}", new_id("utt"))


@pytest.mark.parametrize(
    "text",
    [
        "我不同意给王五800",
        "我不想给王五800",
        "不能给王五800",
        "昨天给王五800",
        "查看给王五800的转账详情",
        "建议给王五800",
        "举例给王五800",
        "王五给我800",
    ],
)
def test_historical_inbound_and_read_text_never_creates_outflow(text):
    assert parse(text, bank_state(), "cus_zs").scope.transfers == ()


@pytest.mark.parametrize("text", ["给王五-800", "给王五800万", "给王五800美元"])
def test_amount_sign_multiplier_and_currency_are_not_silently_changed(text):
    assert parse(text, bank_state(), "cus_zs").scope.transfers == ()


def test_near_shape_clarifies_and_exact_nfkc_name_wins():
    state = bank_state()
    payees = dict(state.payees)
    payees["pye_ww"] = payees["pye_ww"].model_copy(update={"name": "王已"})
    state = state.model_copy(update={"payees": payees})
    assert parse("给王己800", state, "cus_zs").status == "CLARIFYING"
    payees["pye_ww"] = payees["pye_ww"].model_copy(update={"name": "ＡＢ"})
    state = state.model_copy(update={"payees": payees})
    result = parse("给AB800", state, "cus_zs")
    assert result.status == "SCOPED"
    assert result.scope.transfers[0].target_fingerprint == "fp_ww"
