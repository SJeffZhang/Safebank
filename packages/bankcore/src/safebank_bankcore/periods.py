"""Frozen subscription cost normalization, in integer cents only (PRD §5.4)."""


def normalize_period(amount_cents: int, period: str) -> tuple[int, int]:
    """Return (monthly equivalent, annual cost) for MONTHLY or WEEKLY only."""
    if type(amount_cents) is not int or not 0 <= amount_cents <= 100_000_000:
        raise ValueError("subscription cost must be bounded integer cents")
    if period == "MONTHLY":
        return amount_cents, amount_cents * 12
    if period == "WEEKLY":
        annual = amount_cents * 52
        return annual // 12, annual
    raise ValueError("unsupported subscription billing period")
