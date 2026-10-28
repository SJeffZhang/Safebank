"""Deterministic standing instruction / mandate lifecycle (PRD §5.1 T-3/S-4, §7.7, §8.2).

No IO, database, clock or authorization. Returns new :class:`BankState` copies
with updated ``obligations`` snapshots. Every effect is an immutable record;
persist, migration, scheduler and HTTP routes are not part of this module.

Effect types (``InsertInstruction``, ``SetInstructionStatus``,
``ScheduleEventJob``) are now defined in ``safebank_contracts.state`` as
proper ``Contract`` subclasses so they can travel through the LedgerEffect
discriminated union for persistence.

Reference implementations
    - ``reserve.py``: BUSINESS_TIMEZONE, _business_time, _next_month, _recurrences,
      _check_calendar, scheduled_occurrences, projected_shortfall
    - ``banking.py``: apply_transfer / apply_card patterns (return triple)
    - ``obligations.py``: ScheduledInstruction, MerchantMandate, InstructionTarget,
      ObligationSnapshot, MerchantBilling

Ownership rules (PRD §7.7, §8.2, §9.4)
    - OWN: to_account_id belongs to the same customer
    - OTHER: payee points to a local account of a different customer
    - EXTERNAL: payee points to an external clearing account

commitment_30d_cents
    Uses ``_recurrences`` to count occurrences in ``(now, now+30d]`` from
    the first_run_at (inclusive start), applying the same end_at cap and
    period/end-of-month calendar as ``projected_shortfall``.
    Result = occurrences * amount_cents.

grant_scope_hash
    Deterministic SHA-256 of the scope-defining fields (customer, source,
    target fingerprint, period, amount, first_run_at, time_of_day). Ensures
    the gateway can verify the grant matches the instruction at event time.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from safebank_contracts.codes import Code
from safebank_contracts.obligations import (
    InstructionTarget,
    MerchantMandate,
    ObligationSnapshot,
    ScheduledInstruction,
)
from safebank_contracts.plan import StepResult, Violation
from safebank_contracts.state import (
    BankState,
    InsertInstruction,
    Operation,
    OperationEvent,
    ScheduleEventJob,
    SetInstructionStatus,
)
from safebank_contracts.tools import Schedule
from safebank_contracts.views import WRITE_RESULTS

from .ids import derive, digest
from .reserve import _business_time, _recurrences

# ---------------------------------------------------------------------------
# Helpers – anchor derivation
# ---------------------------------------------------------------------------


def _derive_anchors(first_run_at: datetime, period: str):
    """Return (anchor_day, weekday, local_time) from the first-run instant.

    All values are computed in Asia/Shanghai.
    """
    bt = _business_time(first_run_at)
    local_time = bt.time().replace(tzinfo=None)
    if period == "ONCE":
        return None, None, local_time
    if period == "WEEKLY":
        return None, bt.weekday(), local_time
    # MONTHLY
    return bt.day, None, local_time


def _target_info(
    state: BankState, customer_id: str, payee_id: str | None, to_account_id: str | None
):
    """Resolve ownership (OWN / OTHER / EXTERNAL) and target ids.

    Returns ``(ownership, target_account_id, target_customer_id, target_fingerprint)``.
    """
    if to_account_id is not None:
        account = state.accounts.get(to_account_id)
        if account is None or account.customer_id != customer_id:
            raise ValueError("to_account must belong to the customer")
        return ("OWN", to_account_id, customer_id, account.id)

    if payee_id is None:
        raise ValueError("either payee_id or to_account_id required")

    payee = state.payees.get(payee_id)
    if not isinstance(payee_id, str) or payee is None or payee.customer_id != customer_id:
        raise ValueError("payee not found or not owned by customer")

    if payee.target_account_id is None:
        return ("EXTERNAL", None, None, payee.target_fingerprint)

    target = state.accounts.get(payee.target_account_id)
    if target is None:
        return ("EXTERNAL", None, None, payee.target_fingerprint)
    if target.customer_id == customer_id:
        return ("OWN", payee.target_account_id, customer_id, payee.target_account_id)
    return ("OTHER", payee.target_account_id, target.customer_id, payee.target_account_id)


def _prep_instruction(
    *,
    args,
    now: datetime,
    plan_id: str,
    step_id: str,
    customer_id: str,
    state: BankState,
) -> tuple[ScheduledInstruction, InstructionTarget, str]:
    """Validate and build a ``ScheduledInstruction`` + ``InstructionTarget``.

    Returns ``(instruction, target, fingerprint)``.  fingerprint is the grant
    scope key (OWN/OTHER = target account id, EXTERNAL = payee fingerprint).
    """
    source = state.accounts.get(args.from_account_id)
    if source is None or source.customer_id != customer_id:
        raise ValueError("source account not found or not owned by customer")

    if args.end_at is not None and args.end_at < args.first_run_at:
        raise ValueError("end_at precedes first_run_at")

    bt = _business_time(args.first_run_at)
    if bt <= _business_time(state.sim_now):
        raise ValueError("first_run_at must be in the future")

    ownership, target_acct, target_cust, target_fp = _target_info(
        state, customer_id, args.payee_id, args.to_account_id
    )

    anchor_day, weekday, local_time = _derive_anchors(args.first_run_at, args.period)

    # Validate calendar consistency using shared _check_calendar path
    from .reserve import _check_calendar

    _check_calendar(
        period=args.period,
        anchor_day=anchor_day,
        weekday=weekday,
        local_time=local_time,
        next_run=bt,
    )

    inst_id = derive(plan_id, step_id, "si")
    grant_hash = _grant_scope_hash(
        inst_id=inst_id,
        customer_id=customer_id,
        kind="SCHEDULED_TRANSFER",
        from_account_id=args.from_account_id,
        ownership=ownership,
        target_fp=target_fp,
        period=args.period,
        time_of_day_val=local_time,
        amount_cents=args.amount_cents,
        first_run_at=bt.isoformat(),
    )
    inst = ScheduledInstruction(
        id=inst_id,
        customer_id=customer_id,
        kind="SCHEDULED_TRANSFER",
        amount_type="FIXED",
        status="ACTIVE",
        from_account_id=args.from_account_id,
        payee_id=args.payee_id if isinstance(args.payee_id, str) else None,
        to_account_id=args.to_account_id,
        amount_cents=args.amount_cents,
        recurrence="PERIODIC",
        period=args.period,
        anchor_day=anchor_day,
        weekday=weekday,
        time_of_day=local_time,
        next_run_at=args.first_run_at,
        end_at=args.end_at,
        priority="HIGH",
        grant_scope_hash=grant_hash,
        source_plan_id=plan_id,
        created_at=now,
    )

    target = InstructionTarget(
        instruction_id=inst.id,
        source_customer_id=customer_id,
        ownership=ownership,
        target_account_id=target_acct,
        target_customer_id=target_cust,
    )
    return inst, target, target_fp


# ---------------------------------------------------------------------------
# grant scope hash
# ---------------------------------------------------------------------------


def _grant_scope_hash(
    inst_id: str,
    customer_id: str,
    kind: str,
    from_account_id: str,
    ownership: str,
    target_fp: str,
    period: str,
    time_of_day_val,
    amount_cents: int,
    first_run_at: str,
) -> str:
    """Deterministic SHA-256 hash covering scope-defining fields.

    Changing any of these fields (or the instruction id) invalidates the grant
    so a stale pre-authorisation cannot be replayed against a modified instruction.
    """
    payload = "‖".join(
        (
            inst_id,
            customer_id,
            kind,
            from_account_id,
            ownership,
            target_fp,
            period,
            str(time_of_day_val),
            str(amount_cents),
            first_run_at,
        )
    )
    return hashlib.sha256(payload.encode()).hexdigest()


# ---------------------------------------------------------------------------
# commitment_30d_cents  (PRD §7.7, §9.4 W3 result)
# ---------------------------------------------------------------------------


def _first_job_due(inst: ScheduledInstruction) -> datetime:
    """Return the ``due_at`` of the first ``ScheduleEventJob``.

    This is the first_run_at; the scheduler may adjust it when the job is
    actually picked up, but the *event* due time is fixed.
    """
    if inst.next_run_at is None:
        raise ValueError("ACTIVE instruction must have next_run_at")
    return _business_time(inst.next_run_at)


def _commitment_30d(
    inst: ScheduledInstruction,
    sim_now: datetime,
) -> int:
    """Number of occurrences in ``(sim_now, sim_now+30d]`` × amount_cents."""
    if inst.status != "ACTIVE":
        return 0
    start = _business_time(sim_now)
    end = start + timedelta(days=30)
    occurrences = _recurrences(
        period=inst.period,
        anchor_day=inst.anchor_day,
        weekday=inst.weekday,
        local_time=inst.time_of_day,
        next_at=inst.next_run_at,
        end_at=inst.end_at,
        start=start,
        end=end,
    )
    return len(occurrences) * inst.amount_cents


def commitment_30d_cents(inst: ScheduledInstruction, sim_now: datetime) -> int:
    """Public result-shaping wrapper over :func:`_commitment_30d`."""
    return _commitment_30d(inst, sim_now)


# ---------------------------------------------------------------------------
# _make_fresh_snapshot  – add / update instructions in the obligation snapshot
# ---------------------------------------------------------------------------


def _make_fresh_snapshot(
    state: BankState,
    inst: ScheduledInstruction | MerchantMandate,
    target: InstructionTarget | None,
) -> BankState:
    """Return a new BankState with *inst* inserted into ``obligations``."""
    snap = state.obligations
    if snap is None:
        raise ValueError("obligations snapshot not loaded")
    instructions = dict(snap.standing_instructions)
    targets = dict(snap.instruction_targets)
    instructions[inst.id] = inst
    if target is not None:
        targets[inst.id] = target
    new_snap = ObligationSnapshot(
        covered_customer_ids=snap.covered_customer_ids,
        merchants=snap.merchants,
        billings=snap.billings,
        standing_instructions=instructions,
        runs=snap.runs,
        instruction_targets=targets,
    )
    return state.model_copy(update={"obligations": new_snap})


def _operation(now: datetime, ctx, kind: str, *, target: str | None = None, amount: int = 0):
    """Build an ``OperationEvent`` effect for an executed standing-instruction write."""
    return OperationEvent(
        operation=Operation(
            event_id=derive(ctx.plan_id, ctx.step_id, "evt"),
            customer_id=ctx.customer_id,
            plan_id=ctx.plan_id,
            step_id=ctx.step_id,
            action_kind=kind,
            target_fingerprint=target,
            amount_cents=amount,
            occurred_at=now,
        )
    )


# ---------------------------------------------------------------------------
# Public apply functions
# ---------------------------------------------------------------------------


def apply_schedule_transfer(state: BankState, args: Schedule, now: datetime, ctx):
    """Create a standing instruction (PRD §9.4 W3).

    Returns ``(new_state, effects, violations)``.  Effects include an
    ``InsertInstruction``, a ``ScheduleEventJob`` for the first execution and an
    ``OperationEvent`` audit row.  ``grant_scope_hash`` and
    ``commitment_30d_cents`` are computed deterministically from the
    instruction and current ``sim_now``.
    """
    inst, target, target_fp = _prep_instruction(
        args=args,
        now=now,
        plan_id=ctx.plan_id,
        step_id=ctx.step_id,
        customer_id=ctx.customer_id,
        state=state,
    )

    # First event job
    due_at = _first_job_due(inst)
    job_id = derive(ctx.plan_id, ctx.step_id, "job")
    event_id = digest(["SCHEDULED_TRANSFER", inst.id, due_at.isoformat()])
    payload_data = f"{inst.id}|{inst.amount_cents}|{inst.from_account_id}"
    job = ScheduleEventJob(
        job_id=job_id,
        instruction_id=inst.id,
        customer_id=ctx.customer_id,
        account_id=inst.from_account_id,
        event_id=event_id,
        kind="SCHEDULED_TRANSFER",
        due_at=due_at,
        source_id=inst.id,
        period_key=due_at.isoformat(),
        payload_ciphertext=payload_data,
        payload_hash=hashlib.sha256(payload_data.encode()).hexdigest(),
    )

    new_state = _make_fresh_snapshot(state, inst, target)

    effects: tuple = (
        InsertInstruction(instruction=inst, target=target),
        job,
        _operation(now, ctx, "SCHEDULED_TRANSFER_CREATE", target=target_fp, amount=inst.amount_cents),
    )
    return new_state, effects, ()


def apply_cancel_scheduled(state: BankState, args, now: datetime, ctx):
    """Cancel a scheduled transfer (PRD §9.4 W4).

    Only ACTIVE ``SCHEDULED_TRANSFER`` instructions owned by the customer may
    be cancelled.  Already-DELETED returns ``no_op=True`` without errors.
    Invalid ``kind`` (MERCHANT_MANDATE) is rejected.
    """
    snap = state.obligations
    if snap is None:
        raise ValueError("obligations snapshot not loaded")

    inst = snap.standing_instructions.get(args.instruction_id)
    if inst is None:
        return (
            state,
            (),
            (Violation(code=Code.NOT_FOUND, step_id=ctx.step_id, message="instruction not found"),),
        )

    if inst.customer_id != ctx.customer_id:
        return (
            state,
            (),
            (
                Violation(
                    code=Code.OWNERSHIP_MISMATCH,
                    step_id=ctx.step_id,
                    message="not your instruction",
                ),
            ),
        )

    if not isinstance(inst, ScheduledInstruction):
        return (
            state,
            (),
            (
                Violation(
                    code=Code.CONTRACT_INVALID,
                    step_id=ctx.step_id,
                    message="not a scheduled transfer",
                ),
            ),
        )

    if inst.status == "DELETED":
        return state, (), ()

    if inst.status != "ACTIVE":
        return (
            state,
            (),
            (
                Violation(
                    code=Code.CONTRACT_INVALID,
                    step_id=ctx.step_id,
                    message=f"cannot cancel {inst.status} instruction",
                ),
            ),
        )

    updated = inst.model_copy(update={"status": "DELETED"})
    new_state = _make_fresh_snapshot(state, updated, None)

    fx: tuple = (
        SetInstructionStatus(instruction_id=inst.id, old_status="ACTIVE", new_status="DELETED"),
        _operation(now, ctx, "SCHEDULED_CANCEL", target=inst.payee_id, amount=inst.amount_cents),
    )
    return new_state, fx, ()


def apply_cancel_subscription(state: BankState, args, now: datetime, ctx):
    """Revoke a merchant mandate (PRD §9.4 W11).

    Only ACTIVE ``MERCHANT_MANDATE`` instructions owned by the customer may be
    revoked.  The associated billing records are *not* deleted – the merchant
    may still issue billing events, which will then be blocked at authorization
    time (``MANDATE_NOT_ACTIVE``).

    Already-DELETED returns ``no_op=True`` with zero savings.  Invalid ``kind``
    (SCHEDULED_TRANSFER) is rejected.
    """
    snap = state.obligations
    if snap is None:
        raise ValueError("obligations snapshot not loaded")

    inst = snap.standing_instructions.get(args.instruction_id)
    if inst is None:
        return (
            state,
            (),
            (Violation(code=Code.NOT_FOUND, step_id=ctx.step_id, message="instruction not found"),),
        )

    if inst.customer_id != ctx.customer_id:
        return (
            state,
            (),
            (
                Violation(
                    code=Code.OWNERSHIP_MISMATCH,
                    step_id=ctx.step_id,
                    message="not your instruction",
                ),
            ),
        )

    if not isinstance(inst, MerchantMandate):
        return (
            state,
            (),
            (
                Violation(
                    code=Code.CONTRACT_INVALID,
                    step_id=ctx.step_id,
                    message="not a merchant mandate",
                ),
            ),
        )

    if inst.status == "DELETED":
        return state, (), ()

    if inst.status != "ACTIVE":
        return (
            state,
            (),
            (
                Violation(
                    code=Code.CONTRACT_INVALID,
                    step_id=ctx.step_id,
                    message=f"cannot revoke {inst.status} mandate",
                ),
            ),
        )

    # Monthly savings = associated billing amount (if active)
    billing = snap.billings.get(inst.billing_id)
    _monthly = billing.amount_cents if (billing and billing.active) else 0

    updated = inst.model_copy(update={"status": "DELETED"})
    new_state = _make_fresh_snapshot(state, updated, None)

    fx: tuple = (
        SetInstructionStatus(instruction_id=inst.id, old_status="ACTIVE", new_status="DELETED"),
        _operation(now, ctx, "MANDATE_REVOKE", target=inst.merchant_id, amount=_monthly),
    )
    return new_state, fx, ()
