from datetime import datetime

from safebank_contracts.state import Account, BankState, Card, Customer, Payee
from safebank_contracts.views import ChannelFlags, ProductView, SubscriptionView

NOW = datetime.fromisoformat("2026-10-15T09:00:00+08:00")


def bank_state():
    return BankState(
        sim_now=NOW,
        clock_version=1,
        versions={"cus_zs": 1},
        accounts={
            a.id: a
            for a in [
                Account(
                    id="acc_zs",
                    customer_id="cus_zs",
                    type="CHECKING",
                    display_name="活期",
                    account_no="6222000000000001",
                    credits_posted_cents=600000,
                ),
                Account(
                    id="acc_save",
                    customer_id="cus_zs",
                    type="SAVINGS",
                    display_name="储蓄",
                    account_no="6222000000000002",
                    credits_posted_cents=2000000,
                ),
            ]
        },
        customers={
            "cus_zs": Customer(
                id="cus_zs",
                display_name="张三",
                phone="",
                risk_level=3,
                risk_valid_until="2027-01-01",
            )
        },
        cards={
            "crd_zs": Card(
                id="crd_zs",
                account_id="acc_zs",
                last4="1234",
                status="ACTIVE",
                single_limit_cents=500000,
                daily_limit_cents=2000000,
                controls=ChannelFlags(online=True, overseas=False, atm=True, quickpass=True),
                created_at=NOW,
            )
        },
        payees={
            p.id: p
            for p in [
                Payee(
                    id="pye_ww",
                    customer_id="cus_zs",
                    name="王五",
                    alias=None,
                    phone=None,
                    bank_name="银行",
                    account_no="6222000000000003",
                    target_account_id=None,
                    target_fingerprint="fp_ww",
                    created_at=NOW,
                ),
                Payee(
                    id="pye_wu",
                    customer_id="cus_zs",
                    name="王武",
                    alias=None,
                    phone=None,
                    bank_name="银行",
                    account_no="6222000000000004",
                    target_account_id=None,
                    target_fingerprint="fp_wu",
                    created_at=NOW,
                ),
                Payee(
                    id="pye_zl",
                    customer_id="cus_zs",
                    name="赵六",
                    alias=None,
                    phone=None,
                    bank_name="银行",
                    account_no="6222000000000005",
                    target_account_id=None,
                    target_fingerprint="fp_zl",
                    created_at=NOW,
                ),
            ]
        },
        subscriptions=(
            SubscriptionView(
                instruction_id="si_gym",
                billing_id="bil_gym",
                account_id="acc_zs",
                merchant_id="mer_gym",
                merchant_name="健身",
                period="MONTHLY",
                amount_cents=19900,
                max_amount_cents=19900,
                next_due_at=NOW,
                status="ACTIVE",
                annual_cost_cents=238800,
            ),
        ),
        products=tuple(
            ProductView(
                id=id_,
                name=name,
                product_type="BOND",
                risk_level=level,
                risk_label=label,
                nav="1.2311",
                nav_date=NOW.date(),
                recent_return_bp=10,
                confirm_days=1,
                settle_days=1,
                min_amount_cents=100,
                risk_disclosure="合成揭示",
                disclosure_version="v1",
                nav_history=(),
            )
            for id_, name, level, label in [
                ("prd_a", "稳盈短债", 2, "稳健"),
                ("prd_b", "稳健增利", 3, "稳健"),
                ("prd_c", "均衡混合", 3, "平衡"),
            ]
        ),
    )
