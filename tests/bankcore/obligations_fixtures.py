"""Raw obligation-snapshot builders for bankcore reserve tests.

The production ``projected_shortfall`` consumes ``BankState.obligations`` (an
``ObligationSnapshot``), never the legacy ``instructions``/``subscriptions``
views. These helpers construct complete raw snapshots so the S2 report tests
prove their inputs explicitly instead of relying on default empty tuples.
"""

from datetime import datetime, time, timedelta

from safebank_contracts.obligations import (
    InstructionTarget,
    Merchant,
    MerchantBilling,
    MerchantMandate,
    ObligationSnapshot,
    ScheduledInstruction,
)
from safebank_contracts.state import Payee

from tests.bankcore.fixtures import NOW, base_state

GRANT = "0" * 64


def at(value: str) -> datetime:
    return datetime.fromisoformat(value)


def raw_instruction(**updates) -> ScheduledInstruction:
    values = dict(
        id="si_1",
        customer_id="cus_a",
        status="ACTIVE",
        from_account_id="acc_a",
        payee_id="pye_external",
        to_account_id=None,
        amount_cents=1000,
        period="ONCE",
        anchor_day=None,
        weekday=None,
        time_of_day=time(6, 0),
        next_run_at=at("2026-10-18T06:00:00+08:00"),
        end_at=None,
        priority="HIGH",
        grant_scope_hash=GRANT,
        source_plan_id="pln_seed",
        created_at=NOW,
    )
    values.update(updates)
    return ScheduledInstruction(**values)


def raw_mandate(**updates) -> MerchantMandate:
    values = dict(
        id="si_video",
        customer_id="cus_a",
        status="ACTIVE",
        from_account_id="acc_a",
        merchant_id="mer_video",
        billing_id="bill_video",
        max_amount_cents=3600,
        end_at=None,
        priority="HIGH",
        grant_scope_hash=GRANT,
        source_plan_id="pln_seed",
        created_at=NOW,
    )
    values.update(updates)
    return MerchantMandate(**values)


def raw_billing(**updates) -> MerchantBilling:
    values = dict(
        id="bill_video",
        customer_id="cus_a",
        account_id="acc_a",
        merchant_id="mer_video",
        amount_cents=3000,
        period="MONTHLY",
        anchor_day=18,
        weekday=None,
        time_of_day=time(6, 0),
        next_due_at=at("2026-10-18T06:00:00+08:00"),
        active=True,
        created_at=NOW,
    )
    values.update(updates)
    return MerchantBilling(**values)


def raw_merchant(**updates) -> Merchant:
    values = dict(
        id="mer_video", name="爱看视频", mcc="5816", category="娱乐", usage_expected=False
    )
    values.update(updates)
    return Merchant(**values)


def external_payee() -> Payee:
    return Payee(
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


def raw_projection_state(
    *,
    balance: int = 600000,
    instructions=(),
    mandates=(),
    billings=(),
    merchants=(),
    covered=None,
    payees=None,
    targets=None,
):
    """Build a BankState whose ``obligations`` snapshot is explicitly complete."""
    state = base_state(balance)
    payee_list = payees if payees is not None else [external_payee()]
    payees_map = {p.id: p for p in payee_list}
    state = state.model_copy(update={"payees": payees_map})

    override = {t.instruction_id: t for t in (targets or ())}
    derived: dict[str, InstructionTarget] = {}
    for item in instructions:
        if item.id in override:
            continue
        if item.to_account_id is not None:
            owner = state.accounts[item.to_account_id].customer_id
            derived[item.id] = InstructionTarget(
                instruction_id=item.id,
                source_customer_id=item.customer_id,
                ownership="OWN",
                target_account_id=item.to_account_id,
                target_customer_id=owner,
            )
            continue
        payee = payees_map[item.payee_id]
        if payee.target_account_id is None:
            derived[item.id] = InstructionTarget(
                instruction_id=item.id,
                source_customer_id=item.customer_id,
                ownership="EXTERNAL",
                target_account_id=None,
                target_customer_id=None,
            )
        else:
            peer = state.accounts[payee.target_account_id].customer_id
            derived[item.id] = InstructionTarget(
                instruction_id=item.id,
                source_customer_id=item.customer_id,
                ownership="OTHER",
                target_account_id=payee.target_account_id,
                target_customer_id=peer,
            )
    derived.update(override)

    snapshot = ObligationSnapshot(
        covered_customer_ids=covered or tuple(sorted(state.versions)),
        merchants={m.id: m for m in merchants},
        billings={b.id: b for b in billings},
        standing_instructions={i.id: i for i in (*instructions, *mandates)},
        runs=(),
        instruction_targets=derived,
    )
    return state.model_copy(update={"obligations": snapshot})
