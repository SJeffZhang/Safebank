"""Pure-function analytics: spending summary (R4), anomaly detection (R5),
and subscription detection (R9).

Consumption = CARD_SPEND / MERCHANT_CHARGE (legacy MANDATE), POSTED OUT only.
Transfer out = P2P_INTERNAL / P2P_EXTERNAL (legacy TRANSFER_EXTERNAL), OUT only.
Self-transfers, fund operations, deposits are excluded entirely.

Anomaly detection (R5) only examines CARD_SPEND transactions, using only
history that occurred *before* each transaction.  Four deterministic signals:

* ANOMALY_NIGHT_LARGE  – 00:00–05:00 local time, amount > 50 000 cents
* ANOMALY_DUPLICATE     – same card / merchant / amount within 5 minutes
* ANOMALY_NEW_MERCHANT_LARGE – first spend at merchant in prior 90 days, > 100 000 cents
* ANOMALY_CATEGORY_OUTLIER  – prior 90 days same category ≥ 10 txns, amount > mean + 3σ

Multiple signals on the same transaction are merged into a single anomaly item.

Subscription detection (R9) identifies recurring merchant charges from
transactions (same merchant_id, ≥ 3 occurrences, amount variance ≤ 5 %, interval
28–31 days (MONTHLY) or 7 days (WEEKLY)), cross-references with
standing_instructions (MERCHANT_MANDATE), and computes idle status via usage
evidence.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from safebank_contracts.state import BankState

from safebank_bankcore.periods import normalize_period
from safebank_bankcore.reserve import _business_time
from safebank_bankcore.transaction_facts import is_external_transfer

# The 8 category buckets defined in PRD §5.2.
CATEGORIES = ("", "餐饮", "交通", "购物", "娱乐", "生活服务", "数码", "健身", "其他")

# Transaction kinds that count as consumption.
_CHARGE_KINDS = frozenset({"MERCHANT_CHARGE", "MANDATE"})
_CONSUMPTION_KINDS = _CHARGE_KINDS | {"CARD_SPEND"}


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
    owned_account_ids = {a.id for a in state.accounts.values() if a.customer_id == customer_id}

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

        if tx.kind in _CONSUMPTION_KINDS and tx.direction == "OUT":
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
        elif is_external_transfer(tx, state.transfers, owned_account_ids):
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

    results: dict[str, list[str]] = {}  # tx_id → codes
    evidence_map: dict[str, dict] = {}  # tx_id → evidence fields

    for tx in candidates:
        idx = full_history.index(tx)  # position in full_history (sorted)
        prior = full_history[:idx]  # everything strictly before this tx

        codes: list[str] = []
        ev: dict = {}

        local_hour = _business_time(tx.occurred_at).hour

        # 2a. ANOMALY_NIGHT_LARGE  (00:00–04:59, > 50 000 cents)
        if 0 <= local_hour <= 4 and tx.amount_cents > 50_000:
            codes.append("ANOMALY_NIGHT_LARGE")
            ev["threshold_cents"] = 50_000

        # 2b. ANOMALY_DUPLICATE  (same card/merchant/amount, ≤ 5 min apart)
        if tx.card_id is not None and tx.merchant_id is not None:
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
                h for h in prior if h.category == tx.category and h.occurred_at >= cutoff
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


# ── R9: detect_subscriptions ────────────────────────────────────────────────

# Thresholds exposed as keyword-only parameters so the caller can adjust them
# without rewriting the function. The defaults reflect PRD §5.4 rules.
#
#   * variance_tolerance_bp : amount tolerance in basis points (5 % = 500 bp).
#   * min_occurrences       : minimum charge count to qualify as recurring.
#   * period_monthly_days   : inclusive day range for MONTHLY intervals.
#   * period_weekly_days    : exact day count for WEEKLY intervals.
#   * typical_method        : "median" (default) or "floor_mean" for
#     typical_amount_cents.

_PERIOD_MONTHLY_RANGE = range(28, 32)  # 28 … 31 inclusive
_PERIOD_WEEKLY_DAYS = 7


def detect_subscriptions(
    state: BankState,
    *,
    customer_id: str,
    lookback_days: int,
    variance_tolerance_bp: int = 500,
    min_occurrences: int = 3,
    typical_method: str = "median",
) -> dict:
    """Return a ``DetectedSubscriptionsResult`` dict for R9 ``detect_subscriptions``.

    Steps (PRD §5.4):

    1. Collect POSTED transactions of kind ``MANDATE`` belonging to
       *customer_id*, within ``[sim_now - lookback_days, sim_now)``.
       (``CARD_SPEND`` is a *usage* signal, never a charge candidate.)
    2. Group by ``merchant_id`` and identify recurring merchants:
       - ≥ *min_occurrences* charges
       - inter-charge intervals all within 28–31 days → MONTHLY,
         or all exactly 7 days → WEEKLY
       - every amount within ± *variance_tolerance_bp* of the typical amount
    3. Cross-reference with ``state.subscriptions`` (derived from
       ``MERCHANT_MANDATE`` standing instructions) by merchant_id to attach
       ``instruction_id`` and ``billing_id``.
    4. Compute usage evidence for idle detection (PRD §5.4 S-5):
       - *recent*: non-MANDATE transactions (CARD_SPEND) at the same merchant
         within the last 60 calendar days (sim_now - 60 d, sim_now].
       - *previous*: same, but 60–120 days prior to sim_now
         (sim_now - 120 d, sim_now - 60 d].
       - ``is_idle`` = true iff the merchant has ``usage_expected=True``,
         recent == 0, *and* previous > 0.
    5. Annualize: MONTHLY → ×12, WEEKLY → ×52.
    """
    from statistics import median

    sim_now = _business_time(state.sim_now)
    cutoff = sim_now - _days_as_delta(lookback_days)
    idle_recent_cutoff = sim_now - _days_as_delta(60)
    idle_prev_cutoff = sim_now - _days_as_delta(120)

    variance_ratio = variance_tolerance_bp / 10000.0  # bp → fraction

    # ── 1. Collect candidate transactions ─────────────────────────────
    txs: list = []
    for tx in state.transactions:
        if tx.merchant_id is None:
            continue
        if tx.kind not in _CHARGE_KINDS or tx.direction != "OUT":
            continue
        if tx.status != "POSTED":
            continue
        acct = state.accounts.get(tx.account_id)
        if acct is None or acct.customer_id != customer_id:
            continue
        bt = _business_time(tx.occurred_at)
        if bt < cutoff or bt >= sim_now:
            continue
        txs.append(tx)

    # ── 2. Group by merchant and identify recurring ───────────────────
    by_merchant: dict[str, list] = {}
    for tx in txs:
        by_merchant.setdefault(tx.merchant_id, []).append(tx)

    # Collect all transactions (including those outside the lookback) for
    # usage-evidence computation.
    all_txs: list = []
    for tx in state.transactions:
        if tx.merchant_id is None:
            continue
        acct = state.accounts.get(tx.account_id)
        if acct is None or acct.customer_id != customer_id:
            continue
        if tx.status != "POSTED":
            continue
        all_txs.append(tx)

    # Pre-group all transactions by merchant for usage evidence.
    all_by_merchant: dict[str, list] = {}
    for tx in all_txs:
        all_by_merchant.setdefault(tx.merchant_id, []).append(tx)

    items: list[dict] = []
    merchant_meta: dict = {}
    if state.obligations is not None:
        merchant_meta = dict(state.obligations.merchants)

    # Build a merchant→instruction_id/billing_id map from state.subscriptions.
    sub_by_merchant: dict[str, dict] = {}
    for sub in state.subscriptions:
        owner = state.accounts.get(sub.account_id)
        if owner is None or owner.customer_id != customer_id:
            continue  # never link another customer's mandate or bill
        if sub.status != "DELETED":
            sub_by_merchant[sub.merchant_id] = dict(
                instruction_id=sub.instruction_id,
                billing_id=sub.billing_id,
            )

    for merchant_id, charges in sorted(by_merchant.items()):
        if len(charges) < min_occurrences:
            continue

        charges.sort(key=lambda t: t.occurred_at)
        amounts = [c.amount_cents for c in charges]
        intervals = [
            (
                _business_time(charges[i].occurred_at) - _business_time(charges[i - 1].occurred_at)
            ).days
            for i in range(1, len(charges))
        ]

        # Determine period from intervals.
        period: str | None = None
        if set(intervals) == {_PERIOD_WEEKLY_DAYS}:
            period = "WEEKLY"
        elif all(d in _PERIOD_MONTHLY_RANGE for d in intervals):
            period = "MONTHLY"

        if period is None:
            continue

        # Compute typical amount.
        if typical_method == "floor_mean":
            typical = sum(amounts) // len(amounts)
        else:  # median
            typical = int(median(amounts))

        # Check variance for every charge.
        if typical == 0:
            continue
        if any(abs(a - typical) / typical > variance_ratio for a in amounts):
            continue

        # Correlate with standing instructions.
        sub_info = sub_by_merchant.get(merchant_id, {})
        instruction_id = sub_info.get("instruction_id")
        billing_id = sub_info.get("billing_id")

        # ── Usage evidence ──────────────────────────────────────────
        recent = 0
        previous = 0
        for tx in all_by_merchant.get(merchant_id, []):
            if tx.kind in _CHARGE_KINDS:
                continue  # canonical and historical charges are never usage
            bt = _business_time(tx.occurred_at)
            if bt > sim_now:
                continue
            if bt > idle_recent_cutoff:
                recent += 1
            elif bt > idle_prev_cutoff:
                previous += 1

        meta = merchant_meta.get(merchant_id)
        usage_expected = meta.usage_expected if meta else False
        is_idle = usage_expected and recent == 0 and previous > 0

        # ── Annual cost ─────────────────────────────────────────────
        _, annual_cost_cents = normalize_period(typical, period)

        items.append(
            dict(
                merchant_id=merchant_id,
                merchant_name=meta.name if meta else "",
                instruction_id=instruction_id,
                billing_id=billing_id,
                period=period,
                typical_amount_cents=typical,
                occurrences=len(charges),
                last_charged_at=charges[-1].occurred_at,
                is_idle=is_idle,
                usage_evidence=dict(
                    recent_usage_count=recent,
                    previous_usage_count=previous,
                ),
                annual_cost_cents=annual_cost_cents,
            )
        )

    return {"items": tuple(items)}


def _days_as_delta(days: int):
    """Return a ``timedelta`` for *days* calendar days."""
    from datetime import timedelta

    return timedelta(days=days)
