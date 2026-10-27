"""Pure-function analytics: spending summary (R4).

Consumption = CARD_SPEND / MANDATE, POSTED only.
Transfer out = TRANSFER_EXTERNAL with direction OUT.
Self-transfers, fund operations, deposits are excluded entirely.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime

from safebank_contracts.state import BankState

# The 8 category buckets defined in PRD §5.2.
CATEGORIES = ("", "餐饮", "交通", "购物", "娱乐", "生活服务", "数码", "健身", "其他")

# Transaction kinds that count as consumption.
_CONSUMPTION_KINDS = {"CARD_SPEND", "MANDATE"}

# Transaction kinds that count as external transfer out.
_TRANSFER_OUT_KINDS = {"TRANSFER_EXTERNAL"}


def spending_summary(
    state: BankState,
    *,
    customer_id: str,
    month: str,
    account_id: str | None = None,
) -> dict:
    """Return a SpendingResult dict for R4 ``get_spending_summary``.

    Args:
        state: Full bank state snapshot.
        customer_id: The calling customer.
        month: ``YYYY-MM`` string identifying the target month.
        account_id: Optional; if given the account must belong to *customer_id*.

    Returns:
        Dict ready for ``SpendingResult.model_validate()``.
    """
    parsed = datetime.strptime(month, "%Y-%m")
    year, mon = parsed.year, parsed.month

    def _in_month(ts: datetime) -> bool:
        return ts.year == year and ts.month == mon

    def _prev_month_start() -> date:
        if mon == 1:
            return date(year - 1, 12, 1)
        return date(year, mon - 1, 1)

    # Build daily buckets for target month.
    days_in_month: dict[date, int] = defaultdict(int)

    # Category aggregates:
    # {category_name -> {month:{amount_cents, count}, prev:{amount_cents, count}}}
    cat_amt: dict[str, dict[str, int]] = defaultdict(
        lambda: defaultdict(int)  # keys: "cur", "prev"
    )

    total_cents = 0
    prev_total_cents = 0
    transfer_out_cents = 0

    tz = state.sim_now.tzinfo
    for tx in state.transactions:
        # Only owned accounts.
        acct = state.accounts.get(tx.account_id)
        if acct is None or acct.customer_id != customer_id:
            continue
        if account_id is not None and tx.account_id != account_id:
            continue

        if tx.status != "POSTED":
            continue

        local_ts = (
            tx.occurred_at.astimezone(tz)
            if tx.occurred_at.tzinfo
            else tx.occurred_at.replace(tzinfo=tz)
        )

        if tx.kind in _CONSUMPTION_KINDS:
            cat = tx.category or ""
            # Map to known categories; "其他" is the catch-all.
            if cat not in CATEGORIES:
                cat = "其他"

            if _in_month(local_ts):
                cat_amt[cat]["cur"] += tx.amount_cents
                total_cents += tx.amount_cents
                days_in_month[local_ts.date()] += tx.amount_cents
            elif (
                local_ts.year == _prev_month_start().year
                and local_ts.month == _prev_month_start().month
            ):
                cat_amt[cat]["prev"] += tx.amount_cents
                prev_total_cents += tx.amount_cents
        elif tx.kind in _TRANSFER_OUT_KINDS and tx.direction == "OUT":
            if _in_month(local_ts):
                transfer_out_cents += tx.amount_cents

    # Build category list in stable order.
    categories: list[dict] = []
    for cat_name in CATEGORIES[1:]:  # skip empty sentinel
        cur = cat_amt.get(cat_name, {}).get("cur", 0)
        prev = cat_amt.get(cat_name, {}).get("prev", 0)
        if cur == 0 and prev == 0:
            continue
        share_bp = int(round(cur / total_cents * 10000)) if total_cents > 0 else 0
        categories.append(
            dict(
                category=cat_name,
                amount_cents=cur,
                share_bp=share_bp,
                previous_amount_cents=prev,
            )
        )

    # Calculate MoM change.
    mom_change_cents = total_cents - prev_total_cents
    mom_change_bp: int | None = None
    if prev_total_cents > 0:
        mom_change_bp = int(round(mom_change_cents / prev_total_cents * 10000))

    # Daily breakdown (sorted by date).
    daily = [dict(date=d, amount_cents=amt) for d, amt in sorted(days_in_month.items())]

    return dict(
        month=month,
        total_cents=total_cents,
        previous_total_cents=prev_total_cents,
        mom_change_cents=mom_change_cents,
        mom_change_bp=mom_change_bp,
        categories=tuple(categories),
        daily=tuple(daily),
        transfer_out_cents=transfer_out_cents,
    )
