"""Formal PRD assistant queries and real scheduled-view target ownership."""

from datetime import timedelta

import pytest
from safebank_contracts.views import ScheduledView

from services.gateway.app.utterance.parser import parse
from tests.gateway.w1.fixtures import NOW, bank_state

QUERIES = [
    "我有哪些定时转账",
    "AA收款进度怎么样",
    "查一下我10月的消费流水",
    "我在兰州拉面花了多少钱",
    "我上个月各类消费的占比，和前一个月比怎么样",
    "最近有没有异常交易",
    "我的卡今天还能刷多少",
    "帮我找出所有周期性扣费",
    "我有哪些订阅，一年总共要花多少",
    "未来7天有哪些扣费",
    "哪些订阅是闲置没用的",
    "我的风险等级是多少",
    "有哪些理财产品适合我",
    "比较一下稳盈短债和稳健增利",
    "我的持仓和在途订单有哪些",
]


@pytest.mark.parametrize("text", QUERIES)
def test_formal_readonly_has_no_write_scope_or_unnecessary_slot(text):
    parsed = parse(text, bank_state(), "cus_zs")
    assert parsed.status == "NO_WRITE"
    assert not parsed.scope.action_kinds and not parsed.required_fields and not parsed.candidates


@pytest.mark.parametrize(
    "text",
    [
        "我有哪些订阅并取消全部",
        "比较一下稳盈短债和稳健增利并申购2000元",
        "查一下我10月的消费流水后把钱转走",
        "给王五转100元？",
        "我能不能给王五转100？",
    ],
)
def test_unrecognized_mixed_suffix_or_consultation_never_authorizes_write(text):
    assert parse(text, bank_state(), "cus_zs").status != "SCOPED"


def test_explicit_read_then_write_keeps_only_real_write_scope():
    result = parse("我有哪些订阅，给王五转100元", bank_state(), "cus_zs")
    assert result.status == "SCOPED"
    assert result.scope.action_kinds == ("TRANSFER_EXTERNAL",)
    assert result.scope.transfers[0].amount_cents == 10000


def scheduled(**updates):
    data = dict(
        id="si_real",
        from_account_id="acc_zs",
        payee_id="pye_zl",
        to_account_id=None,
        target_display="他行收款人",
        amount_cents=20000,
        period="MONTHLY",
        anchor_day=20,
        weekday=None,
        time_of_day="08:00:00",
        next_run_at=NOW + timedelta(days=5),
        end_at=None,
        status="ACTIVE",
        last_run=None,
    )
    return ScheduledView(**(data | updates))


def test_cancel_resolves_real_payee_name_instead_of_generic_external_display():
    state = bank_state().model_copy(update={"instructions": (scheduled(),)})
    result = parse("取消给赵六的定时转账", state, "cus_zs")
    assert result.status == "SCOPED"
    assert result.scope.action_kinds == ("SCHEDULE_CANCEL",)
    assert result.scope.fields["instruction_id"] == "si_real"


@pytest.mark.parametrize("fault", ["foreign_source", "foreign_payee", "spoofed_display"])
def test_cancel_never_uses_foreign_or_misleading_target_display(fault):
    state = bank_state()
    inst = scheduled()
    if fault == "foreign_source":
        inst = scheduled(from_account_id="acc_foreign")
    elif fault == "foreign_payee":
        payees = dict(state.payees)
        payees["pye_zl"] = payees["pye_zl"].model_copy(update={"customer_id": "cus_other"})
        state = state.model_copy(update={"payees": payees})
    else:
        inst = scheduled(payee_id="pye_ww", target_display="赵六")
    state = state.model_copy(update={"instructions": (inst,)})
    assert parse("取消给赵六的定时转账", state, "cus_zs").status != "SCOPED"


def test_cancel_multiple_owned_schedules_requires_exact_instruction_choice():
    state = bank_state().model_copy(
        update={"instructions": (scheduled(), scheduled(id="si_other"))}
    )
    result = parse("取消给赵六的定时转账", state, "cus_zs")
    assert result.status == "CLARIFYING"
    assert result.required_fields == ["instruction_id"]
    assert {c["entity_id"] for c in result.candidates} == {"si_real", "si_other"}
    assert "instruction_id" not in result.scope.fields


def test_existing_instruction_id_still_cancels_only_its_own_schedule():
    state = bank_state().model_copy(update={"instructions": (scheduled(),)})
    assert parse("取消定时si_real", state, "cus_zs").scope.fields["instruction_id"] == "si_real"


def test_partial_payee_name_only_offers_candidates_without_authorized_transfer():
    result = parse("给王转200块", bank_state(), "cus_zs")
    assert result.status == "CLARIFYING"
    assert result.required_fields == ["payee_id"]
    assert {c["entity_id"] for c in result.candidates} == {"pye_ww", "pye_wu"}
    assert not result.scope.transfers and result.scope.fields["amount_cents"] == 20000


@pytest.mark.parametrize(
    "text,template",
    [
        ("帮我把风险测评改成R5", "risk_assessment_form"),
        ("给钱七6222020000009999转5000", "register_payee_form"),
        ("我能不能给王五转100？", "transfer_consultation"),
    ],
)
def test_unscoped_form_has_specific_safe_guidance_without_grant(text, template):
    result = parse(text, bank_state(), "cus_zs")
    assert result.status == "INTENT_UNCLEAR" and not result.scope.action_kinds
    assert result.guidance_template == template
