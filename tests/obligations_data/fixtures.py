"""Fresh PRD §2.2 obligations acceptance fixture (tests only, never app.seed).

Installs the four PRD customers, their accounts/cards/payees and the real
merchant billings, standing instructions and run history, funding accounts
through the same pure ledger + persist path used by the P1 seed. This is an
acceptance subset, not the full baseline_v1 dataset.
"""

from datetime import datetime, time

from app import models as m
from app.state import fingerprint, persist
from safebank_bankcore.ledger import post_transfer
from safebank_contracts.obligations import (
    Merchant,
    MerchantBilling,
    MerchantMandate,
    ScheduledInstruction,
)
from safebank_contracts.state import Account, BankState, Card, Customer, LedgerTransfer, Payee
from safebank_contracts.views import ChannelFlags

NOW = datetime.fromisoformat("2026-10-15T09:00:00+08:00")
GRANT = "0" * 64
KEY = "test-fingerprint-key"
DATASET = "obligations_acceptance"


def at(value: str) -> datetime:
    return datetime.fromisoformat(value)


async def install_core(conn) -> None:
    # cus_sb carries a sentinel phone so ownership/privacy tests can prove it never
    # leaks into a snapshot whose scope does not include that customer.
    customers = {
        cid: Customer(
            id=cid,
            display_name=name,
            phone="13900009999" if cid == "cus_sb" else "13800000000",
        )
        for cid, name in [
            ("cus_zs", "张三"),
            ("cus_ls", "李四"),
            ("cus_sb", "孙八"),
            ("cus_ww", "王五"),
        ]
    }
    accounts = {}
    for account_id, customer, kind, number in [
        ("acc_zs_chk", "cus_zs", "CHECKING", "6222020000000001"),
        ("acc_zs_sav", "cus_zs", "SAVINGS", "6222020000000002"),
        ("acc_ls_chk", "cus_ls", "CHECKING", "6222020000000003"),
        ("acc_ls_sav", "cus_ls", "SAVINGS", "6222020000000004"),
        ("acc_sb_chk", "cus_sb", "CHECKING", "6222020000000005"),
        ("acc_ww_chk", "cus_ww", "CHECKING", "6222020000000006"),
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
        id="crd_zs_1",
        account_id="acc_zs_chk",
        last4="1234",
        status="ACTIVE",
        single_limit_cents=500_000,
        daily_limit_cents=2_000_000,
        controls=ChannelFlags(online=True, overseas=False, atm=True, quickpass=False),
        created_at=NOW,
    )
    await conn.execute(m.cards.insert().values(**card.model_dump()))
    await conn.execute(
        m.accounts.update()
        .where(m.accounts.c.id == "acc_zs_chk")
        .values(current_card_id="crd_zs_1")
    )
    accounts["acc_zs_chk"] = accounts["acc_zs_chk"].model_copy(
        update={"current_card_id": "crd_zs_1"}
    )
    payees = {}
    for pid, number, target in [
        ("pye_zs_mom", "6222020000000098", None),
        ("pye_zs_wwu", "6222020000000097", None),
        ("pye_zs_zl", "6222020000000096", None),
        ("pye_zs_ww", "6222020000000006", "acc_ww_chk"),
    ]:
        payee = Payee(
            id=pid,
            customer_id="cus_zs",
            name=pid,
            alias=None,
            phone=None,
            bank_name="本行" if target else "他行",
            account_no=number,
            target_account_id=target,
            target_fingerprint=fingerprint(number, KEY),
            created_at=NOW,
        )
        await conn.execute(m.payees.insert().values(**payee.model_dump()))
        payees[pid] = payee
    state = BankState(
        sim_now=NOW,
        clock_version=1,
        versions={k: 0 for k in customers},
        accounts=accounts,
        customers=customers,
        cards={card.id: card},
        payees=payees,
    )
    for account_id, amount in [
        ("acc_zs_chk", 600_000),
        ("acc_zs_sav", 2_000_000),
        ("acc_ls_chk", 1_200_000),
        ("acc_ls_sav", 5_000_000),
        ("acc_sb_chk", 3_000_000),
        ("acc_ww_chk", 200_000),
    ]:
        cmd = LedgerTransfer(
            id=f"trf_seed_{account_id}",
            debit_account_id="acc_seed_equity",
            credit_account_id=account_id,
            amount_cents=amount,
            code="SEED",
            phase="POSTED",
            pending_id=None,
            plan_id="pln_seed",
            step_id=account_id,
            occurred_at=NOW,
            expires_at=None,
        )
        state, effects, errors = post_transfer(state, cmd, NOW)
        if errors:
            raise RuntimeError("PRD fixture seed violates ledger rules")
        await persist(conn, effects)
    await conn.execute(
        m.clock.insert().values(id=1, sim_now=NOW, version=1, dataset_revision=DATASET)
    )
    return state


async def install_obligations(conn) -> None:
    for merchant in [
        Merchant(
            id="mer_video", name="爱看视频", mcc="5816", category="娱乐", usage_expected=False
        ),
        Merchant(
            id="mer_music", name="乐听音乐", mcc="5815", category="娱乐", usage_expected=False
        ),
        Merchant(
            id="mer_cloud", name="云盘存储", mcc="5734", category="生活服务", usage_expected=False
        ),
        Merchant(id="mer_gym", name="力健健身", mcc="7997", category="健身", usage_expected=True),
    ]:
        await conn.execute(m.merchants.insert().values(**merchant.model_dump()))
    for bill in [
        MerchantBilling(
            id="bill_zs_video",
            customer_id="cus_zs",
            account_id="acc_zs_chk",
            merchant_id="mer_video",
            amount_cents=3000,
            period="MONTHLY",
            anchor_day=18,
            weekday=None,
            time_of_day=time(6, 0),
            next_due_at=at("2026-10-18T06:00:00+08:00"),
            active=True,
            created_at=NOW,
        ),
        MerchantBilling(
            id="bill_zs_music",
            customer_id="cus_zs",
            account_id="acc_zs_chk",
            merchant_id="mer_music",
            amount_cents=1500,
            period="MONTHLY",
            anchor_day=20,
            weekday=None,
            time_of_day=time(6, 0),
            next_due_at=at("2026-10-20T06:00:00+08:00"),
            active=True,
            created_at=NOW,
        ),
        MerchantBilling(
            id="bill_zs_cloud",
            customer_id="cus_zs",
            account_id="acc_zs_chk",
            merchant_id="mer_cloud",
            amount_cents=2100,
            period="MONTHLY",
            anchor_day=1,
            weekday=None,
            time_of_day=time(6, 0),
            next_due_at=at("2026-11-01T06:00:00+08:00"),
            active=True,
            created_at=NOW,
        ),
        MerchantBilling(
            id="bill_zs_gym",
            customer_id="cus_zs",
            account_id="acc_zs_chk",
            merchant_id="mer_gym",
            amount_cents=19900,
            period="MONTHLY",
            anchor_day=25,
            weekday=None,
            time_of_day=time(6, 0),
            next_due_at=at("2026-10-25T06:00:00+08:00"),
            active=True,
            created_at=NOW,
        ),
        MerchantBilling(
            id="bill_ls_video",
            customer_id="cus_ls",
            account_id="acc_ls_chk",
            merchant_id="mer_video",
            amount_cents=3000,
            period="MONTHLY",
            anchor_day=20,
            weekday=None,
            time_of_day=time(6, 0),
            next_due_at=at("2026-10-20T06:00:00+08:00"),
            active=True,
            created_at=NOW,
        ),
        MerchantBilling(
            id="bill_ls_music",
            customer_id="cus_ls",
            account_id="acc_ls_chk",
            merchant_id="mer_music",
            amount_cents=1500,
            period="MONTHLY",
            anchor_day=22,
            weekday=None,
            time_of_day=time(6, 0),
            next_due_at=at("2026-10-22T06:00:00+08:00"),
            active=True,
            created_at=NOW,
        ),
    ]:
        await conn.execute(m.merchant_billing.insert().values(**bill.model_dump(mode="python")))
    for item in [
        ScheduledInstruction(
            id="si_zs_mom",
            customer_id="cus_zs",
            status="ACTIVE",
            from_account_id="acc_zs_chk",
            payee_id="pye_zs_mom",
            to_account_id=None,
            amount_cents=100000,
            period="MONTHLY",
            anchor_day=5,
            weekday=None,
            time_of_day=time(6, 0),
            next_run_at=at("2026-11-05T06:00:00+08:00"),
            end_at=None,
            priority="HIGH",
            grant_scope_hash=GRANT,
            source_plan_id="pln_seed",
            created_at=NOW,
        ),
        MerchantMandate(
            id="si_zs_video",
            customer_id="cus_zs",
            status="ACTIVE",
            from_account_id="acc_zs_chk",
            merchant_id="mer_video",
            billing_id="bill_zs_video",
            max_amount_cents=3600,
            end_at=None,
            priority="HIGH",
            grant_scope_hash=GRANT,
            source_plan_id="pln_seed",
            created_at=NOW,
        ),
        MerchantMandate(
            id="si_zs_music",
            customer_id="cus_zs",
            status="ACTIVE",
            from_account_id="acc_zs_chk",
            merchant_id="mer_music",
            billing_id="bill_zs_music",
            max_amount_cents=1800,
            end_at=None,
            priority="HIGH",
            grant_scope_hash=GRANT,
            source_plan_id="pln_seed",
            created_at=NOW,
        ),
        MerchantMandate(
            id="si_zs_cloud",
            customer_id="cus_zs",
            status="ACTIVE",
            from_account_id="acc_zs_chk",
            merchant_id="mer_cloud",
            billing_id="bill_zs_cloud",
            max_amount_cents=2520,
            end_at=None,
            priority="HIGH",
            grant_scope_hash=GRANT,
            source_plan_id="pln_seed",
            created_at=NOW,
        ),
        MerchantMandate(
            id="si_zs_gym",
            customer_id="cus_zs",
            status="ACTIVE",
            from_account_id="acc_zs_chk",
            merchant_id="mer_gym",
            billing_id="bill_zs_gym",
            max_amount_cents=23880,
            end_at=None,
            priority="HIGH",
            grant_scope_hash=GRANT,
            source_plan_id="pln_seed",
            created_at=NOW,
        ),
        MerchantMandate(
            id="si_ls_video",
            customer_id="cus_ls",
            status="ACTIVE",
            from_account_id="acc_ls_chk",
            merchant_id="mer_video",
            billing_id="bill_ls_video",
            max_amount_cents=3600,
            end_at=None,
            priority="HIGH",
            grant_scope_hash=GRANT,
            source_plan_id="pln_seed",
            created_at=NOW,
        ),
        MerchantMandate(
            id="si_ls_music",
            customer_id="cus_ls",
            status="ACTIVE",
            from_account_id="acc_ls_chk",
            merchant_id="mer_music",
            billing_id="bill_ls_music",
            max_amount_cents=1800,
            end_at=None,
            priority="HIGH",
            grant_scope_hash=GRANT,
            source_plan_id="pln_seed",
            created_at=NOW,
        ),
    ]:
        await conn.execute(
            m.standing_instructions.insert().values(**item.model_dump(mode="python"))
        )
