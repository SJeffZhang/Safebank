from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest
import safebank_bankcore as bankcore
from pydantic import ValidationError
from safebank_bankcore.banking import Context, apply_card, apply_transfer
from safebank_contracts.obligations import (
    InstructionTarget,
    MerchantMandate,
    ScheduledInstruction,
)
from safebank_contracts.state import Card, Payee
from safebank_contracts.tools import CardId, Transfer
from safebank_contracts.views import ChannelFlags, FundOrderView, ScheduledView

from tests.bankcore.fixtures import NOW, base_state
from tests.bankcore.obligations_fixtures import (
    external_payee,
    raw_billing,
    raw_instruction,
    raw_mandate,
    raw_merchant,
    raw_projection_state,
)


def at(value: str) -> datetime:
    return datetime.fromisoformat(value)


def instruction(**updates) -> ScheduledView:
    values = {
        "id": "si_1",
        "from_account_id": "acc_a",
        "payee_id": "pye_external",
        "to_account_id": None,
        "target_display": "外部收款人",
        "amount_cents": 1000,
        "period": "ONCE",
        "anchor_day": None,
        "weekday": None,
        "time_of_day": "06:00",
        "next_run_at": at("2026-10-18T06:00:00+08:00"),
        "end_at": None,
        "status": "ACTIVE",
        "last_run": None,
    }
    return ScheduledView(**(values | updates))


def test_once_window_is_left_open_right_closed_and_has_integer_rank():
    end = NOW + timedelta(days=7)
    items = (
        instruction(id="si_start", next_run_at=NOW, time_of_day="09:00"),
        instruction(id="si_end", next_run_at=end, time_of_day="09:00"),
        instruction(
            id="si_outside", next_run_at=end + timedelta(seconds=1), time_of_day="09:00:01"
        ),
    )
    result = bankcore.scheduled_occurrences(items, NOW, end)
    assert len(result) == 1
    due = result[0]
    assert (due.due_at, due.rank, due.id, due.account_id, due.delta_cents) == (
        end,
        3,
        "si_end",
        "acc_a",
        -1000,
    )
    assert type(due.rank) is int


@pytest.mark.parametrize("status", ["DISABLED", "DELETED", "COMPLETED", "FAILED"])
def test_inactive_instructions_are_not_obligations(status):
    result = bankcore.scheduled_occurrences(
        (instruction(status=status),), NOW, NOW + timedelta(days=7)
    )
    assert result == ()


def test_monthly_retains_original_31_anchor_through_short_month_and_leap_day():
    leap_start = at("2024-01-30T09:00:00+08:00")
    schedule = instruction(
        period="MONTHLY", anchor_day=31, next_run_at=at("2024-01-31T06:00:00+08:00")
    )
    result = bankcore.scheduled_occurrences(
        (schedule,), leap_start, at("2024-04-01T00:00:00+08:00")
    )
    assert [item.due_at.isoformat() for item in result] == [
        "2024-01-31T06:00:00+08:00",
        "2024-02-29T06:00:00+08:00",
        "2024-03-31T06:00:00+08:00",
    ]


def test_monthly_restarts_at_original_anchor_after_nonleap_february():
    schedule = instruction(
        period="MONTHLY", anchor_day=31, next_run_at=at("2025-02-28T06:00:00+08:00")
    )
    result = bankcore.scheduled_occurrences(
        (schedule,), at("2025-02-27T00:00:00+08:00"), at("2025-04-30T06:00:00+08:00")
    )
    assert [item.due_at.isoformat() for item in result] == [
        "2025-02-28T06:00:00+08:00",
        "2025-03-31T06:00:00+08:00",
        "2025-04-30T06:00:00+08:00",
    ]


def test_end_at_includes_exact_due_instant_and_excludes_later_occurrences():
    schedule = instruction(period="MONTHLY", anchor_day=18, end_at=at("2026-11-18T06:00:00+08:00"))
    result = bankcore.scheduled_occurrences((schedule,), NOW, at("2026-12-31T23:59:59+08:00"))
    assert [item.due_at.isoformat() for item in result] == [
        "2026-10-18T06:00:00+08:00",
        "2026-11-18T06:00:00+08:00",
    ]


def test_scheduled_output_is_stable_for_same_timestamp_and_input_permutations():
    items = (instruction(id="si_z"), instruction(id="si_a"))
    forward = bankcore.scheduled_occurrences(items, NOW, NOW + timedelta(days=30))
    reverse = bankcore.scheduled_occurrences(items[::-1], NOW, NOW + timedelta(days=30))
    assert [item.id for item in forward] == ["si_a", "si_z"]
    assert forward == reverse


def test_weekly_keeps_monday_zero_weekday_and_expands_thirty_days():
    schedule = instruction(period="WEEKLY", weekday=3, time_of_day="09:00", next_run_at=NOW)
    result = bankcore.scheduled_occurrences((schedule,), NOW, NOW + timedelta(days=30))
    assert [item.due_at.isoformat() for item in result] == [
        "2026-10-22T09:00:00+08:00",
        "2026-10-29T09:00:00+08:00",
        "2026-11-05T09:00:00+08:00",
        "2026-11-12T09:00:00+08:00",
    ]


@pytest.mark.parametrize("weekday", [0, 6])
def test_weekday_bounds_have_their_datetime_meaning(weekday):
    due = at("2026-10-19T06:00:00+08:00") if weekday == 0 else at("2026-10-18T06:00:00+08:00")
    result = bankcore.scheduled_occurrences(
        (instruction(period="WEEKLY", weekday=weekday, next_run_at=due),),
        NOW,
        NOW + timedelta(days=7),
    )
    assert [item.due_at for item in result] == [due]


@pytest.mark.parametrize("weekday", [None, -1, 7, 3])
def test_weekly_invalid_or_inconsistent_weekday_is_rejected(weekday):
    with pytest.raises(ValueError):
        bankcore.scheduled_occurrences(
            (instruction(period="WEEKLY", weekday=weekday),), NOW, NOW + timedelta(days=7)
        )


def test_weekly_respects_end_at_before_a_later_execution_time():
    schedule = instruction(period="WEEKLY", weekday=6, end_at=at("2026-10-25T05:59:59+08:00"))
    result = bankcore.scheduled_occurrences((schedule,), NOW, NOW + timedelta(days=30))
    assert [item.due_at.isoformat() for item in result] == ["2026-10-18T06:00:00+08:00"]


@pytest.mark.parametrize(
    "updates",
    [
        {"next_run_at": None},
        {"amount_cents": 0},
        {"period": "MONTHLY", "anchor_day": None},
        {"period": "MONTHLY", "anchor_day": 32},
        {"period": "MONTHLY", "anchor_day": 17},
        {"time_of_day": "25:00"},
        {"time_of_day": "06:01"},
        {"time_of_day": "06:00+08:00"},
        {"anchor_day": 18},
        {"payee_id": None},
        {"to_account_id": "acc_s"},
        {"payee_id": None, "to_account_id": "acc_a"},
    ],
)
def test_invalid_active_schedule_is_rejected_instead_of_omitted(updates):
    with pytest.raises(ValueError):
        bankcore.scheduled_occurrences((instruction(**updates),), NOW, NOW + timedelta(days=7))


def test_duplicate_instruction_ids_are_rejected():
    with pytest.raises(ValueError):
        bankcore.scheduled_occurrences((instruction(), instruction()), NOW, NOW + timedelta(days=7))


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (NOW.replace(tzinfo=None), NOW),
        (NOW, NOW.replace(tzinfo=None)),
        (NOW, NOW - timedelta(days=1)),
    ],
)
def test_invalid_window_is_rejected(start, end):
    with pytest.raises(ValueError):
        bankcore.scheduled_occurrences((), start, end)


# ---- raw obligation-snapshot report tests ----


def prd_raw_state():
    """PRD §2.2 张三 obligations projected onto the synthetic base accounts."""
    return raw_projection_state(
        instructions=(
            raw_instruction(
                id="si_mom",
                amount_cents=100000,
                period="MONTHLY",
                anchor_day=5,
                next_run_at=at("2026-11-05T06:00:00+08:00"),
            ),
        ),
        mandates=(
            raw_mandate(),
            raw_mandate(
                id="si_music",
                billing_id="bill_music",
                merchant_id="mer_music",
                max_amount_cents=1800,
            ),
            raw_mandate(
                id="si_cloud",
                billing_id="bill_cloud",
                merchant_id="mer_cloud",
                max_amount_cents=2520,
            ),
            raw_mandate(
                id="si_gym", billing_id="bill_gym", merchant_id="mer_gym", max_amount_cents=23880
            ),
        ),
        billings=(
            raw_billing(),
            raw_billing(
                id="bill_music",
                merchant_id="mer_music",
                amount_cents=1500,
                anchor_day=20,
                next_due_at=at("2026-10-20T06:00:00+08:00"),
            ),
            raw_billing(
                id="bill_cloud",
                merchant_id="mer_cloud",
                amount_cents=2100,
                anchor_day=1,
                next_due_at=at("2026-11-01T06:00:00+08:00"),
            ),
            raw_billing(
                id="bill_gym",
                merchant_id="mer_gym",
                amount_cents=19900,
                anchor_day=25,
                next_due_at=at("2026-10-25T06:00:00+08:00"),
            ),
        ),
        merchants=(
            raw_merchant(),
            raw_merchant(id="mer_music", name="乐听音乐"),
            raw_merchant(id="mer_cloud", name="云盘存储"),
            raw_merchant(id="mer_gym", name="力健健身"),
        ),
    )


def test_report_uses_actual_bills_and_preserves_merchant_execution_times():
    state = raw_projection_state(
        mandates=(
            raw_mandate(),
            raw_mandate(
                id="si_music",
                billing_id="bill_music",
                merchant_id="mer_music",
                max_amount_cents=1800,
            ),
        ),
        billings=(
            raw_billing(),
            raw_billing(
                id="bill_music",
                merchant_id="mer_music",
                amount_cents=1500,
                anchor_day=20,
                time_of_day=time(21, 15, 30),
                next_due_at=at("2026-10-20T21:15:30+08:00"),
            ),
        ),
        merchants=(raw_merchant(), raw_merchant(id="mer_music", name="乐听音乐")),
    )
    report = bankcore.projected_shortfall(state)
    account = report.accounts["acc_a"]
    assert (account.reserve_cents, account.shortfall_cents, account.max_outflow_cents) == (
        4500,
        0,
        595500,
    )
    assert report.total_reserve_cents == 4500
    assert report.total_shortfall_cents == 0
    assert [
        (item.due_at.isoformat(), item.rank, item.id, item.delta_cents)
        for item in report.obligations
    ] == [
        ("2026-10-18T06:00:00+08:00", 4, "bill_video", -3000),
        ("2026-10-20T21:15:30+08:00", 4, "bill_music", -1500),
    ]
    assert "acc_seed_equity" not in report.accounts
    assert "acc_external_clearing" not in report.accounts


@pytest.mark.parametrize("status", ["DISABLED", "DELETED"])
def test_report_excludes_inactive_mandates(status):
    state = raw_projection_state(
        mandates=(raw_mandate(status=status),),
        billings=(raw_billing(),),
        merchants=(raw_merchant(),),
    )
    report = bankcore.projected_shortfall(state)
    assert report.obligations == ()
    assert report.total_reserve_cents == 0
    assert report.accounts["acc_a"].max_outflow_cents == 600000


@pytest.mark.parametrize("status", ["BLOCKED", "COMPLETED", "FAILED"])
def test_raw_instruction_status_rejects_run_only_statuses(status):
    with pytest.raises(ValidationError):
        ScheduledInstruction(**{**raw_instruction().model_dump(), "status": status})
    with pytest.raises(ValidationError):
        MerchantMandate(**{**raw_mandate().model_dump(), "status": status})


def test_window_excludes_now_and_includes_end():
    end = NOW + timedelta(days=7)
    state = raw_projection_state(
        mandates=(raw_mandate(), raw_mandate(id="si_end", billing_id="bill_end")),
        billings=(
            raw_billing(anchor_day=15, time_of_day=time(9, 0), next_due_at=NOW),
            raw_billing(id="bill_end", anchor_day=22, time_of_day=time(9, 0), next_due_at=end),
        ),
        merchants=(raw_merchant(),),
    )
    report = bankcore.projected_shortfall(state)
    assert [(item.id, item.due_at) for item in report.obligations] == [("bill_end", end)]


@pytest.mark.parametrize("next_due_at", [NOW, NOW - timedelta(days=7)])
def test_weekly_billing_expands_next_period_without_double_counting_now(next_due_at):
    billing = raw_billing(
        period="WEEKLY",
        anchor_day=None,
        weekday=next_due_at.weekday(),
        next_due_at=next_due_at,
        time_of_day=time(9, 0),
    )
    state = raw_projection_state(
        mandates=(raw_mandate(),), billings=(billing,), merchants=(raw_merchant(),)
    )
    report = bankcore.projected_shortfall(state)
    assert [(item.due_at, item.delta_cents) for item in report.obligations] == [
        (NOW + timedelta(days=7), -3000)
    ]


def test_stale_monthly_billing_recovers_from_original_anchor():
    billing = raw_billing(anchor_day=18, next_due_at=at("2026-09-18T06:00:00+08:00"))
    state = raw_projection_state(
        mandates=(raw_mandate(),), billings=(billing,), merchants=(raw_merchant(),)
    )
    report = bankcore.projected_shortfall(state)
    assert [(item.due_at.isoformat(), item.delta_cents) for item in report.obligations] == [
        ("2026-10-18T06:00:00+08:00", -3000)
    ]


@pytest.mark.parametrize("days", [0, -1, 1, 8, 30, 7.0, True, "7", None])
def test_projection_rejects_unsupported_days_instead_of_guessing_monthly_recurrence(days):
    with pytest.raises(ValueError, match="7"):
        bankcore.projected_shortfall(raw_projection_state(), days=days)


def test_scheduled_debits_precede_merchant_charges_at_equal_timestamp():
    state = raw_projection_state(
        instructions=(raw_instruction(id="si_z"),),
        mandates=(raw_mandate(),),
        billings=(raw_billing(),),
        merchants=(raw_merchant(),),
    )
    report = bankcore.projected_shortfall(state)
    assert [(item.rank, item.id) for item in report.obligations] == [(3, "si_z"), (4, "bill_video")]
    assert report.total_reserve_cents == 4000


def test_existing_shortfall_preserves_negative_max_outflow_and_uses_available_balance():
    state = raw_projection_state(
        balance=2000,
        mandates=(raw_mandate(),),
        billings=(raw_billing(),),
        merchants=(raw_merchant(),),
    )
    accounts = dict(state.accounts)
    accounts["acc_a"] = accounts["acc_a"].model_copy(update={"debits_pending_cents": 500})
    state = state.model_copy(update={"accounts": accounts})
    report = bankcore.projected_shortfall(state)
    account = report.accounts["acc_a"]
    assert (account.reserve_cents, account.shortfall_cents, account.max_outflow_cents) == (
        3000,
        1500,
        -1500,
    )
    assert report.total_shortfall_cents == 1500


def test_future_fund_settlement_and_pending_income_do_not_guarantee_outflow():
    state = raw_projection_state(
        balance=1000,
        mandates=(raw_mandate(),),
        billings=(raw_billing(),),
        merchants=(raw_merchant(),),
    )
    accounts = dict(state.accounts)
    accounts["acc_a"] = accounts["acc_a"].model_copy(update={"credits_pending_cents": 900000})
    order = FundOrderView(
        id="ord_1",
        account_id="acc_a",
        product_id="prd_1",
        type="RED",
        status="PENDING_SETTLEMENT",
        amount_cents=900000,
        units=Decimal("9000.0000"),
        nav=Decimal("1.0000"),
        trade_date=date(2026, 10, 15),
        confirm_at=NOW,
        settle_at=NOW + timedelta(days=1),
        failure_code=None,
    )
    report = bankcore.projected_shortfall(
        state.model_copy(update={"accounts": accounts, "orders": (order,)})
    )
    assert report.accounts["acc_a"].max_outflow_cents == -2000
    assert report.total_shortfall_cents == 2000


def test_report_does_not_mutate_inputs_and_its_mappings_and_records_are_immutable():
    state = raw_projection_state(
        instructions=(raw_instruction(),),
        mandates=(raw_mandate(),),
        billings=(raw_billing(),),
        merchants=(raw_merchant(),),
    )
    before = state.model_dump_json()
    report = bankcore.projected_shortfall(state)
    assert state.model_dump_json() == before
    with pytest.raises(TypeError):
        report.accounts["acc_a"] = report.accounts["acc_s"]
    with pytest.raises(FrozenInstanceError):
        report.accounts["acc_a"].reserve_cents = 0
    with pytest.raises(FrozenInstanceError):
        report.obligations[0].delta_cents = 0
    with pytest.raises(TypeError):
        state.obligations.merchants["mutated"] = raw_merchant(id="mer_mutated")


def test_null_obligations_are_rejected_instead_of_treated_as_empty():
    state = base_state()
    assert state.obligations is None
    with pytest.raises(ValueError, match="not loaded"):
        bankcore.projected_shortfall(state)


def test_incomplete_coverage_is_rejected():
    state = raw_projection_state(covered=("cus_a",))
    with pytest.raises(ValueError, match="coverage"):
        bankcore.projected_shortfall(state)


def test_unknown_source_account_fails_instead_of_losing_obligations():
    item = raw_instruction(from_account_id="acc_missing")
    state = raw_projection_state(instructions=(item,))
    with pytest.raises(ValueError):
        bankcore.projected_shortfall(state)


def test_unknown_own_target_account_fails():
    item = raw_instruction(payee_id=None, to_account_id="acc_missing")
    target = InstructionTarget(
        instruction_id="si_1",
        source_customer_id="cus_a",
        ownership="OWN",
        target_account_id="acc_missing",
        target_customer_id="cus_a",
    )
    state = raw_projection_state(instructions=(item,), targets=(target,))
    with pytest.raises(ValueError):
        bankcore.projected_shortfall(state)


def test_mandate_with_missing_billing_fails():
    mandate = raw_mandate(billing_id="bill_missing")
    state = raw_projection_state(
        mandates=(mandate,), billings=(raw_billing(),), merchants=(raw_merchant(),)
    )
    with pytest.raises(ValueError, match="billing"):
        bankcore.projected_shortfall(state)


def test_mandate_billing_account_must_match_source_account():
    state = raw_projection_state(
        mandates=(raw_mandate(from_account_id="acc_s"),),
        billings=(raw_billing(),),  # account_id acc_a != acc_s
        merchants=(raw_merchant(),),
    )
    with pytest.raises(ValueError, match="account"):
        bankcore.projected_shortfall(state)


def test_unknown_subscription_account_is_rejected():
    billing = raw_billing(account_id="acc_missing")
    state = raw_projection_state(
        mandates=(raw_mandate(),), billings=(billing,), merchants=(raw_merchant(),)
    )
    with pytest.raises(ValueError):
        bankcore.projected_shortfall(state)


def test_duplicate_merchant_obligations_are_rejected():
    duplicate = raw_mandate(id="si_other")
    state = raw_projection_state(
        mandates=(raw_mandate(), duplicate),
        billings=(raw_billing(),),
        merchants=(raw_merchant(),),
    )
    with pytest.raises(ValueError, match="duplicate merchant billing"):
        bankcore.projected_shortfall(state)


def with_balances(state, **balances):
    accounts = dict(state.accounts)
    for account_id, balance in balances.items():
        accounts[account_id] = accounts[account_id].model_copy(
            update={"credits_posted_cents": balance, "debits_posted_cents": 0}
        )
    return state.model_copy(update={"accounts": accounts})


def test_own_scheduled_credit_can_fund_same_instant_merchant_charge():
    state = raw_projection_state(
        instructions=(
            raw_instruction(from_account_id="acc_s", payee_id=None, to_account_id="acc_a"),
        ),
        mandates=(raw_mandate(),),
        billings=(raw_billing(amount_cents=1000),),
        merchants=(raw_merchant(),),
    )
    report = bankcore.projected_shortfall(with_balances(state, acc_a=0, acc_s=1000))
    assert report.accounts["acc_a"].reserve_cents == 0
    assert report.accounts["acc_s"].reserve_cents == 1000
    assert report.total_shortfall_cents == 0
    assert [(item.rank, item.account_id, item.delta_cents) for item in report.obligations] == [
        (3, "acc_s", -1000),
        (3, "acc_a", 1000),
        (4, "acc_a", -1000),
    ]


def test_later_own_credit_cannot_hide_an_earlier_target_balance_peak():
    state = raw_projection_state(
        instructions=(
            raw_instruction(from_account_id="acc_s", payee_id=None, to_account_id="acc_a"),
        ),
        mandates=(raw_mandate(),),
        billings=(
            raw_billing(
                amount_cents=1000, next_due_at=at("2026-10-17T06:00:00+08:00"), anchor_day=17
            ),
        ),
        merchants=(raw_merchant(),),
    )
    report = bankcore.projected_shortfall(with_balances(state, acc_a=0, acc_s=500))
    assert report.accounts["acc_a"].reserve_cents == 1000
    assert report.accounts["acc_a"].shortfall_cents == 1000
    assert report.accounts["acc_s"].max_outflow_cents == -500
    assert report.total_shortfall_cents == 1500


def test_direct_target_requires_same_owner():
    target = InstructionTarget(
        instruction_id="si_1",
        source_customer_id="cus_a",
        ownership="OWN",
        target_account_id="acc_b",
        target_customer_id="cus_a",
    )
    item = raw_instruction(payee_id=None, to_account_id="acc_b")
    state = raw_projection_state(instructions=(item,), targets=(target,))
    with pytest.raises(ValueError):
        bankcore.projected_shortfall(state)


def test_internal_account_cannot_act_as_customer_obligation_source():
    item = raw_instruction(from_account_id="acc_seed_equity")
    state = raw_projection_state(instructions=(item,))
    with pytest.raises(ValueError):
        bankcore.projected_shortfall(state)


def test_other_targets_never_guarantee_target_income():
    payee = external_payee().model_copy(update={"target_account_id": "acc_b"})
    state = raw_projection_state(instructions=(raw_instruction(),), payees=(payee,))
    report = bankcore.projected_shortfall(with_balances(state, acc_b=0))
    assert report.accounts["acc_b"].reserve_cents == 0
    assert report.accounts["acc_a"].reserve_cents == 1000
    assert [(item.account_id, item.delta_cents) for item in report.obligations] == [
        ("acc_a", -1000)
    ]


def test_prd_complete_obligations_and_real_transfer_share_exact_295500_limit():
    state = prd_raw_state()
    payee = Payee(
        id="pye_ww",
        customer_id="cus_a",
        name="王五",
        alias=None,
        phone=None,
        bank_name="本行",
        account_no="6222020000000003",
        target_account_id="acc_b",
        target_fingerprint="internal",
        created_at=NOW - timedelta(days=10),
    )
    state = state.model_copy(update={"payees": {**state.payees, payee.id: payee}})
    after, _, errors = apply_transfer(
        state,
        Transfer(
            from_account_id="acc_a",
            payee_id="pye_ww",
            to_account_id=None,
            amount_cents=300000,
            memo=None,
        ),
        NOW,
        Context("cus_a", "pln_prd", "s1"),
    )
    assert errors == ()
    report = bankcore.projected_shortfall(after)
    assert report.accounts["acc_a"].max_outflow_cents == 295500
    assert report.total_reserve_cents == 4500
    assert [item.id for item in report.obligations] == ["bill_video", "bill_music"]

    for amount, expected_shortfall in [(295500, 0), (295501, 1)]:
        candidate, _, errors = apply_transfer(
            after,
            Transfer(
                from_account_id="acc_a",
                payee_id="pye_external",
                to_account_id=None,
                amount_cents=amount,
                memo=None,
            ),
            NOW,
            Context("cus_a", "pln_prd", "s2"),
        )
        assert errors == ()
        assert bankcore.projected_shortfall(candidate).total_shortfall_cents == expected_shortfall


def test_existing_shortfall_does_not_increase_when_actual_freeze_succeeds():
    state = with_balances(prd_raw_state(), acc_a=1000)
    card = Card(
        id="crd_a",
        account_id="acc_a",
        last4="1234",
        status="ACTIVE",
        single_limit_cents=500000,
        daily_limit_cents=2000000,
        controls=ChannelFlags(online=True, overseas=False, atm=True, quickpass=False),
        created_at=NOW,
    )
    state = state.model_copy(update={"cards": {card.id: card}})
    before = bankcore.projected_shortfall(state)
    frozen, _, errors = apply_card(
        state, CardId(card_id="crd_a"), NOW, Context("cus_a", "pln_freeze", "s1"), "freeze_card"
    )
    assert errors == () and frozen.cards["crd_a"].status == "FROZEN"
    after = bankcore.projected_shortfall(frozen)
    assert after.total_shortfall_cents == before.total_shortfall_cents == 3500


def test_cancelling_an_obligation_reduces_existing_shortfall():
    state = with_balances(prd_raw_state(), acc_a=1000)
    before = bankcore.projected_shortfall(state)
    snapshot = state.obligations.model_copy(
        update={
            "standing_instructions": {
                k: v.model_copy(update={"status": "DELETED"})
                if isinstance(v, MerchantMandate) and v.id == "si_video"
                else v
                for k, v in state.obligations.standing_instructions.items()
            }
        }
    )
    after = bankcore.projected_shortfall(state.model_copy(update={"obligations": snapshot}))
    assert before.total_shortfall_cents == 3500
    assert after.total_shortfall_cents == 500


def test_report_is_identical_under_account_instruction_and_subscription_permutations():
    state = prd_raw_state()
    own = raw_instruction(
        id="si_own", from_account_id="acc_s", payee_id=None, to_account_id="acc_a"
    )
    snapshot = state.obligations.model_copy(
        update={
            "standing_instructions": {**state.obligations.standing_instructions, own.id: own},
            "instruction_targets": {
                **state.obligations.instruction_targets,
                own.id: InstructionTarget(
                    instruction_id=own.id,
                    source_customer_id="cus_a",
                    ownership="OWN",
                    target_account_id="acc_a",
                    target_customer_id="cus_a",
                ),
            },
        }
    )
    state = state.model_copy(update={"obligations": snapshot})
    reversed_state = state.model_copy(
        update={
            "accounts": dict(reversed(tuple(state.accounts.items()))),
            "obligations": snapshot.model_copy(
                update={
                    "standing_instructions": dict(
                        reversed(tuple(snapshot.standing_instructions.items()))
                    )
                }
            ),
        }
    )
    assert bankcore.projected_shortfall(state) == bankcore.projected_shortfall(reversed_state)


@pytest.mark.parametrize("offset_hours", [0, -7, 14])
def test_report_offsets_preserve_same_seven_day_obligations(offset_hours):
    offset = timezone(timedelta(hours=offset_hours))
    item = raw_instruction(
        period="MONTHLY",
        anchor_day=18,
        next_run_at=at("2026-10-18T06:00:00+08:00").astimezone(offset),
    )
    state = raw_projection_state(instructions=(item,)).model_copy(
        update={"sim_now": NOW.astimezone(offset)}
    )
    report = bankcore.projected_shortfall(state)
    assert [due.due_at.isoformat() for due in report.obligations] == ["2026-10-18T06:00:00+08:00"]
    assert report.accounts["acc_a"].reserve_cents == 1000


@pytest.mark.parametrize("end_shift_microseconds", [0, -1])
def test_raw_mixed_offset_window_and_end_at_compare_instants(end_shift_microseconds):
    end = NOW + timedelta(days=7)
    item = raw_instruction(
        period="WEEKLY",
        weekday=3,
        time_of_day=time(9, 0),
        next_run_at=NOW.astimezone(UTC),
        end_at=(end + timedelta(microseconds=end_shift_microseconds)).astimezone(
            timezone(timedelta(hours=14))
        ),
    )
    state = raw_projection_state(instructions=(item,)).model_copy(
        update={"sim_now": NOW.astimezone(timezone(timedelta(hours=-7)))}
    )
    report = bankcore.projected_shortfall(state)
    assert [due.due_at.isoformat() for due in report.obligations] == (
        ["2026-10-22T09:00:00+08:00"] if end_shift_microseconds == 0 else []
    )


def test_raw_mixed_offset_report_keeps_merchant_and_scheduled_window_boundaries():
    end = NOW + timedelta(days=7)
    state = raw_projection_state(
        instructions=(
            raw_instruction(
                period="WEEKLY",
                weekday=3,
                time_of_day=time(9, 0),
                next_run_at=NOW.astimezone(UTC),
                end_at=end.astimezone(timezone(timedelta(hours=14))),
            ),
        ),
        mandates=(
            raw_mandate(),
            raw_mandate(id="si_end", billing_id="bill_end"),
            raw_mandate(id="si_late", billing_id="bill_late"),
        ),
        billings=(
            raw_billing(
                period="MONTHLY",
                anchor_day=15,
                weekday=None,
                time_of_day=time(9, 0),
                next_due_at=NOW.astimezone(UTC),
            ),
            raw_billing(
                id="bill_end",
                period="WEEKLY",
                anchor_day=None,
                weekday=3,
                time_of_day=time(9, 0),
                next_due_at=end.astimezone(timezone(timedelta(hours=-7))),
            ),
            raw_billing(
                id="bill_late",
                period="WEEKLY",
                anchor_day=None,
                weekday=3,
                time_of_day=time(9, 0, 1),
                next_due_at=(end + timedelta(seconds=1)).astimezone(UTC),
            ),
        ),
        merchants=(raw_merchant(),),
    ).model_copy(update={"sim_now": NOW.astimezone(timezone(timedelta(hours=-7)))})
    report = bankcore.projected_shortfall(state)
    assert [(due.due_at.isoformat(), due.rank, due.id) for due in report.obligations] == [
        ("2026-10-22T09:00:00+08:00", 3, "si_1"),
        ("2026-10-22T09:00:00+08:00", 4, "bill_end"),
    ]
    assert report.accounts["acc_a"].reserve_cents == 4000


@pytest.mark.parametrize(
    "updates",
    [
        {"time_of_day": time(22, 0)},
        {"period": "WEEKLY", "weekday": 5},
        {"period": "MONTHLY", "anchor_day": 17},
    ],
)
def test_raw_wrong_shanghai_time_or_anchor_is_rejected(updates):
    item = raw_instruction(**updates)
    state = raw_projection_state(instructions=(item,))
    with pytest.raises(ValueError):
        bankcore.projected_shortfall(state)


@pytest.mark.parametrize(
    "updates",
    [
        {"time_of_day": time(22, 0)},
        {"period": "WEEKLY", "anchor_day": None, "weekday": 5},
        {"period": "MONTHLY", "anchor_day": 17},
    ],
)
def test_raw_billing_wrong_shanghai_time_or_anchor_is_rejected(updates):
    billing = raw_billing(**updates)
    state = raw_projection_state(
        mandates=(raw_mandate(),), billings=(billing,), merchants=(raw_merchant(),)
    )
    with pytest.raises(ValueError):
        bankcore.projected_shortfall(state)


def test_inactive_billing_is_not_reserved_but_billing_stays():
    state = raw_projection_state(
        mandates=(raw_mandate(),),
        billings=(raw_billing(active=False),),
        merchants=(raw_merchant(),),
    )
    report = bankcore.projected_shortfall(state)
    assert report.obligations == ()
    assert report.total_reserve_cents == 0
    assert "bill_video" in state.obligations.billings


# ---- upcoming_window validation ----


@pytest.mark.parametrize("days", [0, -1, 31, 365, 7.0, True, "7", None])
def test_upcoming_window_rejects_invalid_days(days):
    with pytest.raises(ValueError, match="1.*30"):
        bankcore.upcoming_window(raw_projection_state(), days=days)


def test_upcoming_window_rejects_null_obligations():
    state = base_state()
    with pytest.raises(ValueError, match="not loaded"):
        bankcore.upcoming_window(state, days=7)


def test_upcoming_window_rejects_incomplete_coverage():
    state = raw_projection_state(covered=("cus_a",))
    with pytest.raises(ValueError, match="coverage"):
        bankcore.upcoming_window(state, days=7)


def test_upcoming_window_rejects_unknown_account():
    with pytest.raises(ValueError):
        bankcore.upcoming_window(raw_projection_state(), days=7, account_id="acc_missing")


def test_upcoming_window_rejects_internal_account():
    with pytest.raises(ValueError):
        bankcore.upcoming_window(raw_projection_state(), days=7, account_id="acc_seed_equity")
