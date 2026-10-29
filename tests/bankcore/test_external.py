"""Tests for simulated external events (PRD §5.1/§7.7/§8.5): credit, debit, split payment.

These are whitelisted system/demo events, not write tools. Money moves between a
customer account and the external clearing account; counterparty text and memos
are stored verbatim as untrusted data.
"""

from datetime import datetime

from safebank_bankcore.banking import Context
from safebank_bankcore.external import (
    ExternalCredit,
    ExternalDebit,
    SplitPayment,
    apply_external_credit,
    apply_external_debit,
    apply_split_payment,
)
from safebank_contracts.codes import Code
from safebank_contracts.state import (
    Account,
    AppendTransaction,
    BankState,
    Card,
    Customer,
    OperationEvent,
    PostTransfer,
)
from safebank_contracts.views import ChannelFlags, SplitItem, SplitView

NOW = datetime.fromisoformat("2026-10-15T09:00:00+08:00")
PLAN_ID = "pln_ext"


def _ctx(step_id="s1"):
    return Context(customer_id="cus_a", plan_id=PLAN_ID, step_id=step_id)


def _card(status="ACTIVE", single=100_000, daily=150_000):
    return Card(
        id="crd_a",
        account_id="acc_a",
        last4="1234",
        status=status,
        single_limit_cents=single,
        daily_limit_cents=daily,
        controls=ChannelFlags(online=True, overseas=False, atm=False, quickpass=False),
        created_at=NOW,
    )


def _state(card_status="ACTIVE", balance=600_000, daily_used=0):
    accounts = {
        "acc_a": Account(
            id="acc_a",
            customer_id="cus_a",
            type="CHECKING",
            display_name="活期",
            account_no="6222020000000001",
            current_card_id="crd_a",
            credits_posted_cents=balance,
        ),
        "acc_s": Account(
            id="acc_s",
            customer_id="cus_a",
            type="SAVINGS",
            display_name="储蓄",
            account_no="6222020000000002",
            credits_posted_cents=50_000,
        ),
        "acc_seed_equity": Account(
            id="acc_seed_equity",
            customer_id=None,
            type="INTERNAL",
            display_name="种子",
            account_no="internal-seed",
            debits_posted_cents=balance + 50_000,
            flags=0,
        ),
        "acc_external_clearing": Account(
            id="acc_external_clearing",
            customer_id=None,
            type="INTERNAL",
            display_name="清算",
            account_no="internal-external",
            flags=0,
        ),
    }
    state = BankState(
        sim_now=NOW,
        clock_version=1,
        versions={"cus_a": 0},
        accounts=accounts,
        customers={"cus_a": Customer(id="cus_a", display_name="甲", phone="13800000000")},
        cards={"crd_a": _card(status=card_status)},
        payees={},
    )
    if daily_used:
        from safebank_contracts.state import Counters

        state = state.model_copy(
            update={
                "counters": Counters(daily_used_cents={"acc_a": daily_used}),
            }
        )
    return state


def _split_state():
    state = _state()
    split = SplitView(
        id="spl_x",
        account_id="acc_a",
        total_cents=10000,
        include_self=False,
        self_share_cents=0,
        received_cents=0,
        outstanding_cents=10000,
        created_at=NOW,
        items=(
            SplitItem(
                id="spi_1", payee_id="pye_f1", payee_name="小林",
                amount_cents=3334, status="UNPAID", paid_at=None,
            ),
            SplitItem(
                id="spi_2", payee_id="pye_f2", payee_name="小周",
                amount_cents=3333, status="UNPAID", paid_at=None,
            ),
            SplitItem(
                id="spi_3", payee_id="pye_f3", payee_name="小陈",
                amount_cents=3333, status="UNPAID", paid_at=None,
            ),
        ),
    )
    return state.model_copy(update={"splits": (split,)})


def _total_balance(state):
    return sum(a.balance_cents for a in state.accounts.values())


def _customer_tx(effects, direction):
    for e in effects:
        if isinstance(e, AppendTransaction) and e.transaction.direction == direction:
            return e.transaction
    raise AssertionError(f"no {direction} transaction in effects")


# ---- external credit --------------------------------------------------------


class TestExternalCredit:
    def test_posts_memo_and_balance_after(self):
        state = _state()
        args = ExternalCredit(
            account_id="acc_a", amount_cents=12345, counterparty="某公司", memo="工资", kind="SALARY"
        )
        after, effects, errors = apply_external_credit(state, args, NOW, _ctx())
        assert not errors
        assert isinstance(effects[0], PostTransfer)
        assert after.accounts["acc_a"].balance_cents == 600_000 + 12345

        tx = _customer_tx(effects, "IN")
        assert tx.memo == "工资"
        assert tx.counterparty == "某公司"
        assert tx.balance_after_cents == 612345
        assert tx.available_after_cents == 612345
        assert isinstance(effects[-1], OperationEvent)
        assert effects[-1].operation.action_kind == "EXTERNAL_CREDIT"

    def test_credit_conservation(self):
        state = _state()
        before = _total_balance(state)
        after, effects, errors = apply_external_credit(
            state, ExternalCredit(account_id="acc_a", amount_cents=999), NOW, _ctx()
        )
        assert not errors
        assert _total_balance(after) == before

    def test_credit_unknown_account(self):
        state = _state()
        after, effects, errors = apply_external_credit(
            state, ExternalCredit(account_id="acc_nope", amount_cents=1), NOW, _ctx()
        )
        assert effects == ()
        assert errors[0].code == Code.NOT_FOUND

    def test_credit_foreign_account(self):
        state = _state()
        after, effects, errors = apply_external_credit(
            state, ExternalCredit(account_id="acc_seed_equity", amount_cents=1), NOW, _ctx()
        )
        assert errors[0].code == Code.NOT_FOUND


# ---- external debit ---------------------------------------------------------


class TestExternalDebit:
    def test_rejected_over_balance(self):
        state = _state()
        # acc_s has no card and 50_000 balance; account-level limits are large.
        after, effects, errors = apply_external_debit(
            state, ExternalDebit(account_id="acc_s", amount_cents=200_000), NOW, _ctx()
        )
        assert effects == ()
        assert errors[0].code == Code.INSUFFICIENT_FUNDS

    def test_rejected_over_single_limit(self):
        state = _state()  # crd_a single limit 100_000
        after, effects, errors = apply_external_debit(
            state, ExternalDebit(account_id="acc_a", amount_cents=200_000), NOW, _ctx()
        )
        assert effects == ()
        assert errors[0].code == Code.CARD_SINGLE_LIMIT

    def test_rejected_over_daily_limit(self):
        state = _state(daily_used=140_000)  # crd_a daily limit 150_000
        after, effects, errors = apply_external_debit(
            state, ExternalDebit(account_id="acc_a", amount_cents=20_000), NOW, _ctx()
        )
        assert effects == ()
        assert errors[0].code == Code.CARD_DAILY_LIMIT

    def test_rejected_on_frozen_card(self):
        state = _state(card_status="FROZEN")
        after, effects, errors = apply_external_debit(
            state, ExternalDebit(account_id="acc_a", amount_cents=100), NOW, _ctx()
        )
        assert errors[0].code == Code.CARD_STATE_CONFLICT

    def test_rejected_on_lost_card(self):
        state = _state(card_status="LOST")
        after, effects, errors = apply_external_debit(
            state, ExternalDebit(account_id="acc_a", amount_cents=100), NOW, _ctx()
        )
        assert errors[0].code == Code.CARD_STATE_CONFLICT

    def test_within_limits_posts_and_counts_daily(self):
        state = _state()
        after, effects, errors = apply_external_debit(
            state,
            ExternalDebit(
                account_id="acc_a", amount_cents=80_000, counterparty="商户X", memo="刷卡"
            ),
            NOW,
            _ctx(),
        )
        assert not errors
        assert after.accounts["acc_a"].balance_cents == 600_000 - 80_000
        assert after.counters.daily_used_cents["acc_a"] == 80_000

        tx = _customer_tx(effects, "OUT")
        assert tx.counterparty == "商户X"
        assert tx.memo == "刷卡"
        assert tx.balance_after_cents == 520_000

        # Second 80_000 spend would exceed the 150_000 daily limit.
        after2, effects2, errors2 = apply_external_debit(
            after, ExternalDebit(account_id="acc_a", amount_cents=80_000), NOW, _ctx("s2")
        )
        assert effects2 == ()
        assert errors2[0].code == Code.CARD_DAILY_LIMIT

    def test_debit_conservation(self):
        state = _state()
        before = _total_balance(state)
        after, effects, errors = apply_external_debit(
            state, ExternalDebit(account_id="acc_a", amount_cents=50_000), NOW, _ctx()
        )
        assert not errors
        assert _total_balance(after) == before


# ---- split payment ----------------------------------------------------------


class TestSplitPayment:
    def test_credits_requester_and_marks_item_paid(self):
        state = _split_state()
        before = _total_balance(state)
        args = SplitPayment(split_id="spl_x", item_id="spi_1", amount_cents=3334)
        after, effects, errors = apply_split_payment(state, args, NOW, _ctx())
        assert not errors
        assert isinstance(effects[0], PostTransfer)
        assert after.accounts["acc_a"].balance_cents == 600_000 + 3334
        assert _total_balance(after) == before

        split = after.splits[0]
        assert split.received_cents == 3334
        assert split.outstanding_cents == 6666
        item = next(it for it in split.items if it.id == "spi_1")
        assert item.status == "PAID"
        assert item.paid_at == NOW
        others = [it for it in split.items if it.id != "spi_1"]
        assert all(it.status == "UNPAID" for it in others)

        tx = _customer_tx(effects, "IN")
        assert tx.counterparty == "小林"
        assert isinstance(effects[-1], OperationEvent)
        assert effects[-1].operation.action_kind == "SPLIT_PAYMENT"

    def test_second_payment_rejected(self):
        state = _split_state()
        args = SplitPayment(split_id="spl_x", item_id="spi_1", amount_cents=3334)
        after, _, errors = apply_split_payment(state, args, NOW, _ctx())
        assert not errors
        _, effects2, errors2 = apply_split_payment(after, args, NOW, _ctx("s2"))
        assert effects2 == ()
        assert errors2[0].code == Code.SPLIT_INVALID

    def test_amount_mismatch_rejected(self):
        state = _split_state()
        after, effects, errors = apply_split_payment(
            state, SplitPayment(split_id="spl_x", item_id="spi_1", amount_cents=3333), NOW, _ctx()
        )
        assert effects == ()
        assert errors[0].code == Code.SPLIT_INVALID
        # Overpay also rejected.
        _, effects2, errors2 = apply_split_payment(
            state, SplitPayment(split_id="spl_x", item_id="spi_1", amount_cents=4000), NOW, _ctx()
        )
        assert effects2 == ()
        assert errors2[0].code == Code.SPLIT_INVALID

    def test_unknown_split_or_item(self):
        state = _split_state()
        _, _, e1 = apply_split_payment(
            state, SplitPayment(split_id="spl_nope", item_id="spi_1", amount_cents=3334), NOW, _ctx()
        )
        assert e1[0].code == Code.NOT_FOUND
        _, _, e2 = apply_split_payment(
            state, SplitPayment(split_id="spl_x", item_id="spi_nope", amount_cents=3334), NOW, _ctx()
        )
        assert e2[0].code == Code.SPLIT_INVALID


# ---- shared input guards ----------------------------------------------------


class TestInputGuards:
    def test_float_amount_rejected(self):
        state = _state()
        _, effects, errors = apply_external_credit(
            state, ExternalCredit(account_id="acc_a", amount_cents=100.0), NOW, _ctx()
        )
        assert effects == ()
        assert errors[0].code == Code.CONTRACT_INVALID

    def test_zero_and_negative_amount_rejected(self):
        state = _state()
        for amount in (0, -1):
            _, effects, errors = apply_external_debit(
                state, ExternalDebit(account_id="acc_a", amount_cents=amount), NOW, _ctx()
            )
            assert effects == ()
            assert errors[0].code == Code.CONTRACT_INVALID

    def test_all_posted_amounts_are_int_cents(self):
        state = _state()
        after, effects, _ = apply_external_credit(
            state, ExternalCredit(account_id="acc_a", amount_cents=12345), NOW, _ctx()
        )
        assert isinstance(after.accounts["acc_a"].balance_cents, int)
        for e in effects:
            if isinstance(e, AppendTransaction):
                assert isinstance(e.transaction.amount_cents, int)
                assert not isinstance(e.transaction.amount_cents, bool)
