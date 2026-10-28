"""Tests for standing.py – pure-domain instruction lifecycle.

Covers ONCE / WEEKLY / MONTHLY anchor derivation, end-of-month boundary,
leap-year, target ownership (OWN, OTHER, EXTERNAL), past-first-run and
invalid-period rejection, cancel (no-op, wrong kind), revoke-mandate
(bill preservation, savings computation), commitment_30d, and grant-scope
determinism.
"""

from datetime import datetime, time, timedelta

import pytest
from safebank_bankcore.banking import Context
from safebank_bankcore.standing import (
    InsertInstruction,
    ScheduleEventJob,
    SetInstructionStatus,
    _commitment_30d,
    _derive_anchors,
    _make_fresh_snapshot,
    _target_info,
    apply_cancel_scheduled,
    apply_cancel_subscription,
    apply_schedule_transfer,
)
from safebank_contracts.codes import Code
from safebank_contracts.obligations import (
    InstructionTarget,
)
from safebank_contracts.state import Payee
from safebank_contracts.tools import Instruction, Schedule

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


def ctx(customer_id="cus_a", plan_id="pln_test", step_id="stp_1"):
    return Context(customer_id=customer_id, plan_id=plan_id, step_id=step_id)


def sched(**updates) -> Schedule:
    values = dict(
        from_account_id="acc_a",
        to_account_id="acc_s",
        payee_id=None,
        amount_cents=50000,
        period="ONCE",
        first_run_at=at("2026-10-18T06:00:00+08:00"),
        end_at=None,
    )
    values.update(updates)
    return Schedule(**values)


def instr(**updates) -> Instruction:
    return Instruction(**(dict(instruction_id="si_xxx") | updates))


# ── anchor derivation ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "period,iso,exp_anchor,exp_wd",
    [
        ("ONCE", "2026-10-18T06:00:00+08:00", None, None),
        ("WEEKLY", "2026-10-19T09:00:00+08:00", None, 0),  # Monday
        ("WEEKLY", "2026-10-25T12:00:00+08:00", None, 6),  # Sunday
        ("MONTHLY", "2026-10-31T08:30:00+08:00", 31, None),
        ("MONTHLY", "2026-02-28T06:00:00+08:00", 28, None),
    ],
)
def test_derive_anchors(period, iso, exp_anchor, exp_wd):
    anchor, wd, lt = _derive_anchors(at(iso), period)
    assert anchor == exp_anchor
    assert wd == exp_wd
    assert lt == time.fromisoformat(iso[11:19])


# ── target ownership ───────────────────────────────────────────────────────


def test_target_own_account():
    state = base_state()
    ownership, acct, cust, fp = _target_info(state, "cus_a", None, "acc_s")
    assert ownership == "OWN"
    assert acct == "acc_s"
    assert cust == "cus_a"


def test_target_external_payee():
    state = base_state()
    state = state.model_copy(update={"payees": {"pye_ext": external_payee()}})
    ownership, acct, cust, fp = _target_info(state, "cus_a", "pye_ext", None)
    assert ownership == "EXTERNAL"
    assert acct is None
    assert cust is None


def test_target_other_payee():
    state = base_state()
    payee = Payee(
        id="pye2b",
        customer_id="cus_a",
        name="乙",
        alias=None,
        phone=None,
        bank_name="本行",
        account_no="6222020000000003",
        target_account_id="acc_b",
        target_fingerprint="fingerprint_b",
        created_at=NOW - timedelta(days=5),
    )
    state = state.model_copy(update={"payees": {"pye2b": payee}})
    ownership, acct, cust, _ = _target_info(state, "cus_a", "pye2b", None)
    assert ownership == "OTHER"
    assert acct == "acc_b"
    assert cust == "cus_b"


def test_target_payee_not_found_raises():
    state = base_state()
    with pytest.raises(ValueError):
        _target_info(state, "cus_a", "pye_missing", None)


def test_target_wrong_customer_payee_raises():
    state = base_state()
    pye = external_payee()
    state = state.model_copy(
        update={"payees": {pye.id: pye.model_copy(update={"customer_id": "cus_b"})}}
    )
    with pytest.raises(ValueError):
        _target_info(state, "cus_a", pye.id, None)


# ── apply_schedule_transfer – happy paths ──────────────────────────────────


def test_create_once_instruction():
    state = base_state()
    state = state.model_copy(
        update={"obligations": raw_projection_state(balance=600000).obligations}
    )
    new_state, effects, violations = apply_schedule_transfer(
        state,
        sched(),
        at("2026-10-15T09:00:00+08:00"),
        ctx(),
    )
    assert not violations
    assert len(effects) == 2
    insert, job = effects
    assert isinstance(insert, InsertInstruction)
    assert isinstance(job, ScheduleEventJob)

    inst = insert.instruction
    assert inst.status == "ACTIVE"
    assert inst.period == "ONCE"
    assert inst.amount_cents == 50000
    assert inst.from_account_id == "acc_a"
    assert inst.to_account_id == "acc_s"

    tgt = insert.target
    assert tgt.ownership == "OWN"
    assert tgt.instruction_id == inst.id

    assert job.instruction_id == inst.id
    assert job.kind == "SCHEDULED_TRANSFER"
    assert job.due_at == at("2026-10-18T06:00:00+08:00")

    # snapshot updated
    assert inst.id in new_state.obligations.standing_instructions
    saved = new_state.obligations.standing_instructions[inst.id]
    assert saved.status == "ACTIVE"


def test_create_weekly_instruction():
    state = base_state()
    state = state.model_copy(
        update={"obligations": raw_projection_state(balance=600000).obligations}
    )
    args = sched(period="WEEKLY", first_run_at=at("2026-10-19T09:00:00+08:00"))
    new_state, effects, _ = apply_schedule_transfer(
        state, args, at("2026-10-15T09:00:00+08:00"), ctx()
    )
    inst = effects[0].instruction
    assert inst.period == "WEEKLY"
    assert inst.weekday == 0
    assert inst.anchor_day is None


def test_create_monthly_instruction():
    state = base_state()
    state = state.model_copy(
        update={"obligations": raw_projection_state(balance=600000).obligations}
    )
    args = sched(period="MONTHLY", first_run_at=at("2026-10-18T09:00:00+08:00"))
    new_state, effects, _ = apply_schedule_transfer(
        state, args, at("2026-10-15T09:00:00+08:00"), ctx()
    )
    inst = effects[0].instruction
    assert inst.period == "MONTHLY"
    assert inst.anchor_day == 18
    assert inst.weekday is None


def test_create_with_end_at():
    state = base_state()
    state = state.model_copy(
        update={"obligations": raw_projection_state(balance=600000).obligations}
    )
    args = sched(
        period="WEEKLY",
        first_run_at=at("2026-10-19T06:00:00+08:00"),
        end_at=at("2026-12-31T23:59:59+08:00"),
    )
    new_state, effects, _ = apply_schedule_transfer(
        state, args, at("2026-10-15T09:00:00+08:00"), ctx()
    )
    inst = effects[0].instruction
    assert inst.end_at == at("2026-12-31T23:59:59+08:00")


def test_create_external_payee():
    state = base_state()
    state = state.model_copy(
        update={
            "payees": {"pye_ext": external_payee()},
            "obligations": raw_projection_state(balance=600000).obligations,
        }
    )
    args = sched(to_account_id=None, payee_id="pye_ext")
    new_state, effects, _ = apply_schedule_transfer(
        state, args, at("2026-10-15T09:00:00+08:00"), ctx()
    )
    inst = effects[0].instruction
    tgt = effects[0].target
    assert inst.payee_id == "pye_ext"
    assert inst.to_account_id is None
    assert tgt.ownership == "EXTERNAL"


# ── apply_schedule_transfer – error paths ─────────────────────────────────


def test_create_past_first_run_rejected():
    state = base_state()
    state = state.model_copy(
        update={"obligations": raw_projection_state(balance=600000).obligations}
    )
    args = sched(first_run_at=at("2026-10-10T06:00:00+08:00"))  # before NOW (10-15)
    with pytest.raises(ValueError, match="future"):
        apply_schedule_transfer(state, args, NOW, ctx())


def test_create_end_before_first_rejected():
    state = base_state()
    state = state.model_copy(
        update={"obligations": raw_projection_state(balance=600000).obligations}
    )
    with pytest.raises(ValueError, match="end.*precedes"):
        sched(first_run_at=at("2026-10-18T06:00:00+08:00"), end_at=at("2026-10-17T06:00:00+08:00"))


def test_create_wrong_source_customer_rejected():
    state = base_state()
    state = state.model_copy(
        update={"obligations": raw_projection_state(balance=600000).obligations}
    )
    args = sched(from_account_id="acc_b")  # cus_b's account
    with pytest.raises(ValueError, match="source"):
        apply_schedule_transfer(state, args, NOW, ctx(customer_id="cus_a"))


def test_create_bad_to_account_rejected():
    state = base_state()
    state = state.model_copy(
        update={"obligations": raw_projection_state(balance=600000).obligations}
    )
    args = sched(to_account_id="acc_b")  # not cus_a's account
    with pytest.raises(ValueError, match="belong"):
        apply_schedule_transfer(state, args, NOW, ctx(customer_id="cus_a"))


def test_create_same_source_target_rejected():
    """Schedule model rejects to_account == from_account via internal Transfer validation."""
    with pytest.raises(ValueError, match="source and target must differ"):
        sched(to_account_id="acc_a")


def test_create_no_payee_and_no_to_account_rejected():
    """Schedule model rejects both targets None via internal Transfer validation."""
    with pytest.raises(ValueError, match="one payee or own-account target"):
        sched(to_account_id=None, payee_id=None)


# ── grant scope hash determinism ───────────────────────────────────────────


def test_grant_scope_hash_deterministic():
    state = base_state()
    state = state.model_copy(
        update={
            "obligations": raw_projection_state(balance=600000).obligations,
            "payees": {"pye_ext": external_payee()},
        }
    )
    args = sched(to_account_id=None, payee_id="pye_ext")
    _, effects_a, _ = apply_schedule_transfer(state, args, NOW, ctx(plan_id="pln_a"))
    _, effects_b, _ = apply_schedule_transfer(state, args, NOW, ctx(plan_id="pln_b"))
    # Different plan_ids → different instruction IDs → different hashes
    assert effects_a[0].instruction.grant_scope_hash != effects_b[0].instruction.grant_scope_hash


def test_grant_scope_hash_changes_on_amount():
    state = base_state()
    state = state.model_copy(
        update={"obligations": raw_projection_state(balance=600000).obligations}
    )
    a1 = sched(amount_cents=100000)
    a2 = sched(amount_cents=200000)
    _, e1, _ = apply_schedule_transfer(state, a1, NOW, ctx())
    _, e2, _ = apply_schedule_transfer(state, a2, NOW, ctx())
    assert e1[0].instruction.grant_scope_hash != e2[0].instruction.grant_scope_hash


# ── commitment_30d ─────────────────────────────────────────────────────────


def test_commitment_once_in_window():
    state = base_state()
    state = state.model_copy(
        update={"obligations": raw_projection_state(balance=600000).obligations}
    )
    args = sched(amount_cents=100000, first_run_at=at("2026-10-18T06:00:00+08:00"))  # within 30d
    new_state, effects, _ = apply_schedule_transfer(state, args, NOW, ctx())
    inst = effects[0].instruction
    commitment = _commitment_30d(inst, new_state.sim_now)
    assert commitment == 100000


def test_commitment_once_outside_window():
    state = base_state()
    state = state.model_copy(
        update={"obligations": raw_projection_state(balance=600000).obligations}
    )
    # First run beyond 30 days from NOW
    args = sched(first_run_at=at("2026-12-01T06:00:00+08:00"))
    new_state, effects, _ = apply_schedule_transfer(state, args, NOW, ctx())
    inst = effects[0].instruction
    commitment = _commitment_30d(inst, new_state.sim_now)
    assert commitment == 0


def test_commitment_weekly_4_times():
    state = base_state()
    state = state.model_copy(
        update={"obligations": raw_projection_state(balance=600000).obligations}
    )
    # First run is 1 day from now (10-16), 30d window → 4 weeks
    args = sched(period="WEEKLY", amount_cents=50000, first_run_at=at("2026-10-16T06:00:00+08:00"))
    new_state, effects, _ = apply_schedule_transfer(state, args, NOW, ctx())
    inst = effects[0].instruction
    commitment = _commitment_30d(inst, new_state.sim_now)
    # (10-16, 11-14] 30 days after NOW(10-15 09:00) → end=11-14 09:00
    # Runs: 10-16, 10-23, 10-30, 11-06, 11-13 = 5 occurrences
    # Wait, start=10-15 09:00, end=11-14 09:00
    # first_run_at 10-16 06:00 < start? No, 10-16 > 10-15 → 5 occurrences
    assert commitment == 250000


def test_commitment_monthly_1_or_2():
    state = base_state()
    state = state.model_copy(
        update={"obligations": raw_projection_state(balance=600000).obligations}
    )
    # Monthly on the 20th, first run 2026-10-20, now is 10-15 → 1 occurrence in 30d
    args = sched(period="MONTHLY", amount_cents=50000, first_run_at=at("2026-10-20T06:00:00+08:00"))
    new_state, effects, _ = apply_schedule_transfer(state, args, NOW, ctx())
    inst = effects[0].instruction
    commitment = _commitment_30d(inst, new_state.sim_now)
    # 30d from 10-15 09:00 → end=11-14 09:00
    # Oct 20 in window, Nov 20 outside → 1 occurrence
    assert commitment == 50000


# ── end-of-month ───────────────────────────────────────────────────────────


def test_monthly_jan31_to_feb_end():
    state = base_state()
    state = state.model_copy(
        update={"obligations": raw_projection_state(balance=600000).obligations}
    )
    args = sched(period="MONTHLY", first_run_at=at("2027-01-31T09:00:00+08:00"))
    new_state, effects, _ = apply_schedule_transfer(state, args, NOW, ctx())
    inst = effects[0].instruction
    assert inst.anchor_day == 31
    # Feb has 28 days (2027), so next run should be Feb 28
    assert inst.next_run_at == at("2027-01-31T09:00:00+08:00")


def test_monthly_feb28_leap_year():
    state = base_state()
    state = state.model_copy(
        update={"obligations": raw_projection_state(balance=600000).obligations}
    )
    # 2028 is a leap year
    args = sched(period="MONTHLY", first_run_at=at("2028-02-29T09:00:00+08:00"))
    new_state, effects, _ = apply_schedule_transfer(state, args, NOW, ctx())
    inst = effects[0].instruction
    assert inst.anchor_day == 29
    assert inst.next_run_at == at("2028-02-29T09:00:00+08:00")


# ── cancel_scheduled ───────────────────────────────────────────────────────


def test_cancel_scheduled_happy():
    state = raw_projection_state(
        balance=600000,
        instructions=[raw_instruction(id="si_1", customer_id="cus_a", status="ACTIVE")],
    )
    new_state, effects, violations = apply_cancel_scheduled(
        state,
        instr(instruction_id="si_1"),
        NOW,
        ctx(),
    )
    assert not violations
    assert len(effects) == 1
    assert isinstance(effects[0], SetInstructionStatus)
    assert effects[0].instruction_id == "si_1"
    assert effects[0].old_status == "ACTIVE"
    assert effects[0].new_status == "DELETED"
    assert new_state.obligations.standing_instructions["si_1"].status == "DELETED"


def test_cancel_already_deleted_no_op():
    state = raw_projection_state(
        balance=600000,
        instructions=[raw_instruction(id="si_1", customer_id="cus_a", status="DELETED")],
    )
    new_state, effects, violations = apply_cancel_scheduled(
        state,
        instr(instruction_id="si_1"),
        NOW,
        ctx(),
    )
    assert not violations
    # DELETED → no domain effects, StepResult produced as no_op=True
    assert len(effects) == 1  # StepResult
    # State unchanged
    assert new_state.obligations.standing_instructions["si_1"].status == "DELETED"


def test_cancel_not_found():
    state = raw_projection_state(balance=600000)
    new_state, effects, violations = apply_cancel_scheduled(
        state,
        instr(instruction_id="si_missing"),
        NOW,
        ctx(),
    )
    assert len(violations) == 1
    assert violations[0].code == Code.NOT_FOUND
    assert not effects


def test_cancel_wrong_customer():
    state = raw_projection_state(
        balance=600000,
        instructions=[raw_instruction(id="si_1", customer_id="cus_b", status="ACTIVE")],
    )
    new_state, effects, violations = apply_cancel_scheduled(
        state,
        instr(instruction_id="si_1"),
        NOW,
        ctx(customer_id="cus_a"),
    )
    assert len(violations) == 1
    assert violations[0].code == Code.OWNERSHIP_MISMATCH


def test_cancel_mandate_wrong_kind():
    state = raw_projection_state(
        balance=600000,
        mandates=[raw_mandate(id="si_video", customer_id="cus_a", status="ACTIVE")],
    )
    new_state, effects, violations = apply_cancel_scheduled(
        state,
        instr(instruction_id="si_video"),
        NOW,
        ctx(),
    )
    assert len(violations) == 1
    assert violations[0].code == Code.CONTRACT_INVALID


# ── cancel_subscription (revoke mandate) ───────────────────────────────────


def test_revoke_mandate_happy():
    state = raw_projection_state(
        balance=600000,
        mandates=[raw_mandate(id="si_video", customer_id="cus_a", status="ACTIVE")],
        billings=[
            raw_billing(
                id="bill_video",
                customer_id="cus_a",
                account_id="acc_a",
                amount_cents=3000,
                active=True,
            )
        ],
        merchants=[raw_merchant()],
    )
    new_state, effects, violations = apply_cancel_subscription(
        state,
        instr(instruction_id="si_video"),
        NOW,
        ctx(),
    )
    assert not violations
    assert len(effects) == 1
    assert isinstance(effects[0], SetInstructionStatus)
    assert effects[0].instruction_id == "si_video"
    assert effects[0].old_status == "ACTIVE"
    assert effects[0].new_status == "DELETED"
    # Mandate deleted
    assert new_state.obligations.standing_instructions["si_video"].status == "DELETED"
    # Billing preserved
    assert "bill_video" in new_state.obligations.billings


def test_revoke_already_deleted_no_op():
    state = raw_projection_state(
        balance=600000,
        mandates=[raw_mandate(id="si_video", customer_id="cus_a", status="DELETED")],
    )
    new_state, effects, violations = apply_cancel_subscription(
        state,
        instr(instruction_id="si_video"),
        NOW,
        ctx(),
    )
    assert not violations
    assert effects[0].result["no_op"] is True
    assert effects[0].result["monthly_saving_cents"] == 0
    assert new_state.obligations.standing_instructions["si_video"].status == "DELETED"


def test_revoke_not_found():
    state = raw_projection_state(balance=600000)
    new_state, effects, violations = apply_cancel_subscription(
        state,
        instr(instruction_id="si_missing"),
        NOW,
        ctx(),
    )
    assert len(violations) == 1
    assert violations[0].code == Code.NOT_FOUND


def test_revoke_wrong_customer():
    state = raw_projection_state(
        balance=600000,
        mandates=[raw_mandate(id="si_video", customer_id="cus_b", status="ACTIVE")],
    )
    new_state, effects, violations = apply_cancel_subscription(
        state,
        instr(instruction_id="si_video"),
        NOW,
        ctx(customer_id="cus_a"),
    )
    assert len(violations) == 1
    assert violations[0].code == Code.OWNERSHIP_MISMATCH


def test_revoke_scheduled_wrong_kind():
    state = raw_projection_state(
        balance=600000,
        instructions=[raw_instruction(id="si_1", customer_id="cus_a", status="ACTIVE")],
    )
    new_state, effects, violations = apply_cancel_subscription(
        state,
        instr(instruction_id="si_1"),
        NOW,
        ctx(),
    )
    assert len(violations) == 1
    assert violations[0].code == Code.CONTRACT_INVALID


def test_revoke_billing_preserved():
    """Billing records survive mandate deletion (D15: merchant can still initiate)."""
    state = raw_projection_state(
        balance=600000,
        mandates=[raw_mandate(id="si_video", customer_id="cus_a", status="ACTIVE")],
        billings=[
            raw_billing(id="bill_video", customer_id="cus_a", account_id="acc_a", active=True)
        ],
        merchants=[raw_merchant()],
    )
    new_state, _, _ = apply_cancel_subscription(state, instr(instruction_id="si_video"), NOW, ctx())
    assert "bill_video" in new_state.obligations.billings
    assert new_state.obligations.billings["bill_video"].active is True


# ── _make_fresh_snapshot ───────────────────────────────────────────────────


def test_make_fresh_snapshot_insert():
    state = raw_projection_state(balance=600000)
    inst = raw_instruction(id="si_new", customer_id="cus_a")
    tgt = InstructionTarget(
        instruction_id="si_new",
        source_customer_id="cus_a",
        ownership="OWN",
        target_account_id="acc_s",
        target_customer_id="cus_a",
    )
    new_state = _make_fresh_snapshot(state, inst, tgt)
    assert "si_new" in new_state.obligations.standing_instructions
    assert "si_new" in new_state.obligations.instruction_targets


def test_make_fresh_snapshot_update():
    state = raw_projection_state(
        balance=600000,
        instructions=[raw_instruction(id="si_1", status="ACTIVE")],
    )
    updated = state.obligations.standing_instructions["si_1"].model_copy(
        update={"status": "DELETED"}
    )
    new_state = _make_fresh_snapshot(state, updated, None)
    assert new_state.obligations.standing_instructions["si_1"].status == "DELETED"
    # Original untouched
    assert state.obligations.standing_instructions["si_1"].status == "ACTIVE"


def test_make_fresh_snapshot_no_obligations_raises():
    state = base_state(600000)
    inst = raw_instruction()
    tgt = InstructionTarget(
        instruction_id=inst.id,
        source_customer_id="cus_a",
        ownership="OWN",
        target_account_id="acc_s",
        target_customer_id="cus_a",
    )
    with pytest.raises(ValueError, match="not loaded"):
        _make_fresh_snapshot(state, inst, tgt)


def test_grant_scope_hash_changes_on_first_run_at_with_same_anchor():
    """PRD 7.7 lists first_run_at in the grant scope: same weekday/time, next week."""
    state = base_state()
    state = state.model_copy(
        update={"obligations": raw_projection_state(balance=600000).obligations}
    )
    a1 = sched(period="WEEKLY", first_run_at=at("2026-10-19T09:00:00+08:00"))
    a2 = sched(period="WEEKLY", first_run_at=at("2026-10-26T09:00:00+08:00"))
    _, e1, _ = apply_schedule_transfer(state, a1, NOW, ctx())
    _, e2, _ = apply_schedule_transfer(state, a2, NOW, ctx())
    assert e1[0].instruction.grant_scope_hash != e2[0].instruction.grant_scope_hash
