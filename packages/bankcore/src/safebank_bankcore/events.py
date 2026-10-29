"""Deterministic system-event execution (PRD §7.11, §8.6).

Pure functions executed by the L3 scheduler for due standing instructions and
merchant billings. No IO, database, clock or authorization: each returns a new
:class:`BankState` copy plus effects, exactly like the other bankcore appliers.

Return convention
    ``(new_state, effects, run)`` where ``run`` is the terminal
    :class:`InstructionRun` that must be persisted, or ``None`` when the same
    period was already executed (idempotent replay). Money movement reuses
    :func:`safebank_bankcore.ledger.post_transfer`, so the effects carry
    ``PostTransfer`` / ``AppendTransaction`` records.

Period advance
    The next ``next_run_at`` / ``next_due_at`` is advanced in the returned
    state's ``obligations`` snapshot *in the same execution* (the period is
    consumed once regardless of success or block). The scheduled-transfer
    successor job is emitted as a ``ScheduleEventJob`` effect. The merchant
    billing successor has no effect emitted here: the frozen
    ``ScheduleEventJob.kind`` literal only allows ``MERCHANT_MANDATE`` while the
    persist layer and event_jobs DB CHECK expect ``MERCHANT_CHARGE`` (see
    progress file); the billing's ``next_due_at`` advance in state is the
    authoritative source of the next period.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from safebank_contracts.codes import Code
from safebank_contracts.obligations import (
    InstructionRun,
    MerchantBilling,
    MerchantMandate,
    ObligationSnapshot,
    ScheduledInstruction,
)
from safebank_contracts.state import (
    BankState,
    LedgerTransfer,
    ScheduleEventJob,
    SetInstructionStatus,
)

from .ids import derive, digest
from .ledger import post_transfer
from .reserve import _business_time, _next_month


def _run_exists(
    snap: ObligationSnapshot,
    *,
    instruction_id: str | None = None,
    billing_id: str | None = None,
    due_at: datetime,
) -> bool:
    for run in snap.runs:
        if run.due_at != due_at:
            continue
        if instruction_id is not None and run.instruction_id == instruction_id:
            return True
        if billing_id is not None and run.billing_id == billing_id:
            return True
    return False


def _refresh(
    state: BankState,
    *,
    inst: ScheduledInstruction | MerchantMandate | None = None,
    billing: MerchantBilling | None = None,
    run: InstructionRun | None = None,
) -> BankState:
    """Return a new state with an updated obligation snapshot (additive)."""
    snap = state.obligations
    if snap is None:
        raise ValueError("obligations snapshot not loaded")
    instructions = dict(snap.standing_instructions)
    billings = dict(snap.billings)
    runs = snap.runs
    if inst is not None:
        instructions[inst.id] = inst
    if billing is not None:
        billings[billing.id] = billing
    if run is not None:
        runs = (*runs, run)
    new_snap = ObligationSnapshot(
        covered_customer_ids=snap.covered_customer_ids,
        merchants=snap.merchants,
        billings=billings,
        standing_instructions=instructions,
        runs=runs,
        instruction_targets=snap.instruction_targets,
    )
    return state.model_copy(update={"obligations": new_snap})


def _next_scheduled_due(inst: ScheduledInstruction, due: datetime) -> datetime | None:
    """Next due instant for a continuing scheduled transfer, or None if it ends."""
    if inst.period == "ONCE":
        return None
    if inst.period == "WEEKLY":
        nxt = due + timedelta(days=7)
    else:  # MONTHLY
        nxt = _next_month(due, inst.anchor_day, inst.time_of_day)
    if inst.end_at is not None and nxt > _business_time(inst.end_at):
        return None
    return nxt


def _next_billing_due(billing: MerchantBilling, due: datetime) -> datetime:
    if billing.period == "WEEKLY":
        return due + timedelta(days=7)
    return _next_month(due, billing.anchor_day, billing.time_of_day)


def _next_job(inst: ScheduledInstruction, next_due: datetime) -> ScheduleEventJob:
    payload_data = f"{inst.id}|{inst.amount_cents}|{inst.from_account_id}"
    return ScheduleEventJob(
        job_id=derive(inst.id, next_due.isoformat(), "job"),
        instruction_id=inst.id,
        customer_id=inst.customer_id,
        account_id=inst.from_account_id,
        event_id=digest(["SCHEDULED_TRANSFER", inst.id, next_due.isoformat()]),
        kind="SCHEDULED_TRANSFER",
        due_at=next_due,
        source_id=inst.id,
        period_key=next_due.isoformat(),
        payload_ciphertext=payload_data,
        payload_hash=hashlib.sha256(payload_data.encode()).hexdigest(),
    )


def _status_for(code: Code) -> str:
    return "FAILED_INSUFFICIENT" if code == Code.INSUFFICIENT_FUNDS else "BLOCKED"


def apply_scheduled_transfer(
    state: BankState,
    *,
    instruction_id: str,
    due_at: datetime,
    event_id: str | None = None,
    period_key: str | None = None,
):
    """Execute one due period of a standing instruction (PRD §7.11, §8.6).

    A due ``SCHEDULED_TRANSFER`` instruction is executed against its resolved
    target (OWN/OTHER credited directly, EXTERNAL credited to the clearing
    account). The period is consumed exactly once: the run is recorded and the
    next run is advanced regardless of success, and replaying the same
    ``due_at`` is a no-op.
    """
    snap = state.obligations
    if snap is None:
        raise ValueError("obligations snapshot not loaded")
    inst = snap.standing_instructions.get(instruction_id)
    if inst is None or not isinstance(inst, ScheduledInstruction):
        raise ValueError(f"scheduled instruction not found: {instruction_id}")

    due = _business_time(due_at)
    period = period_key or due.isoformat()
    if _run_exists(snap, instruction_id=inst.id, due_at=due):
        return state, (), None

    if inst.status != "ACTIVE":
        run = InstructionRun(
            id=derive(inst.id, period, "run"),
            customer_id=inst.customer_id,
            account_id=inst.from_account_id,
            kind="SCHEDULED_TRANSFER",
            instruction_id=inst.id,
            billing_id=None,
            due_at=due,
            event_id=event_id or digest(["SCHEDULED_TRANSFER", inst.id, due.isoformat()]),
            status="BLOCKED",
            code=Code.SCHEDULED_BYPASS.value,
            amount_cents=inst.amount_cents,
            transfer_id=None,
        )
        return _refresh(state, run=run), (), run

    target = snap.instruction_targets.get(inst.id)
    if target is None:
        raise ValueError(f"instruction target not resolved: {inst.id}")
    credit = "acc_external_clearing" if target.ownership == "EXTERNAL" else target.target_account_id
    cmd = LedgerTransfer(
        id=derive(inst.id, period, "trf"),
        debit_account_id=inst.from_account_id,
        credit_account_id=credit,
        amount_cents=inst.amount_cents,
        code="SCHEDULED_TRANSFER",
        phase="POSTED",
        pending_id=None,
        plan_id=inst.source_plan_id,
        step_id="event",
        occurred_at=due,
        expires_at=None,
    )
    after, fx, errors = post_transfer(state, cmd, due)

    next_due = _next_scheduled_due(inst, due)
    ends = next_due is None
    updated = inst.model_copy(
        update={"next_run_at": next_due, **({"status": "DELETED"} if ends else {})}
    )
    after = _refresh(after, inst=updated)

    new_fx: list = []
    if ends:
        new_fx.append(
            SetInstructionStatus(instruction_id=inst.id, old_status="ACTIVE", new_status="DELETED")
        )
    else:
        new_fx.append(_next_job(inst, next_due))

    if errors:
        code = errors[0].code
        run = InstructionRun(
            id=derive(inst.id, period, "run"),
            customer_id=inst.customer_id,
            account_id=inst.from_account_id,
            kind="SCHEDULED_TRANSFER",
            instruction_id=inst.id,
            billing_id=None,
            due_at=due,
            event_id=event_id or digest(["SCHEDULED_TRANSFER", inst.id, due.isoformat()]),
            status=_status_for(code),
            code=code.value,
            amount_cents=inst.amount_cents,
            transfer_id=None,
        )
    else:
        run = InstructionRun(
            id=derive(inst.id, period, "run"),
            customer_id=inst.customer_id,
            account_id=inst.from_account_id,
            kind="SCHEDULED_TRANSFER",
            instruction_id=inst.id,
            billing_id=None,
            due_at=due,
            event_id=event_id or digest(["SCHEDULED_TRANSFER", inst.id, due.isoformat()]),
            status="SUCCESS",
            code=None,
            amount_cents=inst.amount_cents,
            transfer_id=cmd.id,
        )
    after = _refresh(after, run=run)
    return after, (*fx, *new_fx), run


def apply_merchant_charge(
    state: BankState,
    *,
    billing_id: str,
    due_at: datetime,
    event_id: str | None = None,
    period_key: str | None = None,
):
    """Charge one due merchant billing via its mandate (PRD §7.11, §8.6).

    The billing is the source of the period and amount; the mandate only
    authorizes it. A missing/inactive mandate or an amount above the mandate's
    ``max_amount_cents`` blocks the charge without moving money, while the
    billing's ``next_due_at`` still advances (merchant billings are independent
    of the mandate's lifecycle).
    """
    snap = state.obligations
    if snap is None:
        raise ValueError("obligations snapshot not loaded")
    billing = snap.billings.get(billing_id)
    if billing is None:
        raise ValueError(f"billing not found: {billing_id}")

    due = _business_time(due_at)
    period = period_key or due.isoformat()
    if _run_exists(snap, billing_id=billing.id, due_at=due):
        return state, (), None

    mandate = next(
        (
            m
            for m in snap.standing_instructions.values()
            if isinstance(m, MerchantMandate) and m.billing_id == billing.id
        ),
        None,
    )

    run_kwargs = dict(
        id=derive(billing.id, period, "run"),
        customer_id=billing.customer_id,
        account_id=billing.account_id,
        kind="MERCHANT_MANDATE",
        instruction_id=mandate.id if mandate is not None else None,
        billing_id=billing.id,
        due_at=due,
        event_id=event_id or digest(["MERCHANT_CHARGE", billing.id, due.isoformat()]),
        amount_cents=billing.amount_cents,
    )

    block_code = None
    if mandate is None or mandate.status != "ACTIVE":
        block_code = Code.MANDATE_NOT_ACTIVE
    elif billing.amount_cents > mandate.max_amount_cents:
        block_code = Code.MANDATE_AMOUNT_EXCEEDED

    next_due = _next_billing_due(billing, due)
    updated_billing = billing.model_copy(update={"next_due_at": next_due})

    if block_code is not None:
        run = InstructionRun(**run_kwargs, status="BLOCKED", code=block_code.value, transfer_id=None)
        return _refresh(_refresh(state, billing=updated_billing), run=run), (), run

    cmd = LedgerTransfer(
        id=derive(billing.id, period, "trf"),
        debit_account_id=billing.account_id,
        credit_account_id="acc_external_clearing",
        amount_cents=billing.amount_cents,
        code="MERCHANT_CHARGE",
        phase="POSTED",
        pending_id=None,
        plan_id="system",
        step_id="event",
        occurred_at=due,
        expires_at=None,
    )
    after, fx, errors = post_transfer(state, cmd, due)
    if errors:
        code = errors[0].code
        run = InstructionRun(**run_kwargs, status=_status_for(code), code=code.value, transfer_id=None)
    else:
        run = InstructionRun(**run_kwargs, status="SUCCESS", code=None, transfer_id=cmd.id)
    after = _refresh(after, billing=updated_billing)
    after = _refresh(after, run=run)
    return after, fx, run
