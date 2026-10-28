"""Pure-function analytics: spending summary (R4) and anomaly detection (R5).

Consumption = CARD_SPEND / MANDATE, POSTED only.
Transfer out = TRANSFER_EXTERNAL with direction OUT.
Self-transfers, fund operations, deposits are excluded entirely.

Anomaly detection (R5) only examines CARD_SPEND transactions, using only
history that occurred *before* each transaction.  Four deterministic signals:

* ANOMALY_NIGHT_LARGE  – 00:00–05:00 local time, amount > 50 000 cents
* ANOMALY_DUPLICATE     – same card / merchant / amount within 5 minutes
* ANOMALY_NEW_MERCHANT_LARGE – first spend at merchant in prior 90 days, > 100 000 cents
* ANOMALY_CATEGORY_OUTLIER  – prior 90 days same category ≥ 10 txns, amount > mean + 3σ

Multiple signals on the same transaction are merged into a single anomaly item.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from safebank_bankcore.reserve import _business_time
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

    ``share_bp`` values are computed per-category via
    ``int(round(cur / total * 10000))`` (individually rounded);
    the sum of all categories' ``share_bp`` may therefore differ from
    10000 by up to a few basis points.

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

    for tx in state.transactions:
        # Only owned accounts.
        acct = state.accounts.get(tx.account_id)
        if acct is None or acct.customer_id != customer_id:
            continue
        if account_id is not None and tx.account_id != account_id:
            continue

        if tx.status != "POSTED":
            continue

        local_ts = _business_time(tx.occurred_at)

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


def _tx_view(state: BankState, tx) -> dict:
    """Convert a *Transaction* into a dict suitable for ``TransactionView``.

    ``merchant_name`` is always ``None`` (the obligation-snapshot merchant
    table lives outside BankState; the R5 consumer can enrich this field
    from ``merchant_id`` if needed).
    """
    return dict(
        id=tx.id,
        account_id=tx.account_id,
        direction=tx.direction,
        kind=tx.kind,
        status=tx.status,
        amount_cents=tx.amount_cents,
        balance_after_cents=tx.balance_after_cents,
        available_after_cents=tx.available_after_cents,
        counterparty=tx.counterparty,
        memo=tx.memo,
        category=tx.category,
        merchant_id=tx.merchant_id,
        merchant_name=None,
        payee_id=tx.payee_id,
        card_id=tx.card_id,
        occurred_at=tx.occurred_at,
    )


def detect_anomalies(
    state: BankState,
    *,
    customer_id: str,
    from_at: datetime,
    to_at: datetime,
    account_id: str | None = None,
) -> dict:
    """Return an ``AnomaliesResult`` dict for R5 ``detect_anomalies``.

    Only ``CARD_SPEND`` / ``POSTED`` transactions in ``[from_at, to_at)`` are
    candidates.  Each candidate is checked against the four deterministic
    signals using only history that occurred **before** that transaction.
    """
    # ── 1. Collect candidates (chronological) ──────────────────────────
    candidates: list = []
    # Full CARD_SPEND/POSTED history for this customer up to *to_at*
    # (used as the lookup pool for per-transaction history checks).
    full_history: list = []
    for tx in state.transactions:
        if tx.kind != "CARD_SPEND" or tx.status != "POSTED":
            continue
        acct = state.accounts.get(tx.account_id)
        if acct is None or acct.customer_id != customer_id:
            continue
        if account_id is not None and tx.account_id != account_id:
            continue
        if tx.occurred_at < to_at:
            full_history.append(tx)
            if tx.occurred_at >= from_at:
                candidates.append(tx)

    candidates.sort(key=lambda t: t.occurred_at)
    full_history.sort(key=lambda t: t.occurred_at)

    if not candidates:
        return {"items": ()}

    # ── 2. Per-transaction signal checks ───────────────────────────────
    # Build a fast-lookup set of (card_id, merchant_id, amount_cents) for
    # recent duplicates: we iterate history once and keep the *last-seen*
    # index per key so duplicate check is O(1) per tx.
    # Also pre-index by category for the outlier check.
    history_by_key: dict[tuple, int] = {}  # key → index in full_history

    results: dict[str, list[str]] = {}   # tx_id → codes
    evidence_map: dict[str, dict] = {}   # tx_id → evidence fields

    for tx in candidates:
        idx = full_history.index(tx)  # position in full_history (sorted)
        prior = full_history[:idx]    # everything strictly before this tx

        codes: list[str] = []
        ev: dict = {}

        local_hour = _business_time(tx.occurred_at).hour

        # 2a. ANOMALY_NIGHT_LARGE  (00:00–04:59, > 50 000 cents)
        if 0 <= local_hour <= 4 and tx.amount_cents > 50_000:
            codes.append("ANOMALY_NIGHT_LARGE")
            ev["threshold_cents"] = 50_000

        # 2b. ANOMALY_DUPLICATE  (same card/merchant/amount, ≤ 5 min apart)
        if tx.card_id is not None and tx.merchant_id is not None:
            dup_key = (tx.card_id, tx.merchant_id, tx.amount_cents)
            # Find the closest previous match (backwards through prior)
            for prev in reversed(prior):
                if (
                    prev.card_id == tx.card_id
                    and prev.merchant_id == tx.merchant_id
                    and prev.amount_cents == tx.amount_cents
                ):
                    diff_min = (tx.occurred_at - prev.occurred_at).total_seconds() / 60.0
                    if diff_min <= 5:
                        codes.append("ANOMALY_DUPLICATE")
                        ev["previous_transaction_id"] = prev.id
                        ev["window_minutes"] = int(diff_min)
                    break

        # 2c. ANOMALY_NEW_MERCHANT_LARGE  (no prior merchant in 90 d, > 100 000 cents)
        if tx.merchant_id is not None:
            cutoff = tx.occurred_at - _days_as_delta(90)
            prior_90d = [h for h in prior if h.occurred_at >= cutoff]
            prior_merchants = {h.merchant_id for h in prior_90d}
            if tx.merchant_id not in prior_merchants and tx.amount_cents > 100_000:
                codes.append("ANOMALY_NEW_MERCHANT_LARGE")
                ev["threshold_cents"] = 100_000

        # 2d. ANOMALY_CATEGORY_OUTLIER  (≥ 10 prior same-cat in 90 d, amount > μ + 3σ)
        if tx.category:
            cutoff = tx.occurred_at - _days_as_delta(90)
            same_cat_prior = [
                h
                for h in prior
                if h.category == tx.category and h.occurred_at >= cutoff
            ]
            if len(same_cat_prior) >= 10:
                amounts_dec = [Decimal(h.amount_cents) for h in same_cat_prior]
                n = Decimal(len(amounts_dec))
                mu = sum(amounts_dec) / n
                variance = sum((a - mu) ** 2 for a in amounts_dec) / n
                sigma = variance.sqrt()
                threshold = mu + 3 * sigma
                if tx.amount_cents > int(threshold.to_integral_value(ROUND_HALF_UP)):
                    codes.append("ANOMALY_CATEGORY_OUTLIER")
                    ev["baseline_count"] = len(same_cat_prior)
                    ev["mean_cents"] = int(mu.to_integral_value(ROUND_HALF_UP))
                    ev["threshold_cents"] = int(threshold.to_integral_value(ROUND_HALF_UP))

        if codes:
            results.setdefault(tx.id, []).extend(codes)
            # Merge evidence: later signals on same tx can add fields
            existing = evidence_map.get(tx.id, {})
            existing.update(ev)
            evidence_map[tx.id] = existing

    # ── 3. Build output ────────────────────────────────────────────────
    items: list[dict] = []
    for tx in candidates:
        if tx.id not in results:
            continue
        codes = results[tx.id]
        ev = evidence_map.get(tx.id, {})
        evidence = dict(
            baseline_count=ev.get("baseline_count", 0),
            mean_cents=ev.get("mean_cents"),
            threshold_cents=ev.get("threshold_cents"),
            previous_transaction_id=ev.get("previous_transaction_id"),
            window_minutes=ev.get("window_minutes"),
        )
        items.append(
            dict(
                transaction=_tx_view(state, tx),
                codes=tuple(codes),
                evidence=evidence,
            )
        )

    return {"items": tuple(items)}


def _days_as_delta(days: int):
    """Return a ``timedelta`` for *days* calendar days."""
    from datetime import timedelta

    return timedelta(days=days)
