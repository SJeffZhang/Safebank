"""Trading-day calendar: weekdays (Mon–Fri), no holidays, 15:00 Shanghai cutoff.

No IO, no database, no randomness. Timezone-aware datetimes are normalized to
Asia/Shanghai before cutoff and trade-date decisions.
"""

from datetime import date, datetime, time, timedelta

from .reserve import BUSINESS_TIMEZONE, _business_time

__all__ = [
    "BUSINESS_TIMEZONE",
    "T_CUTOFF",
    "is_trading_day",
    "next_trading_day",
    "previous_trading_day",
    "trading_days_between",
    "is_before_cutoff",
    "trade_date",
]

T_CUTOFF = time(15, 0)


def is_trading_day(d: date) -> bool:
    """Monday–Friday are trading days; no public-holiday handling."""
    return d.weekday() < 5  # Mon=0, Fri=4


def next_trading_day(d: date) -> date:
    """Return the first trading day on or after *d*."""
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def previous_trading_day(d: date) -> date:
    """Return the last trading day on or before *d*."""
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def trading_days_between(start: date, end: date) -> int:
    """Signed count of trading days *after start* up to and including *end*.

    ``trading_days_between(d, d) == 0`` for any *d* (even non-trading days).

    Returns a negative value when *end* < *start*.
    """
    if end == start:
        return 0
    if end > start:
        sign = 1
        cur = start + timedelta(days=1)
        stop = end
    else:
        sign = -1
        cur = end + timedelta(days=1)
        stop = start
    count = 0
    while cur <= stop:
        if is_trading_day(cur):
            count += 1
        cur += timedelta(days=1)
    return sign * count


def is_before_cutoff(dt: datetime) -> bool:
    """Return True when *dt* (in Asia/Shanghai) is before 15:00."""
    bt = _business_time(dt)
    return bt.time() < T_CUTOFF


def trade_date(dt: datetime) -> date:
    """Return the trade date (T-day) for an action at *dt*.

    - On a trading day before 15:00 → same day.
    - On a trading day at/after 15:00 → next trading day.
    - On a non-trading day → next trading day.
    """
    bt = _business_time(dt)
    d = bt.date()
    if is_trading_day(d) and bt.time() < T_CUTOFF:
        return d
    return next_trading_day(d + timedelta(days=1))
