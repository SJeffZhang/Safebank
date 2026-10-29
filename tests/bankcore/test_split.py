"""Tests for AA split request — allocate, create, query projection."""

from datetime import datetime

import pytest
from safebank_bankcore.split import (
    _SplitItemData,
    _SplitRequestData,
    allocate,
    apply_create_split_request,
    build_split_view,
    paginate_splits,
)
from safebank_contracts.codes import Code
from safebank_contracts.state import Account, BankState, Customer, Payee
from safebank_contracts.tools import CreateSplit

# Re-use the same fixture-style helpers as other bankcore tests.
NOW = datetime.fromisoformat("2026-10-15T09:00:00+08:00")
PLAN_ID = "pln_c4"
STEP_ID = "s1"


def _ctx():
    from safebank_bankcore.banking import Context

    return Context(customer_id="cus_a", plan_id=PLAN_ID, step_id=STEP_ID)


def _state(extra_payees=None, accounts_override=None):
    """Minimal state for cus_a with one checking account and optional payees."""
    accounts = accounts_override or {
        "acc_a": Account(
            id="acc_a",
            customer_id="cus_a",
            type="CHECKING",
            display_name="活期",
            account_no="6222020000000001",
            credits_posted_cents=600_000,
        ),
        "acc_s": Account(
            id="acc_s",
            customer_id="cus_a",
            type="SAVINGS",
            display_name="储蓄",
            account_no="6222020000000002",
            credits_posted_cents=2_000_000,
        ),
        "acc_seed_equity": Account(
            id="acc_seed_equity",
            customer_id=None,
            type="INTERNAL",
            display_name="种子",
            account_no="internal-seed",
            debits_posted_cents=2_600_000,
            flags=0,
        ),
    }
    payees = {
        "pye_f1": Payee(
            id="pye_f1",
            customer_id="cus_a",
            name="小林",
            alias=None,
            phone=None,
            bank_name="他行",
            account_no="6222020000000101",
            target_account_id=None,
            target_fingerprint="fp_f1",
            created_at=NOW,
        ),
        "pye_f2": Payee(
            id="pye_f2",
            customer_id="cus_a",
            name="小周",
            alias=None,
            phone=None,
            bank_name="他行",
            account_no="6222020000000102",
            target_account_id=None,
            target_fingerprint="fp_f2",
            created_at=NOW,
        ),
        "pye_f3": Payee(
            id="pye_f3",
            customer_id="cus_a",
            name="小陈",
            alias=None,
            phone=None,
            bank_name="他行",
            account_no="6222020000000103",
            target_account_id=None,
            target_fingerprint="fp_f3",
            created_at=NOW,
        ),
        "pye_b1": Payee(
            id="pye_b1",
            customer_id="cus_b",
            name="乙的收款人",
            alias=None,
            phone=None,
            bank_name="本行",
            account_no="6222020000000201",
            target_account_id=None,
            target_fingerprint="fp_b1",
            created_at=NOW,
        ),
    }
    if extra_payees:
        payees.update(extra_payees)
    return BankState(
        sim_now=NOW,
        clock_version=1,
        versions={"cus_a": 0, "cus_b": 0},
        accounts=accounts,
        payees=payees,
        customers={
            "cus_a": Customer(id="cus_a", display_name="甲", phone="13800000000"),
            "cus_b": Customer(id="cus_b", display_name="乙", phone="13800000001"),
        },
        cards={},
    )


# ---- allocate ---------------------------------------------------------------


class TestAllocate:
    def test_three_people_no_self(self):
        amounts, self_share = allocate(10000, ("pye_f1", "pye_f2", "pye_f3"), include_self=False)
        assert amounts == (3334, 3333, 3333)
        assert self_share == 0
        assert sum(amounts) == 10000

    def test_three_people_with_self(self):
        amounts, self_share = allocate(10000, ("pye_f1", "pye_f2"), include_self=True)
        # 3 heads: self + f1 + f2. 10000/3 = 3333 rem 1
        assert amounts == (3333, 3333)
        assert self_share == 3334
        assert sum(amounts) + self_share == 10000

    def test_two_people_no_self_remainder_to_first(self):
        amounts, self_share = allocate(101, ("pye_f1", "pye_f2"), include_self=False)
        assert amounts == (51, 50)  # 101/2=50 rem 1 → first
        assert self_share == 0

    def test_two_people_with_self_remainder_to_self(self):
        amounts, self_share = allocate(101, ("pye_f1",), include_self=True)
        assert amounts == (50,)  # 101/2=50 rem 1
        assert self_share == 51

    def test_one_cent_each(self):
        amounts, self_share = allocate(3, ("pye_f1", "pye_f2"), include_self=True)
        assert amounts == (1, 1)
        assert self_share == 1

    def test_twenty_participants_with_self(self):
        ids = tuple(f"pye_{i}" for i in range(20))
        total = 2100  # 100 each * 21 people = 2100
        amounts, self_share = allocate(total, ids, include_self=True)
        assert all(a == 100 for a in amounts)
        assert self_share == 100
        assert sum(amounts) + self_share == total

    def test_even_division_no_remainder(self):
        amounts, self_share = allocate(900, ("pye_f1", "pye_f2"), include_self=True)
        assert amounts == (300, 300)
        assert self_share == 300

    def test_insufficient_total_raises(self):
        with pytest.raises(ValueError, match=">= 1 cent"):
            allocate(2, ("pye_f1", "pye_f2"), include_self=True)  # 3 people, 2 cents

    def test_empty_participants_without_self_raises(self):
        with pytest.raises(ValueError, match="positive"):
            allocate(100, (), include_self=False)


# ---- apply_create_split_request --------------------------------------------


class TestApplyCreateSplit:
    def test_basic_create_without_self(self):
        state = _state()
        args = CreateSplit(
            account_id="acc_a",
            total_cents=10000,
            participant_payee_ids=("pye_f1", "pye_f2", "pye_f3"),
            include_self=False,
            memo=None,
        )
        result_state, effects, errors = apply_create_split_request(state, args, NOW, _ctx())
        assert not errors
        assert len(effects) == 2  # InsertSplit + OperationEvent
        assert result_state is state  # state unchanged

        split_effect = effects[0]
        sv = split_effect.split
        assert sv.id.startswith("spl_")
        assert sv.account_id == "acc_a"
        assert sv.total_cents == 10000
        assert sv.include_self is False
        assert sv.self_share_cents == 0
        assert sv.received_cents == 0
        assert sv.outstanding_cents == 10000
        assert len(sv.items) == 3
        assert {it.payee_id for it in sv.items} == {"pye_f1", "pye_f2", "pye_f3"}
        for it in sv.items:
            assert it.status == "UNPAID"
            assert it.paid_at is None
        assert sum(it.amount_cents for it in sv.items) == 10000

        op = effects[1]
        assert op.operation.action_kind == "SPLIT_CREATE"

    def test_basic_create_with_self(self):
        state = _state()
        args = CreateSplit(
            account_id="acc_a",
            total_cents=10000,
            participant_payee_ids=("pye_f1", "pye_f2"),
            include_self=True,
            memo="dinner",
        )
        _, effects, errors = apply_create_split_request(state, args, NOW, _ctx())
        assert not errors
        sv = effects[0].split
        assert sv.include_self is True
        assert sv.self_share_cents == 3334  # remainder to self
        assert sv.outstanding_cents == 6666  # self share is not owed by others
        assert len(sv.items) == 2
        assert sum(it.amount_cents for it in sv.items) + sv.self_share_cents == 10000

    def test_one_cent_boundary(self):
        state = _state()
        args = CreateSplit(
            account_id="acc_a",
            total_cents=2,
            participant_payee_ids=("pye_f1",),
            include_self=True,
            memo=None,
        )
        _, effects, errors = apply_create_split_request(state, args, NOW, _ctx())
        assert not errors
        sv = effects[0].split
        assert sv.self_share_cents == 1
        assert sv.items[0].amount_cents == 1

    def test_twenty_participants_boundary(self):
        """20 participants, no self. Need 20 payees for cus_a."""
        state = _state(
            extra_payees={
                f"pye_{i}": Payee(
                    id=f"pye_{i}",
                    customer_id="cus_a",
                    name=f"P{i}",
                    alias=None,
                    phone=None,
                    bank_name="他行",
                    account_no=f"6222{i:010d}",
                    target_account_id=None,
                    target_fingerprint=f"fp_{i}",
                    created_at=NOW,
                )
                for i in range(20)
            }
        )
        payee_ids = tuple(f"pye_{i}" for i in range(20))
        args = CreateSplit(
            account_id="acc_a",
            total_cents=2000,  # 100 each
            participant_payee_ids=payee_ids,
            include_self=False,
            memo=None,
        )
        _, effects, errors = apply_create_split_request(state, args, NOW, _ctx())
        assert not errors
        sv = effects[0].split
        assert len(sv.items) == 20
        assert all(it.amount_cents == 100 for it in sv.items)

    def test_account_not_found(self):
        state = _state()
        args = CreateSplit(
            account_id="acc_nonexistent",
            total_cents=10000,
            participant_payee_ids=("pye_f1",),
            include_self=False,
            memo=None,
        )
        _, effects, errors = apply_create_split_request(state, args, NOW, _ctx())
        assert effects == ()
        assert len(errors) == 1
        assert errors[0].code == Code.NOT_FOUND

    def test_account_not_owned_by_customer(self):
        state = _state(
            accounts_override={
                "acc_b": Account(
                    id="acc_b",
                    customer_id="cus_b",
                    type="CHECKING",
                    display_name="活期",
                    account_no="6222020000000003",
                    credits_posted_cents=200_000,
                ),
            }
        )
        args = CreateSplit(
            account_id="acc_b",
            total_cents=10000,
            participant_payee_ids=("pye_f1",),
            include_self=False,
            memo=None,
        )
        _, effects, errors = apply_create_split_request(state, args, NOW, _ctx())
        assert errors[0].code == Code.OWNERSHIP_MISMATCH

    def test_payee_not_found(self):
        state = _state()
        args = CreateSplit(
            account_id="acc_a",
            total_cents=10000,
            participant_payee_ids=("pye_f1", "pye_nonexistent"),
            include_self=False,
            memo=None,
        )
        _, effects, errors = apply_create_split_request(state, args, NOW, _ctx())
        assert any(e.code == Code.PAYEE_NOT_FOUND for e in errors)

    def test_payee_not_owned_by_customer(self):
        state = _state()
        args = CreateSplit(
            account_id="acc_a",
            total_cents=10000,
            participant_payee_ids=("pye_b1",),  # belongs to cus_b
            include_self=False,
            memo=None,
        )
        _, effects, errors = apply_create_split_request(state, args, NOW, _ctx())
        assert any(e.code == Code.OWNERSHIP_MISMATCH for e in errors)

    def test_balance_and_ledger_unchanged(self):
        """Verify no ledger transfers occur — state is returned unchanged."""
        state = _state()
        args = CreateSplit(
            account_id="acc_a",
            total_cents=10000,
            participant_payee_ids=("pye_f1", "pye_f2"),
            include_self=False,
            memo=None,
        )
        result_state, effects, errors = apply_create_split_request(state, args, NOW, _ctx())
        assert not errors
        # State identity and contents unchanged.
        assert result_state is state
        # Accounts not touched.
        for acc_id in state.accounts:
            assert (
                state.accounts[acc_id].credits_posted_cents
                == result_state.accounts[acc_id].credits_posted_cents
            )
            assert (
                state.accounts[acc_id].debits_posted_cents
                == result_state.accounts[acc_id].debits_posted_cents
            )

    def test_idempotent_no_duplicate_keys(self):
        """Two calls with different step_id should produce distinct request IDs."""
        state = _state()
        args = CreateSplit(
            account_id="acc_a",
            total_cents=10000,
            participant_payee_ids=("pye_f1",),
            include_self=False,
            memo=None,
        )
        ctx1 = __import__("safebank_bankcore.banking", fromlist=["Context"]).Context(
            "cus_a", PLAN_ID, "s1"
        )
        ctx2 = __import__("safebank_bankcore.banking", fromlist=["Context"]).Context(
            "cus_a", PLAN_ID, "s2"
        )
        _, e1, _ = apply_create_split_request(state, args, NOW, ctx1)
        _, e2, _ = apply_create_split_request(state, args, NOW, ctx2)
        assert e1[0].split.id != e2[0].split.id


# ---- build_split_view / paginate_splits ------------------------------------


class TestQueryProjection:
    @staticmethod
    def _req(**kw):
        defaults = dict(
            id="spl_1",
            customer_id="cus_a",
            account_id="acc_a",
            total_cents=10000,
            include_self=False,
            self_share_cents=0,
            created_at=NOW,
            items=(),
        )
        return _SplitRequestData(**(defaults | kw))

    @staticmethod
    def _item(**kw):
        defaults = dict(
            id="spi_1",
            payee_id="pye_f1",
            payee_name="小林",
            amount_cents=3334,
            status="UNPAID",
            paid_at=None,
        )
        return _SplitItemData(**(defaults | kw))

    def test_build_all_unpaid(self):
        items = (
            self._item(id="spi_1", amount_cents=3334, status="UNPAID"),
            self._item(
                id="spi_2", payee_id="pye_f2", payee_name="小周", amount_cents=3333, status="UNPAID"
            ),
            self._item(
                id="spi_3", payee_id="pye_f3", payee_name="小陈", amount_cents=3333, status="UNPAID"
            ),
        )
        req = self._req(items=items)
        sv = build_split_view(req)
        assert sv.received_cents == 0
        assert sv.outstanding_cents == 10000
        assert len(sv.items) == 3

    def test_build_partially_paid(self):
        items = (
            self._item(id="spi_1", amount_cents=3334, status="PAID", paid_at=NOW),
            self._item(
                id="spi_2", payee_id="pye_f2", payee_name="小周", amount_cents=3333, status="UNPAID"
            ),
            self._item(
                id="spi_3", payee_id="pye_f3", payee_name="小陈", amount_cents=3333, status="UNPAID"
            ),
        )
        req = self._req(items=items)
        sv = build_split_view(req)
        assert sv.received_cents == 3334
        assert sv.outstanding_cents == 6666

    def test_build_fully_paid(self):
        items = (
            self._item(id="spi_1", amount_cents=3334, status="PAID", paid_at=NOW),
            self._item(
                id="spi_2",
                payee_id="pye_f2",
                payee_name="小周",
                amount_cents=3333,
                status="PAID",
                paid_at=NOW,
            ),
            self._item(
                id="spi_3",
                payee_id="pye_f3",
                payee_name="小陈",
                amount_cents=3333,
                status="PAID",
                paid_at=NOW,
            ),
        )
        req = self._req(items=items)
        sv = build_split_view(req)
        assert sv.received_cents == 10000
        assert sv.outstanding_cents == 0

    def test_pagination_first_page(self):
        reqs = tuple(
            self._req(
                id=f"spl_{i}",
                created_at=datetime.fromisoformat(f"2026-10-{15 + i:02d}T09:00:00+08:00"),
            )
            for i in range(5)
        )
        page, next_cursor = paginate_splits(reqs, cursor=None, limit=2)
        assert len(page) == 2
        assert page[0].id == "spl_0"
        assert page[1].id == "spl_1"
        assert next_cursor == "spl_1"

    def test_pagination_with_cursor(self):
        reqs = tuple(self._req(id=f"spl_{i}", created_at=NOW) for i in range(5))
        page, next_cursor = paginate_splits(reqs, cursor="spl_1", limit=2)
        assert page[0].id == "spl_2"
        assert page[1].id == "spl_3"
        assert next_cursor == "spl_3"

    def test_pagination_last_page(self):
        reqs = tuple(self._req(id=f"spl_{i}", created_at=NOW) for i in range(3))
        page, next_cursor = paginate_splits(reqs, cursor="spl_1", limit=2)
        assert len(page) == 1
        assert page[0].id == "spl_2"
        assert next_cursor is None

    def test_pagination_empty(self):
        page, next_cursor = paginate_splits((), cursor=None, limit=10)
        assert page == ()
        assert next_cursor is None

    def test_pagination_stable_order(self):
        """Sorting is by created_at then id."""
        reqs = tuple(
            [
                self._req(id="spl_z", created_at=NOW),
                self._req(id="spl_a", created_at=NOW),
                self._req(id="spl_m", created_at=NOW),
            ]
        )
        page, _ = paginate_splits(reqs, cursor=None, limit=10)
        assert [sv.id for sv in page] == ["spl_a", "spl_m", "spl_z"]

    def test_build_with_self_share(self):
        """When include_self is True, items only cover external participants."""
        items = (
            self._item(
                id="spi_1", payee_id="pye_f1", amount_cents=3333, status="PAID", paid_at=NOW
            ),
            self._item(
                id="spi_2", payee_id="pye_f2", payee_name="小周", amount_cents=3333, status="UNPAID"
            ),
        )
        req = self._req(include_self=True, self_share_cents=3334, items=items)
        sv = build_split_view(req)
        assert sv.self_share_cents == 3334
        assert sv.received_cents == 3333
        assert sv.outstanding_cents == 3333  # total - self_share - received
