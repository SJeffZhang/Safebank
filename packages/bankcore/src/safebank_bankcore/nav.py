"""Synthetic net-asset-value computation (no real market data).

PRD §8.2 formula:

    nav(d) = base_nav + Decimal(business_days_signed(base_date, d)) * Decimal('0.0001')

Floor 0.0001, ROUND_HALF_UP to 4 decimal places.
Money-market funds (MMF) are constant at their base_nav.

Non-trading days use the previous trading day's NAV.
All arithmetic is Decimal (no float).
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from .calendar import is_trading_day, previous_trading_day, trading_days_between

__all__ = [
    "MIN_NAV",
    "NAV_STEP",
    "nav",
    "nav_history",
]

MIN_NAV = Decimal("0.0001")
NAV_STEP = Decimal("0.0001")


def _trading_nav(
    base_nav: Decimal,
    base_date: date,
    on_date: date,
) -> Decimal:
    """Raw NAV for a trading day (caller guarantees *on_date* is a trading day)."""
    days = trading_days_between(base_date, on_date)
    value = base_nav + Decimal(days) * NAV_STEP
    if value < MIN_NAV:
        value = MIN_NAV
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def nav(
    base_nav: Decimal,
    base_date: date,
    product_type: str,
    on_date: date,
) -> Decimal:
    """NAV for *product_type* on *on_date*.

    MMF products always return *base_nav*.  Non-trading days return the
    previous trading day's NAV.
    """
    if product_type == "MMF":
        return base_nav
    if not is_trading_day(on_date):
        on_date = previous_trading_day(on_date)
    return _trading_nav(base_nav, base_date, on_date)


def nav_history(
    base_nav: Decimal,
    base_date: date,
    product_type: str,
    end_date: date,
    count: int = 30,
) -> list[tuple[date, Decimal]]:
    """Return the last *count* trading-day NAV points ending on or before *end_date*.

    Each element is ``(date, nav)``.  The first element is the earliest date,
    the last is the latest.
    """
    if product_type == "MMF":
        # MMF NAV is constant – return the last *count* trading days.
        cur = previous_trading_day(end_date) if not is_trading_day(end_date) else end_date
        result: list[tuple[date, Decimal]] = []
        for _ in range(count):
            result.append((cur, base_nav))
            cur = previous_trading_day(cur - date.resolution)
        result.reverse()
        return result

    # Walk backwards through trading days.
    cur = previous_trading_day(end_date) if not is_trading_day(end_date) else end_date
    points: list[tuple[date, Decimal]] = []
    for _ in range(count):
        points.append((cur, _trading_nav(base_nav, base_date, cur)))
        cur = previous_trading_day(cur - date.resolution)
    points.reverse()
    return points
