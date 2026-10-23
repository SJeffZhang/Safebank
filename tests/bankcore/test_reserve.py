from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
import safebank_bankcore as bankcore
from safebank_bankcore.banking import Context, apply_card, apply_transfer
from safebank_contracts.state import Card, Payee
from safebank_contracts.tools import CardId, Transfer
from safebank_contracts.views import ChannelFlags, FundOrderView, ScheduledView, SubscriptionView

from tests.bankcore.fixtures import NOW, base_state


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


def subscription(**updates) -> SubscriptionView:
    values = {
        "instruction_id": "si_video",
        "billing_id": "bill_video",
        "account_id": "acc_a",
        "merchant_id": "mer_video",
        "merchant_name": "爱看视频",
        "period": "MONTHLY",
        "amount_cents": 3000,
        "max_amount_cents": 3600,
        "next_due_at": at("2026-10-18T06:00:00+08:00"),
        "status": "ACTIVE",
        "annual_cost_cents": 36000,
    }
    return SubscriptionView(**(values | updates))


def projection_state(*, balance=600000, instructions=(), subscriptions=()):
    """Complete synthetic obligations, independent of the incomplete production loader."""
    state = base_state(balance)
    payee = Payee(
        id="pye_external",
        customer_id="cus_a",
        name="外部收款人",
        alias=None,
        phone=None,
        bank_name="他行",
        account_no="6222020000009998",
        target_account_id=None,
        target_fingerprint="external",
        created_at=NOW - timedelta(days=30),
    )
    return state.model_copy(
        update={
            "payees": {payee.id: payee},
            "instructions": tuple(instructions),
            "subscriptions": tuple(subscriptions),
        }
    )


def test_report_uses_actual_bills_and_preserves_merchant_execution_times():
    state = projection_state(
        subscriptions=(
            subscription(),
            subscription(
                instruction_id="si_music",
                billing_id="bill_music",
                merchant_id="mer_music",
                amount_cents=1500,
                max_amount_cents=1800,
                next_due_at=at("2026-10-20T21:15:30+08:00"),
            ),
        )
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


@pytest.mark.parametrize("status", ["DISABLED", "DELETED", "BLOCKED"])
def test_report_excludes_inactive_mandates(status):
    report = bankcore.projected_shortfall(
        projection_state(subscriptions=(subscription(status=status),))
    )
    assert report.obligations == ()
    assert report.total_reserve_cents == 0
    assert report.accounts["acc_a"].max_outflow_cents == 600000


def test_subscription_window_excludes_now_and_includes_end():
    end = NOW + timedelta(days=7)
    state = projection_state(
        subscriptions=(
            subscription(next_due_at=NOW),
            subscription(instruction_id="si_end", billing_id="bill_end", next_due_at=end),
            subscription(
                instruction_id="si_late",
                billing_id="bill_late",
                next_due_at=end + timedelta(seconds=1),
            ),
        )
    )
    report = bankcore.projected_shortfall(state)
    assert [(item.id, item.due_at) for item in report.obligations] == [("bill_end", end)]


@pytest.mark.parametrize("next_due_at", [NOW, NOW - timedelta(days=7)])
def test_weekly_subscription_expands_next_period_without_double_counting_now(next_due_at):
    report = bankcore.projected_shortfall(
        projection_state(subscriptions=(subscription(period="WEEKLY", next_due_at=next_due_at),))
    )
    assert [(item.due_at, item.delta_cents) for item in report.obligations] == [
        (NOW + timedelta(days=7), -3000)
    ]


def test_once_subscription_does_not_recur():
    report = bankcore.projected_shortfall(
        projection_state(subscriptions=(subscription(period="ONCE", next_due_at=NOW),))
    )
    assert report.obligations == ()


def test_stale_monthly_bill_is_rejected_without_guessing_missing_anchor():
    with pytest.raises(ValueError, match="anchor"):
        bankcore.projected_shortfall(
            projection_state(subscriptions=(subscription(next_due_at=NOW - timedelta(days=1)),))
        )


@pytest.mark.parametrize("days", [0, -1, 1, 8, 30, 7.0, True, "7", None])
def test_projection_rejects_unsupported_days_instead_of_guessing_monthly_recurrence(days):
    with pytest.raises(ValueError, match="7"):
        bankcore.projected_shortfall(projection_state(), days=days)


def test_scheduled_debits_precede_merchant_charges_at_equal_timestamp():
    report = bankcore.projected_shortfall(
        projection_state(instructions=(instruction(id="si_z"),), subscriptions=(subscription(),))
    )
    assert [(item.rank, item.id) for item in report.obligations] == [(3, "si_z"), (4, "bill_video")]
    assert report.total_reserve_cents == 4000


def test_existing_shortfall_preserves_negative_max_outflow_and_uses_available_balance():
    state = projection_state(balance=2000, subscriptions=(subscription(),))
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
    state = projection_state(balance=1000, subscriptions=(subscription(),))
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
        state.model_copy(
            update={
                "accounts": accounts,
                "orders": (order,),
            }
        )
    )
    assert report.accounts["acc_a"].max_outflow_cents == -2000
    assert report.total_shortfall_cents == 2000


def test_report_does_not_mutate_inputs_and_its_mappings_and_records_are_immutable():
    state = projection_state(instructions=(instruction(),), subscriptions=(subscription(),))
    before = state.model_dump_json()
    report = bankcore.projected_shortfall(state)
    assert state.model_dump_json() == before
    with pytest.raises(TypeError):
        report.accounts["acc_a"] = report.accounts["acc_s"]
    with pytest.raises(FrozenInstanceError):
        report.accounts["acc_a"].reserve_cents = 0
    with pytest.raises(FrozenInstanceError):
        report.obligations[0].delta_cents = 0


@pytest.mark.parametrize("reference", ["source", "target", "payee", "payee_owner", "target_owner"])
def test_unknown_ownership_fails_instead_of_losing_scheduled_obligations(reference):
    item = instruction()
    state = projection_state(instructions=(item,))
    if reference == "source":
        state = state.model_copy(
            update={"instructions": (item.model_copy(update={"from_account_id": "acc_missing"}),)}
        )
    elif reference == "target":
        state = state.model_copy(
            update={
                "instructions": (
                    item.model_copy(update={"payee_id": None, "to_account_id": "acc_missing"}),
                )
            }
        )
    elif reference == "payee":
        state = state.model_copy(update={"payees": {}})
    elif reference == "payee_owner":
        payee = state.payees["pye_external"].model_copy(update={"customer_id": "cus_b"})
        state = state.model_copy(update={"payees": {payee.id: payee}})
    else:
        payee = state.payees["pye_external"].model_copy(update={"target_account_id": "acc_missing"})
        state = state.model_copy(update={"payees": {payee.id: payee}})
    with pytest.raises(ValueError):
        bankcore.projected_shortfall(state)


def test_unknown_subscription_account_is_rejected():
    with pytest.raises(ValueError):
        bankcore.projected_shortfall(
            projection_state(subscriptions=(subscription(account_id="acc_missing"),))
        )


@pytest.mark.parametrize("identity", ["instruction", "billing"])
def test_duplicate_merchant_obligations_are_rejected(identity):
    duplicate = subscription(
        instruction_id="si_video" if identity == "instruction" else "si_other",
        billing_id="bill_video" if identity == "billing" else "bill_other",
    )
    with pytest.raises(ValueError):
        bankcore.projected_shortfall(projection_state(subscriptions=(subscription(), duplicate)))


def with_balances(state, **balances):
    accounts = dict(state.accounts)
    for account_id, balance in balances.items():
        accounts[account_id] = accounts[account_id].model_copy(
            update={
                "credits_posted_cents": balance,
                "debits_posted_cents": 0,
            }
        )
    return state.model_copy(update={"accounts": accounts})


def test_own_scheduled_credit_can_fund_same_instant_merchant_charge():
    state = projection_state(
        instructions=(instruction(from_account_id="acc_s", payee_id=None, to_account_id="acc_a"),),
        subscriptions=(subscription(amount_cents=1000),),
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
    state = projection_state(
        instructions=(instruction(from_account_id="acc_s", payee_id=None, to_account_id="acc_a"),),
        subscriptions=(
            subscription(amount_cents=1000, next_due_at=at("2026-10-17T06:00:00+08:00")),
        ),
    )
    report = bankcore.projected_shortfall(with_balances(state, acc_a=0, acc_s=500))
    assert report.accounts["acc_a"].reserve_cents == 1000
    assert report.accounts["acc_a"].shortfall_cents == 1000
    assert report.accounts["acc_s"].max_outflow_cents == -500
    assert report.total_shortfall_cents == 1500


def test_payee_resolved_to_same_owner_requires_explicit_own_transfer_target():
    state = projection_state(instructions=(instruction(),))
    payee = state.payees["pye_external"].model_copy(update={"target_account_id": "acc_s"})
    with pytest.raises(ValueError):
        bankcore.projected_shortfall(state.model_copy(update={"payees": {payee.id: payee}}))


def test_cross_customer_scheduled_transfer_does_not_guarantee_target_income():
    state = projection_state(
        instructions=(instruction(),), subscriptions=(subscription(account_id="acc_b"),)
    )
    payee = state.payees["pye_external"].model_copy(update={"target_account_id": "acc_b"})
    state = with_balances(state.model_copy(update={"payees": {payee.id: payee}}), acc_b=0)
    report = bankcore.projected_shortfall(state)
    assert report.accounts["acc_b"].shortfall_cents == 3000
    assert [(item.account_id, item.delta_cents) for item in report.obligations] == [
        ("acc_a", -1000),
        ("acc_b", -3000),
    ]


def test_direct_target_requires_same_owner():
    state = projection_state(instructions=(instruction(payee_id=None, to_account_id="acc_b"),))
    with pytest.raises(ValueError):
        bankcore.projected_shortfall(state)


def test_internal_account_cannot_act_as_customer_obligation_source():
    state = projection_state(instructions=(instruction(from_account_id="acc_seed_equity"),))
    with pytest.raises(ValueError):
        bankcore.projected_shortfall(state)


def prd_state():
    return projection_state(
        instructions=(
            instruction(
                id="si_mom",
                amount_cents=100000,
                period="MONTHLY",
                anchor_day=5,
                next_run_at=at("2026-11-05T06:00:00+08:00"),
            ),
        ),
        subscriptions=(
            subscription(),
            subscription(
                instruction_id="si_music",
                billing_id="bill_music",
                merchant_id="mer_music",
                merchant_name="乐听音乐",
                amount_cents=1500,
                max_amount_cents=1800,
                next_due_at=at("2026-10-20T06:00:00+08:00"),
            ),
            subscription(
                instruction_id="si_cloud",
                billing_id="bill_cloud",
                merchant_id="mer_cloud",
                merchant_name="云盘存储",
                amount_cents=2100,
                max_amount_cents=2520,
                next_due_at=at("2026-11-01T06:00:00+08:00"),
            ),
            subscription(
                instruction_id="si_gym",
                billing_id="bill_gym",
                merchant_id="mer_gym",
                merchant_name="力健健身",
                amount_cents=19900,
                max_amount_cents=23880,
                next_due_at=at("2026-10-25T06:00:00+08:00"),
            ),
        ),
    )


def test_prd_complete_obligations_and_real_transfer_share_exact_295500_limit():
    state = prd_state()
    payee = state.payees["pye_external"].model_copy(
        update={
            "id": "pye_ww",
            "name": "王五",
            "target_account_id": "acc_b",
        }
    )
    state = state.model_copy(update={"payees": dict(state.payees) | {payee.id: payee}})
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

    # W2's amount template and W3's hint consume this same report field.
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
    state = prd_state()
    state = with_balances(state, acc_a=1000)
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


def test_cancelling_an_obligation_reduces_existing_shortfall_without_l2_policy_in_bankcore():
    state = with_balances(prd_state(), acc_a=1000)
    before = bankcore.projected_shortfall(state)
    # Cancellation persistence belongs to S3; project its complete resulting snapshot here.
    cancelled = state.model_copy(
        update={
            "subscriptions": tuple(
                item.model_copy(update={"status": "DELETED"})
                if item.instruction_id == "si_video"
                else item
                for item in state.subscriptions
            )
        }
    )
    after = bankcore.projected_shortfall(cancelled)
    assert before.total_shortfall_cents == 3500
    assert after.total_shortfall_cents == 500


def test_report_is_identical_under_account_instruction_and_subscription_permutations():
    state = prd_state()
    own = instruction(id="si_own", from_account_id="acc_s", payee_id=None, to_account_id="acc_a")
    state = state.model_copy(update={"instructions": (*state.instructions, own)})
    reversed_state = state.model_copy(
        update={
            "accounts": dict(reversed(tuple(state.accounts.items()))),
            "instructions": state.instructions[::-1],
            "subscriptions": state.subscriptions[::-1],
        }
    )
    assert bankcore.projected_shortfall(state) == bankcore.projected_shortfall(reversed_state)
