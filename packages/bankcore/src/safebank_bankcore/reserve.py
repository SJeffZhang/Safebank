"""Deterministic obligation projection; callers must supply complete obligation data.

Windows are left open and right closed. Aware timestamps are normalized to
Asia/Shanghai before calendar validation and recurrence. Scheduled recurrence
uses the original monthly anchor and Shanghai local execution time; weekday
follows datetime.weekday(): Monday=0 through Sunday=6. No wall clock, IO, or
authorization decision is used.

Since S3 the source of truth is the raw ``BankState.obligations`` snapshot
(``ObligationSnapshot``); the legacy ``instructions``/``subscriptions`` views are
derived projections and never override the raw snapshot.
"""

from calendar import monthrange
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from types import MappingProxyType
from zoneinfo import ZoneInfo

from safebank_contracts.obligations import (
    MerchantBilling,
    MerchantMandate,
    ObligationSnapshot,
    ScheduledInstruction,
)
from safebank_contracts.state import Account, BankState
from safebank_contracts.views import ScheduledView

BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True, slots=True)
class Obligation:
    due_at: datetime
    rank: int
    id: str
    account_id: str
    delta_cents: int


@dataclass(frozen=True, slots=True)
class AccountProjection:
    reserve_cents: int
    shortfall_cents: int
    max_outflow_cents: int


@dataclass(frozen=True, slots=True)
class ShortfallReport:
    accounts: Mapping[str, AccountProjection]
    total_shortfall_cents: int
    total_reserve_cents: int
    obligations: tuple[Obligation, ...]


def _business_time(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("obligation timestamps must include a timezone")
    return value.astimezone(BUSINESS_TIMEZONE)


def _next_month(due_at: datetime, anchor_day: int, local_time: time) -> datetime:
    year = due_at.year + due_at.month // 12
    month = due_at.month % 12 + 1
    day = min(anchor_day, monthrange(year, month)[1])
    return datetime.combine(
        due_at.date().replace(year=year, month=month, day=day), local_time, tzinfo=due_at.tzinfo
    )


def _check_calendar(
    *,
    period: str,
    anchor_day: int | None,
    weekday: int | None,
    local_time: time,
    next_run: datetime,
) -> None:
    if next_run.time() != local_time:
        raise ValueError("next_run_at disagrees with the local execution time")
    if period == "WEEKLY":
        if next_run.weekday() != weekday:
            raise ValueError("next_run_at weekday disagrees with weekday")
    elif period == "MONTHLY":
        if anchor_day is None or next_run.day != min(
            anchor_day, monthrange(next_run.year, next_run.month)[1]
        ):
            raise ValueError("next_run_at disagrees with anchor_day")


def _recurrences(
    *,
    period: str,
    anchor_day: int | None,
    weekday: int | None,
    local_time: time,
    next_at: datetime,
    end_at: datetime | None,
    start: datetime,
    end: datetime,
) -> tuple[datetime, ...]:
    """Single private recurrence core shared by views and raw obligations.

    Yields due instants in ``(start, end]`` starting from ``next_at``, honoring an
    inclusive ``end_at`` when supplied. Callers validate anchors via
    ``_check_calendar`` first.
    """
    due_at = _business_time(next_at)
    cutoff = min(end, _business_time(end_at)) if end_at is not None else end
    result: list[datetime] = []
    while due_at <= cutoff:
        if due_at > start:
            result.append(due_at)
        if period == "ONCE":
            break
        if period == "WEEKLY":
            due_at += timedelta(days=7)
            continue
        assert anchor_day is not None
        due_at = _next_month(due_at, anchor_day, local_time)
    return tuple(result)


def _schedule_time(item: ScheduledView) -> time:
    if item.next_run_at is None:
        raise ValueError(f"ACTIVE instruction {item.id} has no next_run_at")
    next_run = _business_time(item.next_run_at)
    if item.amount_cents <= 0:
        raise ValueError(f"instruction {item.id} must have a positive amount")
    if (item.payee_id is None) == (item.to_account_id is None):
        raise ValueError(f"instruction {item.id} must have exactly one target")
    if item.to_account_id == item.from_account_id:
        raise ValueError(f"instruction {item.id} source equals target")
    local_time = time.fromisoformat(item.time_of_day)
    if local_time.tzinfo is not None:
        raise ValueError(f"instruction {item.id} has a timezone in time_of_day")
    if item.period == "ONCE":
        if item.anchor_day is not None or item.weekday is not None:
            raise ValueError(f"ONCE instruction {item.id} cannot have recurring anchors")
    elif item.period == "WEEKLY":
        if (
            type(item.weekday) is not int
            or not 0 <= item.weekday <= 6
            or item.anchor_day is not None
        ):
            raise ValueError(f"WEEKLY instruction {item.id} has invalid anchors")
    elif item.period == "MONTHLY":
        if (
            type(item.anchor_day) is not int
            or not 1 <= item.anchor_day <= 31
            or item.weekday is not None
        ):
            raise ValueError(f"MONTHLY instruction {item.id} has invalid anchors")
    else:
        raise ValueError(f"unsupported period for instruction {item.id}")
    _check_calendar(
        period=item.period,
        anchor_day=item.anchor_day,
        weekday=item.weekday,
        local_time=local_time,
        next_run=next_run,
    )
    return local_time


def scheduled_occurrences(
    instructions: Iterable[ScheduledView], start: datetime, end: datetime
) -> tuple[Obligation, ...]:
    """Expand ACTIVE scheduled debits in (start,end], including exact end_at.

    Calendar fields and recurrence use Asia/Shanghai regardless of input offset;
    returned due_at values use that business timezone. Uses original monthly
    anchor_day. weekday uses Monday=0..Sunday=6.
    Ownership and own-account credits require BankState and are handled by
    projected_shortfall; this expansion also supports independent 30-day callers.
    """
    start = _business_time(start)
    end = _business_time(end)
    if end < start:
        raise ValueError("projection end precedes start")
    result: list[Obligation] = []
    ids: set[str] = set()
    for item in instructions:
        if item.id in ids:
            raise ValueError(f"duplicate instruction {item.id}")
        ids.add(item.id)
        if item.status != "ACTIVE":
            continue
        local_time = _schedule_time(item)
        assert item.next_run_at is not None  # ACTIVE cannot silently lose obligations
        for due_at in _recurrences(
            period=item.period,
            anchor_day=item.anchor_day,
            weekday=item.weekday,
            local_time=local_time,
            next_at=item.next_run_at,
            end_at=item.end_at,
            start=start,
            end=end,
        ):
            result.append(Obligation(due_at, 3, item.id, item.from_account_id, -item.amount_cents))
    return tuple(sorted(result, key=lambda item: (item.due_at, item.rank, item.id)))


def _customer_account(state: BankState, account_id: str) -> Account:
    account = state.accounts.get(account_id)
    if (
        account is None
        or account.id != account_id
        or account.customer_id is None
        or account.customer_id not in state.customers
    ):
        raise ValueError(f"unknown customer ownership for account {account_id}")
    return account


def _validate_scheduled_raw(state: BankState, item: ScheduledInstruction) -> None:
    source = _customer_account(state, item.from_account_id)
    if source.customer_id != item.customer_id:
        raise ValueError(f"instruction {item.id} source does not belong to its customer")
    if item.next_run_at is None:
        raise ValueError(f"ACTIVE instruction {item.id} has no next_run_at")
    next_run = _business_time(item.next_run_at)
    _check_calendar(
        period=item.period,
        anchor_day=item.anchor_day,
        weekday=item.weekday,
        local_time=item.time_of_day,
        next_run=next_run,
    )


def _validate_mandate_billing(
    state: BankState, item: MerchantMandate, snapshot: ObligationSnapshot
) -> MerchantBilling:
    billing = snapshot.billings.get(item.billing_id)
    if billing is None or billing.id != item.billing_id:
        raise ValueError(f"mandate {item.id} references an unknown billing")
    if billing.customer_id != item.customer_id:
        raise ValueError(f"mandate {item.id} billing belongs to a different customer")
    if billing.merchant_id != item.merchant_id:
        raise ValueError(f"mandate {item.id} merchant disagrees with its billing")
    if billing.account_id != item.from_account_id:
        raise ValueError(f"mandate {item.id} billing account disagrees with its source")
    _customer_account(state, billing.account_id)
    next_run = _business_time(billing.next_due_at)
    _check_calendar(
        period=billing.period,
        anchor_day=billing.anchor_day,
        weekday=billing.weekday,
        local_time=billing.time_of_day,
        next_run=next_run,
    )
    return billing


def _raw_obligations(
    state: BankState, snapshot: ObligationSnapshot, start: datetime, end: datetime
) -> list[Obligation]:
    result: list[Obligation] = []
    billing_ids: set[str] = set()
    for item in snapshot.standing_instructions.values():
        if item.status != "ACTIVE":
            continue
        if isinstance(item, ScheduledInstruction):
            _validate_scheduled_raw(state, item)
            target = snapshot.instruction_targets.get(item.id)
            if target is None or target.instruction_id != item.id:
                raise ValueError(f"instruction {item.id} is missing its ownership target")
            if target.source_customer_id != item.customer_id:
                raise ValueError(f"instruction {item.id} target owner disagrees")
            dues = _recurrences(
                period=item.period,
                anchor_day=item.anchor_day,
                weekday=item.weekday,
                local_time=item.time_of_day,
                next_at=item.next_run_at,
                end_at=item.end_at,
                start=start,
                end=end,
            )
            for due_at in dues:
                result.append(
                    Obligation(due_at, 3, item.id, item.from_account_id, -item.amount_cents)
                )
                if target.ownership == "OWN":
                    credit = _customer_account(state, target.target_account_id)
                    if credit.customer_id != item.customer_id:
                        raise ValueError(
                            f"instruction {item.id} OWN target belongs to another customer"
                        )
                    if credit.id == item.from_account_id:
                        raise ValueError(f"instruction {item.id} OWN target equals its source")
                    result.append(Obligation(due_at, 3, item.id, credit.id, item.amount_cents))
            continue
        billing = _validate_mandate_billing(state, item, snapshot)
        if billing.id in billing_ids:
            raise ValueError(f"duplicate merchant billing identity {billing.id}")
        billing_ids.add(billing.id)
        if not billing.active:
            continue
        dues = _recurrences(
            period=billing.period,
            anchor_day=billing.anchor_day,
            weekday=billing.weekday,
            local_time=billing.time_of_day,
            next_at=billing.next_due_at,
            end_at=item.end_at,
            start=start,
            end=end,
        )
        for due_at in dues:
            result.append(
                Obligation(due_at, 4, billing.id, billing.account_id, -billing.amount_cents)
            )
    return result


def projected_shortfall(state: BankState, *, days: int = 7) -> ShortfallReport:
    """Compute per-account prefix reserves from a complete seven-day BankState.

    The raw ``state.obligations`` snapshot is the only source of obligations: a
    null snapshot (not loaded) or a snapshot whose covered customers differ from
    ``state.versions`` is rejected, never silently treated as empty. Own-account
    scheduled transfers credit the target; cross-customer transfers, wages, fund
    settlements and pending income provide no guaranteed income.
    The window and returned due_at values use Asia/Shanghai; any equivalent
    aware input offset is accepted and preserves the same business calendar.
    max_outflow_cents remains negative when obligations exceed available funds.
    No L2 policy violation or authorization decision is returned here.
    """
    if type(days) is not int or days != 7:
        raise ValueError("projected_shortfall supports only integer days=7")
    snapshot = state.obligations
    if snapshot is None:
        raise ValueError("obligations were not loaded; cannot project reserves")
    if set(snapshot.covered_customer_ids) != set(state.versions):
        raise ValueError("obligation coverage does not match state versions")
    start = _business_time(state.sim_now)
    end = start + timedelta(days=days)
    obligations = _raw_obligations(state, snapshot, start, end)
    obligations.sort(key=lambda item: (item.due_at, item.rank, item.id))

    available = {
        account_id: _customer_account(state, account_id).available_cents
        for account_id, account in sorted(state.accounts.items())
        if account.customer_id is not None
    }
    prefixes = dict.fromkeys(available, 0)
    reserves = dict.fromkeys(available, 0)
    for item in obligations:
        prefixes[item.account_id] += item.delta_cents
        reserves[item.account_id] = max(reserves[item.account_id], -prefixes[item.account_id])
    accounts = {
        account_id: AccountProjection(
            reserve_cents=reserves[account_id],
            shortfall_cents=max(0, reserves[account_id] - amount),
            max_outflow_cents=amount - reserves[account_id],
        )
        for account_id, amount in available.items()
    }
    return ShortfallReport(
        accounts=MappingProxyType(accounts),
        total_shortfall_cents=sum(item.shortfall_cents for item in accounts.values()),
        total_reserve_cents=sum(item.reserve_cents for item in accounts.values()),
        obligations=tuple(obligations),
    )
