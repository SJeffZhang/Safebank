from datetime import datetime

from safebank_contracts.state import Account, BankState, Customer

NOW = datetime.fromisoformat("2026-10-15T09:00:00+08:00")


def base_state(balance=600_000):
    accounts = {
        "acc_a": Account(
            id="acc_a",
            customer_id="cus_a",
            type="CHECKING",
            display_name="活期",
            account_no="6222020000000001",
            credits_posted_cents=balance,
        ),
        "acc_s": Account(
            id="acc_s",
            customer_id="cus_a",
            type="SAVINGS",
            display_name="储蓄",
            account_no="6222020000000002",
            credits_posted_cents=2_000_000,
        ),
        "acc_b": Account(
            id="acc_b",
            customer_id="cus_b",
            type="CHECKING",
            display_name="活期",
            account_no="6222020000000003",
            credits_posted_cents=200_000,
        ),
        "acc_seed_equity": Account(
            id="acc_seed_equity",
            customer_id=None,
            type="INTERNAL",
            display_name="种子",
            account_no="internal-seed",
            debits_posted_cents=balance + 2_200_000,
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
    return BankState(
        sim_now=NOW,
        clock_version=1,
        versions={"cus_a": 0, "cus_b": 0},
        accounts=accounts,
        customers={
            k: Customer(id=k, display_name=name, phone="13800000000")
            for k, name in [("cus_a", "甲"), ("cus_b", "乙")]
        },
        cards={},
        payees={},
    )
