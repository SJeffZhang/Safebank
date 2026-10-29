"""Parser coverage (task L2-parser-coverage-a): four live utterances that were
marked INTENT_UNCLEAR and never reached the agent.

These must each produce a correct SCOPED scope.  The fixture below mirrors the
demo seed entities the utterances refer to (merchant 乐听音乐 with a billing
mandate, and a standing instruction to payee 赵六); the parser must resolve
them against state exactly, never invent anything.

Safety rules kept: ambiguous/unknown entities -> FORM_REQUIRED (no guessing);
negation, quotes and questions still produce no scope; an account number in the
text is captured exactly from the text.
"""

import pytest

from safebank_contracts.views import ScheduledView, SubscriptionView

from services.gateway.app.utterance.parser import parse
from tests.gateway.w1.fixtures import bank_state


def rich_state():
    """bank_state() + 乐听音乐 subscription + 赵六 standing instruction."""
    s = bank_state()
    subs = list(s.subscriptions)
    subs.append(
        SubscriptionView(
            instruction_id="si_music",
            billing_id="bil_music",
            account_id="acc_zs",
            merchant_id="mer_music",
            merchant_name="乐听音乐",
            period="MONTHLY",
            amount_cents=1500,
            max_amount_cents=1500,
            next_due_at=s.sim_now,
            status="ACTIVE",
            annual_cost_cents=18000,
        )
    )
    instr = list(s.instructions)
    instr.append(
        ScheduledView(
            id="si_zl",
            from_account_id="acc_zs",
            payee_id="pye_zl",
            to_account_id=None,
            target_display="赵六",
            amount_cents=100000,
            period="MONTHLY",
            anchor_day=1,
            weekday=None,
            time_of_day="08:00",
            next_run_at=s.sim_now,
            end_at=None,
            status="ACTIVE",
            last_run=None,
        )
    )
    return s.model_copy(update={"subscriptions": tuple(subs), "instructions": tuple(instr)})


_STATE = rich_state()
_CUS = "cus_zs"


# ── 1. 新增收款人：开户行他行 ────────────────────────────────────────────────


def test_add_payee_with_kaihuhang_tahang():
    """新增收款人孙九，账号6222020000007777，开户行他行 → SCOPED, PAYEE_ADD.

    当前失败：_clause 只认裸「他行/本行」，「开户行他行」无法识别 → INTENT_UNCLEAR。
    账号必须逐字取自文本，不得编造。
    """
    result = parse("新增收款人孙九，账号6222020000007777，开户行他行", _STATE, _CUS)
    assert result.status == "SCOPED"
    assert result.scope.action_kinds == ("PAYEE_ADD",)
    assert result.scope.fields.get("name") == "孙九"
    assert result.scope.fields.get("bank_name") == "他行"
    assert result.scope.fields.get("account_no") == "6222020000007777"


def test_add_payee_with_kaihuhang_benhang():
    """开户行本行 同样识别为 bank_name=本行。"""
    result = parse("新增收款人钱叔，账号6222020000008888，开户行本行", _STATE, _CUS)
    assert result.status == "SCOPED"
    assert result.scope.fields.get("bank_name") == "本行"
    assert result.scope.fields.get("account_no") == "6222020000008888"


# ── 2. 挂失：我的卡丢了 ──────────────────────────────────────────────────────


def test_report_card_lost_my_card_diu_le():
    """我的卡丢了，帮我挂失 → SCOPED, CARD_LOST on the customer's only card."""
    result = parse("我的卡丢了，帮我挂失", _STATE, _CUS)
    assert result.status == "SCOPED"
    assert result.scope.action_kinds == ("CARD_LOST",)
    assert result.scope.fields.get("card_id") == "crd_zs"


# ── 3. 取消订阅：乐听音乐 ────────────────────────────────────────────────────


def test_cancel_subscription_let_ting_music():
    """取消乐听音乐的订阅 → SCOPED, MANDATE_CANCEL on the 乐听音乐 mandate."""
    result = parse("取消乐听音乐的订阅", _STATE, _CUS)
    assert result.status == "SCOPED"
    assert result.scope.action_kinds == ("MANDATE_CANCEL",)
    assert result.scope.fields.get("instruction_ids") == ["si_music"]


# ── 4. 取消定时转账：赵六 ────────────────────────────────────────────────────


def test_cancel_scheduled_gei_zhao_liu():
    """取消给赵六的定时转账 → SCOPED, SCHEDULE_CANCEL on the 赵六 instruction."""
    result = parse("取消给赵六的定时转账", _STATE, _CUS)
    assert result.status == "SCOPED"
    assert result.scope.action_kinds == ("SCHEDULE_CANCEL",)
    assert result.scope.fields.get("instruction_id") == "si_zl"


# ── 攻击回归：否定 / 疑问 / 未知实体不得授权 ─────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        # 否定
        "我的卡没丢，帮我挂失",
        # 疑问
        "取消乐听音乐的订阅吗？",
        "取消给赵六的定时转账吗？",
        # 未知商户 / 未知收款人不得猜测
        "取消不存在的商户的订阅",
        "取消给王二的定时转账",
        # 引号中的指令不得授权
        "他说“取消乐听音乐的订阅”",
    ],
)
def test_coverage_a_attack_no_scope(text):
    result = parse(text, _STATE, _CUS)
    assert result.status != "SCOPED"
