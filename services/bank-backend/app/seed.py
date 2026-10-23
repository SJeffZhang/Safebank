"""P1-only deterministic core fixture, deliberately not PRD's full baseline_v1."""

from datetime import datetime

from safebank_bankcore.ids import derive
from safebank_bankcore.ledger import post_transfer
from safebank_contracts.state import Account, BankState, Card, Customer, LedgerTransfer, Payee
from safebank_contracts.views import ChannelFlags
from sqlalchemy import select, update

from . import models as m
from .state import fingerprint, persist

NOW = datetime.fromisoformat("2026-10-15T09:00:00+08:00")


async def seed_core(conn, key: str) -> bool:
    if await conn.scalar(select(m.customers.c.id).limit(1)):
        return False
    customers = {
        name: Customer(id=name, display_name=display, phone="13800000000")
        for name, display in [("cus_a", "甲（P1）"), ("cus_b", "乙（P1）")]
    }
    accounts = {}
    for account_id, customer, kind, number in [
        ("acc_a", "cus_a", "CHECKING", "6222020000000001"),
        ("acc_s", "cus_a", "SAVINGS", "6222020000000002"),
        ("acc_b", "cus_b", "CHECKING", "6222020000000003"),
        ("acc_seed_equity", None, "INTERNAL", "internal-seed"),
        ("acc_external_clearing", None, "INTERNAL", "internal-external"),
    ]:
        accounts[account_id] = Account(
            id=account_id,
            customer_id=customer,
            type=kind,
            display_name=kind,
            account_no=number,
            flags=0 if customer is None else 1,
        )
    for customer in customers.values():
        await conn.execute(m.customers.insert().values(**customer.model_dump()))
        await conn.execute(m.state_versions.insert().values(customer_id=customer.id, version=0))
    for account in accounts.values():
        await conn.execute(m.accounts.insert().values(**account.model_dump()))
    card = Card(
        id="crd_a",
        account_id="acc_a",
        last4="1234",
        status="ACTIVE",
        single_limit_cents=500_000,
        daily_limit_cents=2_000_000,
        controls=ChannelFlags(online=True, overseas=False, atm=True, quickpass=False),
        created_at=NOW,
    )
    await conn.execute(m.cards.insert().values(**card.model_dump()))
    await conn.execute(
        update(m.accounts).where(m.accounts.c.id == "acc_a").values(current_card_id="crd_a")
    )
    accounts["acc_a"] = accounts["acc_a"].model_copy(update={"current_card_id": "crd_a"})
    payees = {}
    for name, number, target in [
        ("pye_b", "6222020000000003", "acc_b"),
        ("pye_ext", "6222020000000099", None),
    ]:
        payee = Payee(
            id=name,
            customer_id="cus_a",
            name="收款人（P1）",
            alias=None,
            phone=None,
            bank_name="本行" if target else "他行",
            account_no=number,
            target_account_id=target,
            target_fingerprint=fingerprint(number, key),
            created_at=NOW,
        )
        await conn.execute(m.payees.insert().values(**payee.model_dump()))
        payees[name] = payee
    state = BankState(
        sim_now=NOW,
        clock_version=1,
        versions={k: 0 for k in customers},
        accounts=accounts,
        customers=customers,
        cards={card.id: card},
        payees=payees,
    )
    for account_id, amount in [("acc_a", 600_000), ("acc_s", 2_000_000), ("acc_b", 200_000)]:
        cmd = LedgerTransfer(
            id=derive("p1_core_v1", account_id, "trf"),
            debit_account_id="acc_seed_equity",
            credit_account_id=account_id,
            amount_cents=amount,
            code="SEED",
            phase="POSTED",
            pending_id=None,
            plan_id="pln_seed_p1",
            step_id=account_id,
            occurred_at=NOW,
            expires_at=None,
        )
        state, effects, errors = post_transfer(state, cmd, NOW)
        if errors:
            raise RuntimeError("P1 seed violates ledger rules")
        await persist(conn, effects)
    await conn.execute(
        m.clock.insert().values(id=1, sim_now=NOW, version=1, dataset_revision="p1_core_v1")
    )
    return True
