"""Pure typed-model validation for the raw obligation snapshot (no PostgreSQL).

The F1 review required the ``status=SUCCESS => instruction_id IS NOT NULL``
invariant to be enforced both in the typed model and in the database CHECK.
These tests cover the model half; the migration/PG tests cover the DB half.
"""

from datetime import UTC, datetime, time, timezone

import pytest
from pydantic import ValidationError
from safebank_contracts.obligations import (
    InstructionRun,
    InstructionTarget,
    Merchant,
    MerchantBilling,
    MerchantMandate,
    ObligationSnapshot,
    ScheduledInstruction,
)

GRANT = "0" * 64
TZ = timezone.fromutc


def at(value: str) -> datetime:
    return datetime.fromisoformat(value)


def scheduled(**updates) -> ScheduledInstruction:
    values = dict(
        id="si_mom",
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
        created_at=at("2026-10-15T09:00:00+08:00"),
    )
    values.update(updates)
    return ScheduledInstruction(**values)


def mandate(**updates) -> MerchantMandate:
    values = dict(
        id="si_video",
        customer_id="cus_zs",
        status="ACTIVE",
        from_account_id="acc_zs_chk",
        merchant_id="mer_video",
        billing_id="bill_video",
        max_amount_cents=3600,
        end_at=None,
        priority="HIGH",
        grant_scope_hash=GRANT,
        source_plan_id="pln_seed",
        created_at=at("2026-10-15T09:00:00+08:00"),
    )
    values.update(updates)
    return MerchantMandate(**values)


def billing(**updates) -> MerchantBilling:
    values = dict(
        id="bill_video",
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
        created_at=at("2026-10-15T09:00:00+08:00"),
    )
    values.update(updates)
    return MerchantBilling(**values)


def merchant(**updates) -> Merchant:
    values = dict(
        id="mer_video", name="爱看视频", mcc="5816", category="娱乐", usage_expected=False
    )
    values.update(updates)
    return Merchant(**values)


def run(**updates) -> InstructionRun:
    values = dict(
        id="run_video_1",
        customer_id="cus_zs",
        account_id="acc_zs_chk",
        kind="MERCHANT_MANDATE",
        instruction_id="si_video",
        billing_id="bill_video",
        due_at=at("2026-10-18T06:00:00+08:00"),
        event_id="evt_video_1",
        status="SUCCESS",
        code=None,
        amount_cents=3000,
        transfer_id="trf_video_1",
    )
    values.update(updates)
    return InstructionRun(**values)


def test_scheduled_instruction_rejects_both_or_neither_target():
    with pytest.raises(ValidationError):
        scheduled(payee_id=None, to_account_id=None)
    with pytest.raises(ValidationError):
        scheduled(to_account_id="acc_zs_sav")


def test_scheduled_instruction_rejects_source_equals_target():
    with pytest.raises(ValidationError):
        scheduled(payee_id=None, to_account_id="acc_zs_chk")


def test_scheduled_instruction_rejects_inconsistent_period_anchors():
    with pytest.raises(ValidationError):
        scheduled(period="MONTHLY", anchor_day=None)
    with pytest.raises(ValidationError):
        scheduled(period="WEEKLY", anchor_day=5)
    with pytest.raises(ValidationError):
        scheduled(period="ONCE", anchor_day=5)


def test_active_scheduled_requires_next_run_at():
    with pytest.raises(ValidationError):
        scheduled(next_run_at=None)


def test_billing_rejects_period_anchor_mismatch():
    with pytest.raises(ValidationError):
        billing(period="WEEKLY", anchor_day=18, weekday=None)
    with pytest.raises(ValidationError):
        billing(period="MONTHLY", anchor_day=None)
    with pytest.raises(ValidationError):
        billing(period="MONTHLY", weekday=3)


def test_billing_rejects_local_time_with_timezone():
    with pytest.raises(ValidationError):
        billing(time_of_day=time(6, 0, tzinfo=UTC))


def test_merchant_mandate_never_carries_period_fields():
    with pytest.raises(ValidationError):
        MerchantMandate(**{**mandate().model_dump(), "period": "MONTHLY"})


@pytest.mark.parametrize("status", ["BLOCKED", "COMPLETED", "FAILED"])
def test_instruction_status_rejects_run_statuses(status):
    with pytest.raises(ValidationError):
        scheduled(status=status)
    with pytest.raises(ValidationError):
        mandate(status=status)


def test_run_success_without_instruction_is_rejected():
    with pytest.raises(ValidationError, match="SUCCESS"):
        run(instruction_id=None)


def test_run_blocked_without_instruction_is_accepted():
    accepted = run(
        instruction_id=None, status="BLOCKED", code="MANDATE_NOT_ACTIVE", transfer_id=None
    )
    assert accepted.status == "BLOCKED"


def test_run_success_requires_transfer_and_no_code():
    with pytest.raises(ValidationError):
        run(transfer_id=None)
    with pytest.raises(ValidationError):
        run(code="SOME_CODE")


def test_run_non_success_rejects_transfer_and_requires_code():
    with pytest.raises(ValidationError):
        run(status="BLOCKED", transfer_id="trf_video_1", code="MANDATE_NOT_ACTIVE")
    with pytest.raises(ValidationError):
        run(status="BLOCKED", code=None, transfer_id=None)


def test_scheduled_run_requires_instruction_and_no_billing():
    with pytest.raises(ValidationError):
        run(kind="SCHEDULED_TRANSFER", billing_id="bill_video")
    with pytest.raises(ValidationError):
        run(kind="SCHEDULED_TRANSFER", instruction_id=None, billing_id=None)


def test_target_ownership_rules():
    assert InstructionTarget(
        instruction_id="si_1",
        source_customer_id="cus_zs",
        ownership="EXTERNAL",
        target_account_id=None,
        target_customer_id=None,
    )
    assert InstructionTarget(
        instruction_id="si_1",
        source_customer_id="cus_zs",
        ownership="OWN",
        target_account_id="acc_zs_sav",
        target_customer_id="cus_zs",
    )
    with pytest.raises(ValidationError):
        InstructionTarget(
            instruction_id="si_1",
            source_customer_id="cus_zs",
            ownership="OWN",
            target_account_id="acc_ww_chk",
            target_customer_id="cus_ww",
        )
    with pytest.raises(ValidationError):
        InstructionTarget(
            instruction_id="si_1",
            source_customer_id="cus_zs",
            ownership="OTHER",
            target_account_id="acc_zs_sav",
            target_customer_id="cus_zs",
        )
    with pytest.raises(ValidationError):
        InstructionTarget(
            instruction_id="si_1",
            source_customer_id="cus_zs",
            ownership="EXTERNAL",
            target_account_id="acc_ww_chk",
            target_customer_id="cus_ww",
        )


def test_snapshot_covered_ids_must_be_unique_and_sorted():
    base = dict(
        covered_customer_ids=("cus_zs",),
        merchants={},
        billings={},
        standing_instructions={},
        runs=(),
        instruction_targets={},
    )
    assert ObligationSnapshot(**base)
    with pytest.raises(ValidationError):
        ObligationSnapshot(**{**base, "covered_customer_ids": ()})
    with pytest.raises(ValidationError):
        ObligationSnapshot(**{**base, "covered_customer_ids": ("cus_zs", "cus_zs")})
    with pytest.raises(ValidationError):
        ObligationSnapshot(**{**base, "covered_customer_ids": ("cus_zs", "cus_ww")})


def test_grant_scope_hash_must_be_64_hex():
    with pytest.raises(ValidationError):
        scheduled(grant_scope_hash="not-a-hash")
    with pytest.raises(ValidationError):
        mandate(grant_scope_hash="")
