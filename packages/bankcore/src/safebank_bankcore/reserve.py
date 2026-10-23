"""Deterministic obligation projection; callers must supply complete obligation data.

Windows are left open and right closed. Aware timestamps are normalized to
Asia/Shanghai before calendar validation and recurrence. Scheduled recurrence
uses the original monthly anchor and Shanghai local execution time; weekday
follows datetime.weekday(): Monday=0 through Sunday=6. No wall clock, IO, or
authorization decision is used.
"""

from calendar import monthrange
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from types import MappingProxyType
from zoneinfo import ZoneInfo

from safebank_contracts.state import Account, BankState
from safebank_contracts.views import ScheduledView, SubscriptionView

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
    if local_time.tzinfo is not None or local_time != next_run.time():
        raise ValueError(f"instruction {item.id} has inconsistent local execution time")
    if item.end_at is not None:
        _business_time(item.end_at)
    if item.period == "ONCE":
        if item.anchor_day is not None or item.weekday is not None:
            raise ValueError(f"ONCE instruction {item.id} cannot have recurring anchors")
    elif item.period == "WEEKLY":
        if (
            type(item.weekday) is not int
            or not 0 <= item.weekday <= 6
            or item.anchor_day is not None
            or next_run.weekday() != item.weekday
        ):
            raise ValueError(f"WEEKLY instruction {item.id} has invalid or inconsistent weekday")
    elif item.period == "MONTHLY":
        if (
            type(item.anchor_day) is not int
            or not 1 <= item.anchor_day <= 31
            or item.weekday is not None
        ):
            raise ValueError(f"MONTHLY instruction {item.id} has invalid anchors")
        expected_day = min(item.anchor_day, monthrange(next_run.year, next_run.month)[1])
        if next_run.day != expected_day:
            raise ValueError(f"instruction {item.id} next_run_at disagrees with anchor_day")
    else:
        raise ValueError(f"unsupported period for instruction {item.id}")
    return local_time


def _next_month(due_at: datetime, anchor_day: int, local_time: time) -> datetime:
    year = due_at.year + due_at.month // 12
    month = due_at.month % 12 + 1
    day = min(anchor_day, monthrange(year, month)[1])
    return datetime.combine(
        due_at.date().replace(year=year, month=month, day=day), local_time, tzinfo=due_at.tzinfo
    )


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
        due_at = _business_time(item.next_run_at)
        cutoff = min(end, _business_time(item.end_at)) if item.end_at is not None else end
        while due_at <= cutoff:
            if due_at > start:
                result.append(
                    Obligation(due_at, 3, item.id, item.from_account_id, -item.amount_cents)
                )
            if item.period == "ONCE":
                break
            if item.period == "WEEKLY":
                due_at += timedelta(days=7)
                continue
            assert item.anchor_day is not None
            due_at = _next_month(due_at, item.anchor_day, local_time)
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


def _own_target(state: BankState, item: ScheduledView) -> str | None:
    """Resolve ownership without granting income from another customer."""
    source = _customer_account(state, item.from_account_id)
    if item.to_account_id is not None:
        target = _customer_account(state, item.to_account_id)
        if target.customer_id != source.customer_id:
            raise ValueError(f"instruction {item.id} direct target must be an own account")
        return target.id
    payee = state.payees.get(item.payee_id)
    if payee is None or payee.customer_id != source.customer_id:
        raise ValueError(f"unknown payee ownership for instruction {item.id}")
    if payee.target_account_id is None:
        return None  # explicit external payee; not an unknown internal owner
    target = _customer_account(state, payee.target_account_id)
    if target.customer_id == source.customer_id:
        raise ValueError(f"instruction {item.id} must use to_account_id for own transfers")
    return None


def _merchant_occurrences(
    subscriptions: Iterable[SubscriptionView], start: datetime, end: datetime
) -> tuple[Obligation, ...]:
    """Seven-day projection only: SubscriptionView has no original monthly anchor.

    A monthly next_due_at must be current, since advancing an overdue value may
    drift from a hidden 29/30/31 anchor. A next_due_at equal to start is excluded.
    Weekly recurrence is unambiguous and retains the supplied execution time.
    """
    result: list[Obligation] = []
    instruction_ids: set[str] = set()
    billing_ids: set[str] = set()
    for item in subscriptions:
        if item.instruction_id in instruction_ids or item.billing_id in billing_ids:
            raise ValueError("duplicate merchant instruction or billing identity")
        instruction_ids.add(item.instruction_id)
        billing_ids.add(item.billing_id)
        if item.status != "ACTIVE":
            continue
        due_at = _business_time(item.next_due_at)
        if item.period == "MONTHLY" and due_at < start:
            raise ValueError(f"monthly bill {item.billing_id} lacks anchor for stale next_due_at")
        while due_at <= end:
            if due_at > start:
                result.append(
                    Obligation(due_at, 4, item.billing_id, item.account_id, -item.amount_cents)
                )
            if item.period != "WEEKLY":
                break
            due_at += timedelta(days=7)
    return tuple(result)


def projected_shortfall(state: BankState, *, days: int = 7) -> ShortfallReport:
    """Compute per-account prefix reserves from a complete seven-day BankState.

    Callers MUST load all relevant customer accounts, ACTIVE instructions, actual
    merchant bills and ownership data. Empty default tuples from the P1 loader
    are not evidence of no obligations: production use remains blocked until S3.

    Only integer days=7 is supported: SubscriptionView cannot express monthly
    anchors for longer windows. scheduled_occurrences separately supports them.
    Own-account scheduled transfers credit the target; cross-customer transfers,
    wages, fund settlements and pending income provide no guaranteed income.
    The window and returned due_at values use Asia/Shanghai; any equivalent
    aware input offset is accepted and preserves the same business calendar.
    max_outflow_cents remains negative when obligations exceed available funds.
    No L2 policy violation or authorization decision is returned here.
    """
    if type(days) is not int or days != 7:
        raise ValueError("projected_shortfall supports only integer days=7")
    start = _business_time(state.sim_now)
    end = start + timedelta(days=days)
    scheduled = scheduled_occurrences(state.instructions, start, end)
    own_targets = {
        item.id: _own_target(state, item) for item in state.instructions if item.status == "ACTIVE"
    }
    obligations: list[Obligation] = []
    for debit in scheduled:
        obligations.append(debit)
        target_id = own_targets[debit.id]
        if target_id is not None:
            obligations.append(
                Obligation(debit.due_at, debit.rank, debit.id, target_id, -debit.delta_cents)
            )
    for item in state.subscriptions:
        if item.status == "ACTIVE":
            _customer_account(state, item.account_id)
    obligations.extend(_merchant_occurrences(state.subscriptions, start, end))
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
