"""Parser coverage (task L2-parser-coverage-c): two cross-scenario utterances
that were marked INTENT_UNCLEAR at G3 and never reached the agent.

1. X-2 "找出没用的订阅取消掉，省下来的钱每月1号自动转到我的储蓄账户" must
   produce ONE multi-action SCOPED scope: MANDATE_CANCEL with the predicate
   ``idle_subscriptions`` (the target set is evaluated by L2, not the parser)
   plus a self SCHEDULE_CREATE to the savings account, monthly anchor day 1,
   amount = the derived template ``cancelled_monthly_fees`` (no literal
   amount), source = the CHECKING account.

2. X-4 "把这笔可疑交易对应的卡冻结，再从储蓄转2000到活期应急" must produce
   ONE multi-action SCOPED scope: CARD_FREEZE with the predicate
   ``suspicious_cards`` plus a TRANSFER_SELF acc_zs_sav -> acc_zs_chk of
   200000 cents.

Safety rules kept: each clause must be complete on its own or the whole
utterance stays FORM_REQUIRED; amounts are never invented (X-2 amount is the
derived template only); negation, quotes and questions still produce no scope.
"""

import pytest
from safebank_contracts.state import Account, Card
from safebank_contracts.views import ChannelFlags

from services.gateway.app.utterance.parser import parse
from tests.gateway.w1.fixtures import NOW, bank_state


def state():
    """bank_state() with the demo seed's checking/savings accounts and card."""
    s = bank_state()
    accounts = {
        "acc_zs_chk": Account(
            id="acc_zs_chk",
            customer_id="cus_zs",
            type="CHECKING",
            display_name="活期",
            account_no="6222000000000001",
            credits_posted_cents=600000,
        ),
        "acc_zs_sav": Account(
            id="acc_zs_sav",
            customer_id="cus_zs",
            type="SAVINGS",
            display_name="储蓄",
            account_no="6222000000000002",
            credits_posted_cents=2000000,
        ),
    }
    cards = {
        "crd_zs_1": Card(
            id="crd_zs_1",
            account_id="acc_zs_chk",
            last4="1234",
            status="ACTIVE",
            single_limit_cents=500000,
            daily_limit_cents=2000000,
            controls=ChannelFlags(online=True, overseas=False, atm=True, quickpass=True),
            created_at=NOW,
        )
    }
    return s.model_copy(update={"accounts": accounts, "cards": cards})


_STATE = state()
_CUS = "cus_zs"


# ── 1. X-2：取消闲置订阅 + 省下的钱每月转到储蓄 ─────────────────────────────


def test_x2_cancel_idle_subscriptions_and_schedule_monthly_savings():
    result = parse(
        "找出没用的订阅取消掉，省下来的钱每月1号自动转到我的储蓄账户",
        _STATE,
        _CUS,
        predicates={"idle_subscriptions": ["si_gym"]},
    )
    assert result.status == "SCOPED"
    assert result.scope.action_kinds == ("MANDATE_CANCEL", "SCHEDULE_CREATE")
    assert result.scope.from_account_id == "acc_zs_chk"
    assert [(t.target_fingerprint, t.amount_cents) for t in result.scope.transfers] == [
        ("int:acc_zs_sav", 0)
    ]
    assert result.scope.period == "MONTHLY"
    assert result.scope.fields["derived_template"] == "cancelled_monthly_fees"
    assert result.scope.fields["instruction_ids"] == ["si_gym"]
    assert result.scope.fields["predicates"] == {"idle_subscriptions": ["si_gym"]}
    assert result.scope.fields["first_run_at"] == "2026-11-01T08:00:00+08:00"


# ── 2. X-4：冻结可疑卡 + 从储蓄转2000到活期 ────────────────────────────────


def test_x4_freeze_suspicious_card_and_transfer_savings_to_checking():
    result = parse(
        "把这笔可疑交易对应的卡冻结，再从储蓄转2000到活期应急",
        _STATE,
        _CUS,
        predicates={"suspicious_cards": ["crd_zs_1"]},
    )
    assert result.status == "SCOPED"
    assert result.scope.action_kinds == ("CARD_FREEZE", "TRANSFER_SELF")
    assert result.scope.from_account_id == "acc_zs_sav"
    assert [(t.target_fingerprint, t.amount_cents) for t in result.scope.transfers] == [
        ("int:acc_zs_chk", 200000)
    ]
    assert result.scope.fields["card_id"] == "crd_zs_1"
    assert result.scope.fields["predicates"] == {"suspicious_cards": ["crd_zs_1"]}


# ── 攻击回归：否定 / 疑问 / 引号 / 不完整从句 / 缺谓词目标不得授权 ──────────


@pytest.mark.parametrize(
    ("text", "predicates"),
    [
        # 否定
        (
            "不要找出没用的订阅取消掉，省下来的钱每月1号自动转到我的储蓄账户",
            {"idle_subscriptions": ["si_gym"]},
        ),
        (
            "不要冻结这笔可疑交易对应的卡，再从储蓄转2000到活期应急",
            {"suspicious_cards": ["crd_zs_1"]},
        ),
        # 疑问
        (
            "找出没用的订阅取消掉吗，省下来的钱每月1号自动转到我的储蓄账户",
            {"idle_subscriptions": ["si_gym"]},
        ),
        (
            "把这笔可疑交易对应的卡冻结吗，再从储蓄转2000到活期应急",
            {"suspicious_cards": ["crd_zs_1"]},
        ),
        # 引号中的指令
        (
            "他说“找出没用的订阅取消掉，省下来的钱每月1号自动转到我的储蓄账户”",
            {"idle_subscriptions": ["si_gym"]},
        ),
        # 第二个从句不完整（缺目标）
        (
            "找出没用的订阅取消掉，省下来的钱每月1号自动转到",
            {"idle_subscriptions": ["si_gym"]},
        ),
        # 缺谓词目标（idle_subscriptions 无值）
        ("找出没用的订阅取消掉，省下来的钱每月1号自动转到我的储蓄账户", {}),
        # 缺谓词目标（suspicious_cards 无值 → 选不出卡）
        ("把这笔可疑交易对应的卡冻结，再从储蓄转2000到活期应急", {}),
        # 派生模板没有前置取消订阅动作
        ("省下来的钱每月1号自动转到我的储蓄账户", {"idle_subscriptions": ["si_gym"]}),
    ],
)
def test_cross_scenario_attack_no_scope(text, predicates):
    result = parse(text, _STATE, _CUS, predicates=predicates)
    assert result.status != "SCOPED"
