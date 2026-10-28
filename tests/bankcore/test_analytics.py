"""Pure-function tests for safebank_bankcore.analytics.spending_summary."""

from datetime import date, datetime

import safebank_bankcore.analytics as subject
from safebank_contracts.enums import Phase
from safebank_contracts.state import BankState, Transaction

from tests.bankcore.fixtures import base_state


def at(value: str) -> datetime:
    return datetime.fromisoformat(value)


_UTC = __import__("zoneinfo").ZoneInfo("UTC")


def utc_at(y: int, mo: int, d: int, h: int = 0, mi: int = 0, s: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, s, tzinfo=_UTC)


def tx(**updates) -> Transaction:
    """Minimal posted CARD_SPEND transaction."""
    values = dict(
        id="tx_1",
        transfer_id="xfr_1",
        customer_id="cus_a",
        account_id="acc_a",
        direction="OUT",
        kind="CARD_SPEND",
        phase=Phase.POSTED,
        status="POSTED",
        amount_cents=5000,
        balance_after_cents=595000,
        available_after_cents=595000,
        counterparty="",
        memo=None,
        category="餐饮",
        merchant_id=None,
        payee_id=None,
        card_id=None,
        channel=None,
        occurred_at=at("2026-10-15T12:00:00+08:00"),
        sequence=1,
    )
    values.update(updates)
    return Transaction(**values)


def with_txns(state: BankState, *txns: Transaction) -> BankState:
    return state.model_copy(update={"transactions": txns})


def with_account(state: BankState, account_id: str, **updates) -> BankState:
    accts = dict(state.accounts)
    accts[account_id] = accts[account_id].model_copy(update=updates)
    return state.model_copy(update={"accounts": accts})


def test_no_transactions():
    state = base_state()
    result = subject.spending_summary(state, customer_id="cus_a", month="2026-10")
    assert result["total_cents"] == 0
    assert result["previous_total_cents"] == 0
    assert result["mom_change_cents"] == 0
    assert result["mom_change_bp"] is None
    assert result["categories"] == ()
    assert result["daily"] == ()
    assert result["transfer_out_cents"] == 0


def test_previous_month_zero_gives_null_bp():
    state = with_txns(
        base_state(),
        tx(id="t1", amount_cents=1000, occurred_at=at("2026-10-01T10:00:00+08:00"), sequence=1),
    )
    result = subject.spending_summary(state, customer_id="cus_a", month="2026-10")
    assert result["total_cents"] == 1000
    assert result["previous_total_cents"] == 0
    assert result["mom_change_bp"] is None


def test_multi_category_and_share_bp():
    state = with_txns(
        base_state(),
        tx(
            id="t1",
            amount_cents=3000,
            category="餐饮",
            occurred_at=at("2026-10-05T10:00:00+08:00"),
            sequence=1,
        ),
        tx(
            id="t2",
            amount_cents=7000,
            category="购物",
            occurred_at=at("2026-10-10T10:00:00+08:00"),
            sequence=2,
        ),
    )
    result = subject.spending_summary(state, customer_id="cus_a", month="2026-10")
    assert result["total_cents"] == 10000
    cats = {c["category"]: c for c in result["categories"]}
    assert cats["餐饮"]["amount_cents"] == 3000
    assert cats["购物"]["amount_cents"] == 7000
    # 3000/10000 = 30% = 3000 bp; 7000/10000 = 70% = 7000 bp
    assert cats["餐饮"]["share_bp"] == 3000
    assert cats["购物"]["share_bp"] == 7000
    # Sum of share_bp should be 10000
    assert sum(c["share_bp"] for c in result["categories"]) == 10000


def test_cross_account_aggregation():
    """Transactions across multiple accounts owned by the same customer are aggregated."""
    state = with_account(base_state(), "acc_s", customer_id="cus_a")
    state = with_txns(
        state,
        tx(
            id="t1",
            account_id="acc_a",
            amount_cents=2000,
            category="餐饮",
            occurred_at=at("2026-10-01T10:00:00+08:00"),
            sequence=1,
        ),
        tx(
            id="t2",
            account_id="acc_s",
            amount_cents=3000,
            category="餐饮",
            occurred_at=at("2026-10-02T10:00:00+08:00"),
            sequence=2,
        ),
    )
    result = subject.spending_summary(state, customer_id="cus_a", month="2026-10")
    assert result["total_cents"] == 5000
    cats = {c["category"]: c for c in result["categories"]}
    assert cats["餐饮"]["amount_cents"] == 5000


def test_account_id_filter():
    state = with_account(base_state(), "acc_s", customer_id="cus_a")
    state = with_txns(
        state,
        tx(
            id="t1",
            account_id="acc_a",
            amount_cents=2000,
            category="餐饮",
            occurred_at=at("2026-10-01T10:00:00+08:00"),
            sequence=1,
        ),
        tx(
            id="t2",
            account_id="acc_s",
            amount_cents=3000,
            category="交通",
            occurred_at=at("2026-10-02T10:00:00+08:00"),
            sequence=2,
        ),
    )
    result = subject.spending_summary(
        state, customer_id="cus_a", month="2026-10", account_id="acc_a"
    )
    assert result["total_cents"] == 2000
    cats = {c["category"]: c for c in result["categories"]}
    assert "餐饮" in cats
    assert "交通" not in cats


def test_pending_not_counted():
    state = with_txns(
        base_state(),
        tx(
            id="t1",
            amount_cents=5000,
            status="POSTED",
            occurred_at=at("2026-10-01T10:00:00+08:00"),
            sequence=1,
        ),
        tx(
            id="t2",
            amount_cents=9999,
            status="PENDING",
            occurred_at=at("2026-10-01T11:00:00+08:00"),
            sequence=2,
        ),
    )
    result = subject.spending_summary(state, customer_id="cus_a", month="2026-10")
    assert result["total_cents"] == 5000


def test_released_not_counted():
    state = with_txns(
        base_state(),
        tx(
            id="t1",
            amount_cents=5000,
            status="POSTED",
            occurred_at=at("2026-10-01T10:00:00+08:00"),
            sequence=1,
        ),
        tx(
            id="t2",
            amount_cents=4000,
            status="RELEASED",
            occurred_at=at("2026-10-01T11:00:00+08:00"),
            sequence=2,
        ),
    )
    result = subject.spending_summary(state, customer_id="cus_a", month="2026-10")
    assert result["total_cents"] == 5000


def test_p2p_transfer_out_separate():
    state = with_txns(
        base_state(),
        tx(
            id="t1",
            amount_cents=3000,
            kind="CARD_SPEND",
            category="购物",
            occurred_at=at("2026-10-05T10:00:00+08:00"),
            sequence=1,
        ),
        tx(
            id="t2",
            amount_cents=8000,
            kind="TRANSFER_EXTERNAL",
            category="",
            occurred_at=at("2026-10-10T10:00:00+08:00"),
            sequence=2,
        ),
    )
    result = subject.spending_summary(state, customer_id="cus_a", month="2026-10")
    assert result["total_cents"] == 3000
    assert result["transfer_out_cents"] == 8000


def test_transfer_in_not_counted():
    """Transfer in (direction=IN) does not affect consumption or transfer_out."""
    state = with_txns(
        base_state(),
        tx(
            id="t1",
            amount_cents=3000,
            kind="CARD_SPEND",
            category="购物",
            occurred_at=at("2026-10-05T10:00:00+08:00"),
            sequence=1,
        ),
        tx(
            id="t2",
            amount_cents=5000,
            kind="TRANSFER_EXTERNAL",
            direction="IN",
            occurred_at=at("2026-10-10T10:00:00+08:00"),
            sequence=2,
        ),
    )
    result = subject.spending_summary(state, customer_id="cus_a", month="2026-10")
    assert result["total_cents"] == 3000
    assert result["transfer_out_cents"] == 0


def test_self_transfer_excluded():
    """Self-transfers (TRANSFER_SELF) are excluded from both consumption and transfer_out."""
    state = with_txns(
        base_state(),
        tx(
            id="t1",
            amount_cents=3000,
            kind="CARD_SPEND",
            category="购物",
            occurred_at=at("2026-10-05T10:00:00+08:00"),
            sequence=1,
        ),
        tx(
            id="t2",
            amount_cents=5000,
            kind="TRANSFER_SELF",
            direction="OUT",
            occurred_at=at("2026-10-10T10:00:00+08:00"),
            sequence=2,
        ),
    )
    result = subject.spending_summary(state, customer_id="cus_a", month="2026-10")
    assert result["total_cents"] == 3000
    assert result["transfer_out_cents"] == 0


def test_month_boundary():
    """Transactions outside the target month are not counted."""
    state = with_txns(
        base_state(),
        tx(
            id="t1",
            amount_cents=1000,
            category="餐饮",
            occurred_at=at("2026-09-30T23:59:59+08:00"),
            sequence=1,
        ),
        tx(
            id="t2",
            amount_cents=2000,
            category="餐饮",
            occurred_at=at("2026-10-01T00:00:00+08:00"),
            sequence=2,
        ),
        tx(
            id="t3",
            amount_cents=4000,
            category="交通",
            occurred_at=at("2026-10-31T23:59:59+08:00"),
            sequence=3,
        ),
        tx(
            id="t4",
            amount_cents=8000,
            category="交通",
            occurred_at=at("2026-11-01T00:00:00+08:00"),
            sequence=4,
        ),
    )
    result = subject.spending_summary(state, customer_id="cus_a", month="2026-10")
    assert result["total_cents"] == 6000  # t2 + t3


def test_previous_month_categories():
    state = with_txns(
        base_state(),
        tx(
            id="t1",
            amount_cents=5000,
            category="娱乐",
            occurred_at=at("2026-09-15T10:00:00+08:00"),
            sequence=1,
        ),
        tx(
            id="t2",
            amount_cents=10000,
            category="餐饮",
            occurred_at=at("2026-10-01T10:00:00+08:00"),
            sequence=2,
        ),
        tx(
            id="t3",
            amount_cents=3000,
            category="餐饮",
            occurred_at=at("2026-09-20T10:00:00+08:00"),
            sequence=3,
        ),
    )
    result = subject.spending_summary(state, customer_id="cus_a", month="2026-10")
    assert result["total_cents"] == 10000
    assert result["previous_total_cents"] == 8000
    # MoM: (10000 - 8000) / 8000 * 10000 = 2500 bp
    assert result["mom_change_bp"] == 2500
    cats = {c["category"]: c for c in result["categories"]}
    assert cats["餐饮"]["amount_cents"] == 10000
    assert cats["餐饮"]["previous_amount_cents"] == 3000
    assert cats["娱乐"]["amount_cents"] == 0
    assert cats["娱乐"]["previous_amount_cents"] == 5000


def test_daily_breakdown():
    state = with_txns(
        base_state(),
        tx(
            id="t1",
            amount_cents=1000,
            category="餐饮",
            occurred_at=at("2026-10-01T10:00:00+08:00"),
            sequence=1,
        ),
        tx(
            id="t2",
            amount_cents=2000,
            category="餐饮",
            occurred_at=at("2026-10-01T15:00:00+08:00"),
            sequence=2,
        ),
        tx(
            id="t3",
            amount_cents=3000,
            category="交通",
            occurred_at=at("2026-10-02T10:00:00+08:00"),
            sequence=3,
        ),
    )
    result = subject.spending_summary(state, customer_id="cus_a", month="2026-10")
    assert len(result["daily"]) == 2
    days = {d["date"]: d["amount_cents"] for d in result["daily"]}
    assert days[date(2026, 10, 1)] == 3000
    assert days[date(2026, 10, 2)] == 3000


def test_unknown_category_maps_to_other():
    state = with_txns(
        base_state(),
        tx(
            id="t1",
            amount_cents=5000,
            category="some_unknown_cat",
            occurred_at=at("2026-10-05T10:00:00+08:00"),
            sequence=1,
        ),
    )
    result = subject.spending_summary(state, customer_id="cus_a", month="2026-10")
    cats = {c["category"]: c for c in result["categories"]}
    assert "其他" in cats
    assert cats["其他"]["amount_cents"] == 5000


def test_mandate_counts_as_consumption():
    state = with_txns(
        base_state(),
        tx(
            id="t1",
            amount_cents=2000,
            kind="MANDATE",
            category="生活服务",
            occurred_at=at("2026-10-05T10:00:00+08:00"),
            sequence=1,
        ),
    )
    result = subject.spending_summary(state, customer_id="cus_a", month="2026-10")
    assert result["total_cents"] == 2000


def test_mom_change_negative():
    state = with_txns(
        base_state(),
        tx(
            id="t1",
            amount_cents=10000,
            category="购物",
            occurred_at=at("2026-09-10T10:00:00+08:00"),
            sequence=1,
        ),
        tx(
            id="t2",
            amount_cents=4000,
            category="购物",
            occurred_at=at("2026-10-10T10:00:00+08:00"),
            sequence=2,
        ),
    )
    result = subject.spending_summary(state, customer_id="cus_a", month="2026-10")
    assert result["total_cents"] == 4000
    assert result["previous_total_cents"] == 10000
    assert result["mom_change_cents"] == -6000
    assert result["mom_change_bp"] == -6000  # -60% = -6000 bp


def test_different_customer_transactions_excluded():
    state = with_txns(
        base_state(),
        tx(
            id="t1",
            amount_cents=5000,
            category="餐饮",
            occurred_at=at("2026-10-05T10:00:00+08:00"),
            sequence=1,
        ),
        tx(
            id="t2",
            customer_id="cus_b",
            account_id="acc_b",
            amount_cents=9999,
            category="餐饮",
            occurred_at=at("2026-10-05T10:00:00+08:00"),
            sequence=2,
        ),
    )
    result = subject.spending_summary(state, customer_id="cus_a", month="2026-10")
    assert result["total_cents"] == 5000


def test_year_crossing_boundary():
    """Month 2026-01 should not include 2025-12 transactions, but 2025-12 is prev month."""
    state = with_txns(
        base_state(),
        tx(
            id="t1",
            amount_cents=3000,
            category="餐饮",
            occurred_at=at("2025-12-31T23:00:00+08:00"),
            sequence=1,
        ),
        tx(
            id="t2",
            amount_cents=5000,
            category="交通",
            occurred_at=at("2026-01-01T01:00:00+08:00"),
            sequence=2,
        ),
    )
    result = subject.spending_summary(state, customer_id="cus_a", month="2026-01")
    assert result["total_cents"] == 5000
    assert result["previous_total_cents"] == 3000


# ─── detect_anomalies ───────────────────────────────────────────────


def _anomaly_tx(**updates) -> Transaction:
    """Minimal POSTED CARD_SPEND transaction for anomaly tests."""
    values = dict(
        id="tx_a1",
        transfer_id="xfr_a1",
        customer_id="cus_a",
        account_id="acc_a",
        direction="OUT",
        kind="CARD_SPEND",
        phase=Phase.POSTED,
        status="POSTED",
        amount_cents=50000,
        balance_after_cents=550000,
        available_after_cents=550000,
        counterparty="merchant",
        memo=None,
        category="餐饮",
        merchant_id="mer_1",
        payee_id=None,
        card_id="crd_1",
        channel=None,
        occurred_at=at("2026-10-15T12:00:00+08:00"),
        sequence=1,
    )
    values.update(updates)
    return Transaction(**values)


def _detect(state, customer_id="cus_a", from_at=None, to_at=None, account_id=None):
    """Shortcut for calling detect_anomalies."""
    if from_at is None:
        from_at = at("2026-09-01T00:00:00+08:00")
    if to_at is None:
        to_at = at("2026-11-01T00:00:00+08:00")
    return subject.detect_anomalies(
        state,
        customer_id=customer_id,
        from_at=from_at,
        to_at=to_at,
        account_id=account_id,
    )


def _codes(result, tx_id):
    """Return the sorted codes list for *tx_id* in an anomalies result."""
    for item in result["items"]:
        if item["transaction"]["id"] == tx_id:
            return sorted(item["codes"])
    return []


# ── ANOMALY_NIGHT_LARGE ──


def test_night_large_triggers():
    """Amount > 50,000 at 02:13 local time → flagged (night only; not new-merchant)."""
    state = with_txns(
        base_state(),
        _anomaly_tx(
            id="t_prior",
            amount_cents=5000,
            merchant_id="mer_night",
            occurred_at=at("2026-09-01T10:00:00+08:00"),
        ),
        _anomaly_tx(
            id="t1",
            amount_cents=128000,
            merchant_id="mer_night",
            occurred_at=at("2026-09-07T02:13:00+08:00"),
        ),
    )
    result = _detect(state, from_at=at("2026-09-01T00:00:00+08:00"))
    codes = _codes(result, "t1")
    assert "ANOMALY_NIGHT_LARGE" in codes
    assert "ANOMALY_NEW_MERCHANT_LARGE" not in codes  # prior tx at same merchant

    item = next(it for it in result["items"] if it["transaction"]["id"] == "t1")
    assert item["evidence"]["threshold_cents"] == 50000
    assert item["evidence"]["mean_cents"] is None


def test_night_large_at_boundary_amount_50000_not_triggered():
    """Exactly 50,000 is NOT > 50,000, so no flag."""
    state = with_txns(
        base_state(),
        _anomaly_tx(
            id="t1",
            amount_cents=50000,
            occurred_at=at("2026-09-07T02:13:00+08:00"),
        ),
    )
    result = _detect(state)
    assert _codes(result, "t1") == []


def test_night_large_not_at_night():
    """Same amount but at 12:00 → not flagged."""
    state = with_txns(
        base_state(),
        _anomaly_tx(
            id="t_prior",
            amount_cents=5000,
            merchant_id="mer_night",
            occurred_at=at("2026-09-01T10:00:00+08:00"),
        ),
        _anomaly_tx(
            id="t1",
            amount_cents=128000,
            merchant_id="mer_night",
            occurred_at=at("2026-09-07T12:00:00+08:00"),
        ),
    )
    result = _detect(state)
    assert _codes(result, "t1") == []


def test_night_large_at_0000_boundary():
    """00:00 is in the night window."""
    state = with_txns(
        base_state(),
        _anomaly_tx(
            id="t1",
            amount_cents=60000,
            occurred_at=at("2026-09-07T00:00:00+08:00"),
        ),
    )
    assert "ANOMALY_NIGHT_LARGE" in _codes(_detect(state), "t1")


def test_night_large_at_0500_boundary():
    """05:00 is NOT in the window (window is 00:00–04:59)."""
    state = with_txns(
        base_state(),
        _anomaly_tx(
            id="t1",
            amount_cents=60000,
            occurred_at=at("2026-09-07T05:00:00+08:00"),
        ),
    )
    assert _codes(_detect(state), "t1") == []


# ── ANOMALY_DUPLICATE ──


def test_duplicate_triggers_second_only():
    """Same card/merchant/amount within 5 min → second tx flagged, first not."""
    state = with_txns(
        base_state(),
        _anomaly_tx(
            id="t1",
            amount_cents=26800,
            merchant_id="mer_food",
            occurred_at=at("2026-09-15T12:00:00+08:00"),
        ),
        _anomaly_tx(
            id="t2",
            amount_cents=26800,
            merchant_id="mer_food",
            occurred_at=at("2026-09-15T12:04:00+08:00"),
        ),
    )
    result = _detect(state)
    assert _codes(result, "t1") == []  # first is clean
    assert "ANOMALY_DUPLICATE" in _codes(result, "t2")
    item = next(it for it in result["items"] if it["transaction"]["id"] == "t2")
    assert item["evidence"]["previous_transaction_id"] == "t1"
    assert item["evidence"]["window_minutes"] == 4


def test_duplicate_over_5_minutes_not_triggered():
    """Same card/merchant/amount but >5 min apart → no flag."""
    state = with_txns(
        base_state(),
        _anomaly_tx(
            id="t1",
            amount_cents=26800,
            merchant_id="mer_food",
            occurred_at=at("2026-09-15T12:00:00+08:00"),
        ),
        _anomaly_tx(
            id="t2",
            amount_cents=26800,
            merchant_id="mer_food",
            occurred_at=at("2026-09-15T12:06:00+08:00"),
        ),
    )
    result = _detect(state)
    assert _codes(result, "t2") == []


def test_duplicate_different_amount_not_triggered():
    """Same card/merchant but different amount → no flag."""
    state = with_txns(
        base_state(),
        _anomaly_tx(
            id="t1",
            amount_cents=26800,
            merchant_id="mer_food",
            occurred_at=at("2026-09-15T12:00:00+08:00"),
        ),
        _anomaly_tx(
            id="t2",
            amount_cents=30000,
            merchant_id="mer_food",
            occurred_at=at("2026-09-15T12:03:00+08:00"),
        ),
    )
    assert _codes(_detect(state), "t2") == []


def test_duplicate_no_card_id_not_triggered():
    """Duplicate check requires card_id."""
    state = with_txns(
        base_state(),
        _anomaly_tx(
            id="t1",
            amount_cents=26800,
            merchant_id="mer_food",
            card_id=None,
            occurred_at=at("2026-09-15T12:00:00+08:00"),
        ),
        _anomaly_tx(
            id="t2",
            amount_cents=26800,
            merchant_id="mer_food",
            card_id=None,
            occurred_at=at("2026-09-15T12:03:00+08:00"),
        ),
    )
    assert _codes(_detect(state), "t2") == []


def test_duplicate_no_merchant_id_not_triggered():
    """Duplicate check requires merchant_id."""
    state = with_txns(
        base_state(),
        _anomaly_tx(
            id="t1",
            amount_cents=26800,
            merchant_id=None,
            occurred_at=at("2026-09-15T12:00:00+08:00"),
        ),
        _anomaly_tx(
            id="t2",
            amount_cents=26800,
            merchant_id=None,
            occurred_at=at("2026-09-15T12:03:00+08:00"),
        ),
    )
    assert _codes(_detect(state), "t2") == []


# ── ANOMALY_NEW_MERCHANT_LARGE ──


def test_new_merchant_large_triggers():
    """First transaction at merchant, >100,000 → flagged."""
    state = with_txns(
        base_state(),
        _anomaly_tx(
            id="t1",
            amount_cents=349900,
            merchant_id="mer_new",
            occurred_at=at("2026-09-22T10:00:00+08:00"),
        ),
    )
    result = _detect(state)
    assert "ANOMALY_NEW_MERCHANT_LARGE" in _codes(result, "t1")
    item = next(it for it in result["items"] if it["transaction"]["id"] == "t1")
    assert item["evidence"]["threshold_cents"] == 100000


def test_new_merchant_not_large_enough():
    """First transaction at merchant but ≤100,000 → not flagged."""
    state = with_txns(
        base_state(),
        _anomaly_tx(
            id="t1",
            amount_cents=100000,
            merchant_id="mer_new",
            occurred_at=at("2026-09-22T10:00:00+08:00"),
        ),
    )
    assert _codes(_detect(state), "t1") == []


def test_new_merchant_has_history():
    """Prior tx at same merchant within 90 days → not new."""
    state = with_txns(
        base_state(),
        _anomaly_tx(
            id="t1",
            amount_cents=5000,
            merchant_id="mer_old",
            occurred_at=at("2026-09-15T10:00:00+08:00"),
        ),
        _anomaly_tx(
            id="t2",
            amount_cents=200000,
            merchant_id="mer_old",
            occurred_at=at("2026-09-22T10:00:00+08:00"),
        ),
    )
    assert _codes(_detect(state), "t2") == []


def test_new_merchant_history_beyond_90_days():
    """Prior tx at same merchant but >90 days ago → still 'new'."""
    state = with_txns(
        base_state(),
        _anomaly_tx(
            id="t1",
            amount_cents=5000,
            merchant_id="mer_old",
            occurred_at=at("2026-06-01T10:00:00+08:00"),  # >90 days before t2
        ),
        _anomaly_tx(
            id="t2",
            amount_cents=200000,
            merchant_id="mer_old",
            occurred_at=at("2026-09-22T10:00:00+08:00"),
        ),
    )
    assert "ANOMALY_NEW_MERCHANT_LARGE" in _codes(_detect(state), "t2")


def test_new_merchant_no_merchant_id():
    """Transaction without merchant_id cannot be new-merchant-large."""
    state = with_txns(
        base_state(),
        _anomaly_tx(
            id="t1",
            amount_cents=200000,
            merchant_id=None,
            occurred_at=at("2026-09-22T10:00:00+08:00"),
        ),
    )
    assert _codes(_detect(state), "t1") == []


# ── ANOMALY_CATEGORY_OUTLIER ──


def test_category_outlier_triggers():
    """≥10 prior same-category txns, tx > μ + 3σ → flagged."""
    txns = []
    # 10 prior "餐饮" txns averaging ~10000 each, low variance
    for i in range(10):
        txns.append(
            _anomaly_tx(
                id=f"p{i}",
                amount_cents=10000 + i * 10,  # 10000..10090, tight
                category="餐饮",
                occurred_at=at(f"2026-10-{(i + 1):02d}T10:00:00+08:00"),
            )
        )
    # Outlier: way above
    txns.append(
        _anomaly_tx(
            id="t_out",
            amount_cents=50000,  # well above μ+3σ (~10500)
            category="餐饮",
            occurred_at=at("2026-10-15T10:00:00+08:00"),
        )
    )
    state = with_txns(base_state(), *txns)
    result = _detect(state)
    assert "ANOMALY_CATEGORY_OUTLIER" in _codes(result, "t_out")
    item = next(it for it in result["items"] if it["transaction"]["id"] == "t_out")
    assert item["evidence"]["baseline_count"] == 10
    assert item["evidence"]["mean_cents"] is not None
    assert item["evidence"]["threshold_cents"] is not None


def test_category_outlier_not_enough_history():
    """9 prior same-category txns → not flagged (<10 required)."""
    txns = []
    for i in range(9):
        txns.append(
            _anomaly_tx(
                id=f"p{i}",
                amount_cents=10000,
                category="餐饮",
                occurred_at=at(f"2026-10-{(i + 1):02d}T10:00:00+08:00"),
            )
        )
    txns.append(
        _anomaly_tx(
            id="t_out",
            amount_cents=50000,
            category="餐饮",
            occurred_at=at("2026-10-15T10:00:00+08:00"),
        )
    )
    state = with_txns(base_state(), *txns)
    assert _codes(_detect(state), "t_out") == []


def test_category_outlier_within_bounds():
    """≥10 prior but tx amount is within μ+3σ → not flagged."""
    txns = []
    for i in range(10):
        txns.append(
            _anomaly_tx(
                id=f"p{i}",
                amount_cents=10000 + i * 10,  # 10000..10090, tight
                category="餐饮",
                occurred_at=at(f"2026-10-{(i + 1):02d}T10:00:00+08:00"),
            )
        )
    txns.append(
        _anomaly_tx(
            id="t_normal",
            amount_cents=10010,  # within bounds (μ≈10045, σ≈29, μ+3σ≈10132)
            category="餐饮",
            occurred_at=at("2026-10-15T10:00:00+08:00"),
        )
    )
    state = with_txns(base_state(), *txns)
    assert _codes(_detect(state), "t_normal") == []


def test_category_outlier_history_bounded_by_90_days():
    """Prior txns >90 days old should not count toward baseline."""
    txns = []
    # 5 recent + 5 old (far enough) = only 5 count as baseline → not ≥10
    for i in range(5):
        txns.append(
            _anomaly_tx(
                id=f"recent{i}",
                amount_cents=10000,
                category="餐饮",
                occurred_at=at(f"2026-10-{(i + 1):02d}T10:00:00+08:00"),
            )
        )
    for i in range(5):
        txns.append(
            _anomaly_tx(
                id=f"old{i}",
                amount_cents=10000,
                category="餐饮",
                occurred_at=at("2026-06-01T10:00:00+08:00"),
            )
        )
    txns.append(
        _anomaly_tx(
            id="t_out",
            amount_cents=50000,
            category="餐饮",
            occurred_at=at("2026-10-15T10:00:00+08:00"),
        )
    )
    state = with_txns(base_state(), *txns)
    assert _codes(_detect(state), "t_out") == []


def test_category_outlier_different_category_not_counted():
    """Only same-category prior txns count."""
    txns = []
    for i in range(10):
        txns.append(
            _anomaly_tx(
                id=f"p{i}",
                amount_cents=10000,
                category="交通",
                occurred_at=at(f"2026-10-{(i + 1):02d}T10:00:00+08:00"),
            )
        )
    txns.append(
        _anomaly_tx(
            id="t_out",
            amount_cents=50000,
            category="餐饮",
            occurred_at=at("2026-10-15T10:00:00+08:00"),
        )
    )
    state = with_txns(base_state(), *txns)
    assert _codes(_detect(state), "t_out") == []


# ── multi-signal / merge ──


def test_multiple_signals_merged():
    """A night-time large tx at a new merchant → both codes on one item."""
    state = with_txns(
        base_state(),
        _anomaly_tx(
            id="t1",
            amount_cents=200000,
            merchant_id="mer_night_new",
            occurred_at=at("2026-09-07T02:13:00+08:00"),
        ),
    )
    result = _detect(state)
    codes = _codes(result, "t1")
    assert "ANOMALY_NIGHT_LARGE" in codes
    assert "ANOMALY_NEW_MERCHANT_LARGE" in codes


# ── no future information leak ──


def test_no_future_information_leak():
    """A tx in the candidate window must not see later txns."""
    # t1 is first-ever at merchant_old, small amount → clean
    # t2 is large amount at same merchant → should NOT be "new" because t1 exists
    # But t1 should NOT see t2's info (and t1 is clean anyway)
    state = with_txns(
        base_state(),
        _anomaly_tx(
            id="t1",
            amount_cents=5000,
            merchant_id="mer_both",
            occurred_at=at("2026-10-10T10:00:00+08:00"),
        ),
        _anomaly_tx(
            id="t2",
            amount_cents=200000,
            merchant_id="mer_both",
            occurred_at=at("2026-10-15T10:00:00+08:00"),
        ),
    )
    result = _detect(state)
    # t1: not large, not new (the later t2 doesn't affect t1's check)
    assert _codes(result, "t1") == []
    # t2: large but merchant is not new (t1 existed)
    assert _codes(result, "t2") == []


# ── empty / no candidates ──


def test_detect_anomalies_empty_range():
    """No transactions in range → empty result."""
    result = _detect(
        base_state(),
        from_at=at("2026-01-01T00:00:00+08:00"),
        to_at=at("2026-01-02T00:00:00+08:00"),
    )
    assert result["items"] == ()


def test_detect_anomalies_wrong_customer():
    """Transactions belong to cus_b, not cus_a."""
    state = with_txns(
        base_state(),
        _anomaly_tx(
            id="t1",
            customer_id="cus_b",
            account_id="acc_b",
            amount_cents=128000,
            occurred_at=at("2026-09-07T02:13:00+08:00"),
        ),
    )
    assert _detect(state)["items"] == ()


# ── account_id filter ──


def test_detect_anomalies_account_filter():
    """Filter by account_id — only anomalies from that account."""
    state = with_account(base_state(), "acc_s", customer_id="cus_a")
    state = with_txns(
        state,
        _anomaly_tx(
            id="t_checked",
            account_id="acc_a",
            amount_cents=128000,
            merchant_id="mer_night",
            occurred_at=at("2026-09-07T02:13:00+08:00"),
        ),
        _anomaly_tx(
            id="t_sav",
            account_id="acc_s",
            amount_cents=200000,
            merchant_id="mer_new_sav",
            occurred_at=at("2026-09-07T03:00:00+08:00"),
        ),
    )
    filtered = _detect(state, account_id="acc_a")
    ids = {it["transaction"]["id"] for it in filtered["items"]}
    assert ids == {"t_checked"}
    assert "t_sav" not in ids


# ── business timezone (UTC sim_now → Asia/Shanghai business tz) ──

UTC_NOW = datetime(2026, 11, 15, 9, 0, 0, tzinfo=_UTC)


def _base_state_utc():
    """base_state variant with UTC sim_now."""
    s = base_state()
    return s.model_copy(update={"sim_now": UTC_NOW})


def test_spending_summary_utc_sim_now_october_boundary():
    """Transaction at Beijing 10-31 23:30 (15:30Z) → counted in 2026-10."""
    state = with_txns(
        _base_state_utc(),
        tx(
            id="t_oct31",
            amount_cents=5000,
            category="餐饮",
            occurred_at=utc_at(2026, 10, 31, 15, 30),
            sequence=1,
        ),
    )
    result = subject.spending_summary(state, customer_id="cus_a", month="2026-10")
    assert result["total_cents"] == 5000


def test_spending_summary_utc_sim_now_november_boundary():
    """Beijing 11-01 00:30 (10-31 16:30Z) → counted in 2026-11, not 2026-10."""
    utc_tx = tx(
        id="t_nov01",
        amount_cents=8000,
        category="购物",
        occurred_at=utc_at(2026, 10, 31, 16, 30),
        sequence=1,
    )
    state_nov = with_txns(_base_state_utc(), utc_tx)
    result_nov = subject.spending_summary(state_nov, customer_id="cus_a", month="2026-11")
    assert result_nov["total_cents"] == 8000

    result_oct = subject.spending_summary(state_nov, customer_id="cus_a", month="2026-10")
    assert result_oct["total_cents"] == 0


def test_anomaly_night_large_uses_business_timezone():
    """18:13Z (Beijing 02:13) → ANOMALY_NIGHT_LARGE; 14:00Z (Beijing 22:00) → not."""
    state = with_txns(
        _base_state_utc(),
        _anomaly_tx(
            id="t_night",
            amount_cents=60000,
            merchant_id="mer_a",
            occurred_at=utc_at(2026, 10, 15, 18, 13),
        ),
        _anomaly_tx(
            id="t_day",
            amount_cents=60000,
            merchant_id="mer_a",
            occurred_at=utc_at(2026, 10, 15, 14, 0),
        ),
    )
    from_at = utc_at(2026, 10, 1)
    to_at = utc_at(2026, 11, 1)
    result = subject.detect_anomalies(state, customer_id="cus_a", from_at=from_at, to_at=to_at)
    assert "ANOMALY_NIGHT_LARGE" in _codes(result, "t_night")
    assert _codes(result, "t_day") == []


# ── R9 detect_subscriptions ────────────────────────────────────────────


def _sub_tx(merchant_id, occurred_at, amount_cents, id_suffix="", kind="MANDATE", **kw):
    """Minimal MANDATE transaction for subscription detection."""
    return Transaction(
        id=f"stx_{merchant_id}_{id_suffix or occurred_at.replace('-','').replace('T','').replace(':','')}",
        transfer_id=f"xfr_{id_suffix or 's'}",
        customer_id="cus_a",
        account_id="acc_a",
        direction="OUT",
        kind=kind,
        phase=Phase.POSTED,
        status="POSTED",
        amount_cents=amount_cents,
        balance_after_cents=600_000 - amount_cents,
        available_after_cents=600_000 - amount_cents,
        counterparty="",
        memo=None,
        category="",
        merchant_id=merchant_id,
        payee_id=None,
        card_id=None,
        channel=None,
        occurred_at=occurred_at,
        sequence=1,
    )


def _subs_result(items):
    """Convert a list of dicts to a {merchant_id: dict} lookup with sorted keys."""
    return {i["merchant_id"]: i for i in items}


# ── Recognition: minimum occurrences ──────────────────────────────────


def test_r9_three_monthly_charges_detected():
    """Three charges ~30 days apart → MONTHLY, median 19900."""
    state = with_txns(
        base_state(),
        _sub_tx("mer_gym", at("2026-07-25T06:00:00+08:00"), 19900, "1"),
        _sub_tx("mer_gym", at("2026-08-25T06:00:00+08:00"), 19900, "2"),
        _sub_tx("mer_gym", at("2026-09-25T06:00:00+08:00"), 19900, "3"),
    )
    result = subject.detect_subscriptions(state, customer_id="cus_a", lookback_days=180)
    items = result["items"]
    assert len(items) == 1
    item = items[0]
    assert item["merchant_id"] == "mer_gym"
    assert item["period"] == "MONTHLY"
    assert item["typical_amount_cents"] == 19900
    assert item["occurrences"] == 3


def test_r9_two_charges_not_detected():
    """Only 2 charges at merchant → below min_occurrences."""
    state = with_txns(
        base_state(),
        _sub_tx("mer_gym", at("2026-07-25T06:00:00+08:00"), 19900, "1"),
        _sub_tx("mer_gym", at("2026-08-25T06:00:00+08:00"), 19900, "2"),
    )
    result = subject.detect_subscriptions(state, customer_id="cus_a", lookback_days=180)
    assert len(result["items"]) == 0


# ── Interval: 28/29/30/31 day boundaries ─────────────────────────────


def test_r9_28_day_interval_detected_monthly():
    """Three charges each 28 days apart → MONTHLY (within 28-31 range)."""
    state = with_txns(
        base_state(),
        _sub_tx("mer_v", at("2026-07-01T06:00:00+08:00"), 3000, "1"),
        _sub_tx("mer_v", at("2026-07-29T06:00:00+08:00"), 3000, "2"),
        _sub_tx("mer_v", at("2026-08-26T06:00:00+08:00"), 3000, "3"),
    )
    result = subject.detect_subscriptions(state, customer_id="cus_a", lookback_days=180)
    assert len(result["items"]) == 1
    assert result["items"][0]["period"] == "MONTHLY"


def test_r9_31_day_interval_detected_monthly():
    """Three charges each 31 days apart → MONTHLY (within 28-31 range)."""
    state = with_txns(
        base_state(),
        _sub_tx("mer_v", at("2026-07-01T06:00:00+08:00"), 3000, "1"),
        _sub_tx("mer_v", at("2026-08-01T06:00:00+08:00"), 3000, "2"),
        _sub_tx("mer_v", at("2026-09-01T06:00:00+08:00"), 3000, "3"),
    )
    result = subject.detect_subscriptions(state, customer_id="cus_a", lookback_days=180)
    assert len(result["items"]) == 1
    assert result["items"][0]["period"] == "MONTHLY"


def test_r9_29_day_interval_detected_monthly():
    """Three charges each 29 days apart → MONTHLY."""
    state = with_txns(
        base_state(),
        _sub_tx("mer_v", at("2026-07-03T06:00:00+08:00"), 3000, "1"),
        _sub_tx("mer_v", at("2026-08-01T06:00:00+08:00"), 3000, "2"),
        _sub_tx("mer_v", at("2026-08-30T06:00:00+08:00"), 3000, "3"),
    )
    result = subject.detect_subscriptions(state, customer_id="cus_a", lookback_days=180)
    assert len(result["items"]) == 1
    assert result["items"][0]["period"] == "MONTHLY"


def test_r9_27_day_interval_not_detected():
    """Three charges 27 days apart → NOT monthly (below 28)."""
    state = with_txns(
        base_state(),
        _sub_tx("mer_v", at("2026-07-01T06:00:00+08:00"), 3000, "1"),
        _sub_tx("mer_v", at("2026-07-28T06:00:00+08:00"), 3000, "2"),
        _sub_tx("mer_v", at("2026-08-24T06:00:00+08:00"), 3000, "3"),
    )
    result = subject.detect_subscriptions(state, customer_id="cus_a", lookback_days=180)
    assert len(result["items"]) == 0


def test_r9_32_day_interval_not_detected():
    """Three charges 32 days apart → NOT monthly (above 31)."""
    state = with_txns(
        base_state(),
        _sub_tx("mer_v", at("2026-07-01T06:00:00+08:00"), 3000, "1"),
        _sub_tx("mer_v", at("2026-08-02T06:00:00+08:00"), 3000, "2"),
        _sub_tx("mer_v", at("2026-09-03T06:00:00+08:00"), 3000, "3"),
    )
    result = subject.detect_subscriptions(state, customer_id="cus_a", lookback_days=180)
    assert len(result["items"]) == 0


# ── Weekly detection ─────────────────────────────────────────────────


def test_r9_weekly_charges_detected():
    """Three charges exactly 7 days apart → WEEKLY."""
    state = with_txns(
        base_state(),
        _sub_tx("mer_w", at("2026-09-22T09:00:00+08:00"), 1500, "1"),
        _sub_tx("mer_w", at("2026-09-29T09:00:00+08:00"), 1500, "2"),
        _sub_tx("mer_w", at("2026-10-06T09:00:00+08:00"), 1500, "3"),
    )
    result = subject.detect_subscriptions(state, customer_id="cus_a", lookback_days=180)
    assert len(result["items"]) == 1
    assert result["items"][0]["period"] == "WEEKLY"


# ── Non-periodic merchant not detected ───────────────────────────────


def test_r9_irregular_intervals_not_detected():
    """Charges at irregular intervals (10, 25 days) → not periodic."""
    state = with_txns(
        base_state(),
        _sub_tx("mer_x", at("2026-07-05T06:00:00+08:00"), 5000, "1"),
        _sub_tx("mer_x", at("2026-07-15T06:00:00+08:00"), 5000, "2"),  # 10 days
        _sub_tx("mer_x", at("2026-07-30T06:00:00+08:00"), 5000, "3"),  # 15 days
    )
    result = subject.detect_subscriptions(state, customer_id="cus_a", lookback_days=180)
    assert len(result["items"]) == 0


# ── Amount variance ──────────────────────────────────────────────────


def test_r9_amount_within_5pct_variance():
    """Amounts 19900, 19400, 20400 all within 5% of median 19900."""
    state = with_txns(
        base_state(),
        _sub_tx("mer_gym", at("2026-07-25T06:00:00+08:00"), 19900, "1"),
        _sub_tx("mer_gym", at("2026-08-25T06:00:00+08:00"), 19400, "2"),  # -2.5%
        _sub_tx("mer_gym", at("2026-09-25T06:00:00+08:00"), 20400, "3"),  # +2.5%
    )
    result = subject.detect_subscriptions(state, customer_id="cus_a", lookback_days=180)
    assert len(result["items"]) == 1
    assert result["items"][0]["typical_amount_cents"] == 19900


def test_r9_amount_exceeds_5pct_variance():
    """Amount 25000 vs 19900 median → 30% variance → not detected."""
    state = with_txns(
        base_state(),
        _sub_tx("mer_gym", at("2026-07-25T06:00:00+08:00"), 19900, "1"),
        _sub_tx("mer_gym", at("2026-08-25T06:00:00+08:00"), 19900, "2"),
        _sub_tx("mer_gym", at("2026-09-25T06:00:00+08:00"), 25000, "3"),
    )
    result = subject.detect_subscriptions(state, customer_id="cus_a", lookback_days=180)
    assert len(result["items"]) == 0


def test_r9_amount_exact_5pct_boundary():
    """19900 ± 5% = 19900 ± 995 → 20895 (exactly 5%) → included."""
    state = with_txns(
        base_state(),
        _sub_tx("mer_gym", at("2026-07-25T06:00:00+08:00"), 19900, "1"),
        _sub_tx("mer_gym", at("2026-08-25T06:00:00+08:00"), 19900, "2"),
        _sub_tx("mer_gym", at("2026-09-25T06:00:00+08:00"), 20895, "3"),
    )
    result = subject.detect_subscriptions(state, customer_id="cus_a", lookback_days=180)
    assert len(result["items"]) == 1


def test_r9_amount_slightly_over_5pct_boundary():
    """19900 + 5% = 20895 → 20896 (over boundary) → rejected."""
    state = with_txns(
        base_state(),
        _sub_tx("mer_gym", at("2026-07-25T06:00:00+08:00"), 19900, "1"),
        _sub_tx("mer_gym", at("2026-08-25T06:00:00+08:00"), 19900, "2"),
        _sub_tx("mer_gym", at("2026-09-25T06:00:00+08:00"), 20896, "3"),
    )
    result = subject.detect_subscriptions(state, customer_id="cus_a", lookback_days=180)
    assert len(result["items"]) == 0


# ── Cancelled MANDATE still detected from transactions ─────────────────


def test_r9_detects_from_transactions_not_instruction_status():
    """Subscription is detected from recurring transactions even if mandate
    is DELETED (the charge history still exists)."""
    state = with_txns(
        base_state(),
        _sub_tx("mer_gym", at("2026-07-25T06:00:00+08:00"), 19900, "1"),
        _sub_tx("mer_gym", at("2026-08-25T06:00:00+08:00"), 19900, "2"),
        _sub_tx("mer_gym", at("2026-09-25T06:00:00+08:00"), 19900, "3"),
    )
    # State has no subscriptions → detected from transactions only
    result = subject.detect_subscriptions(state, customer_id="cus_a", lookback_days=180)
    assert len(result["items"]) == 1
    # instruction_id/billing_id absent because no matching subscription
    assert result["items"][0]["instruction_id"] is None
    assert result["items"][0]["billing_id"] is None


# ── Other customer transactions excluded ──────────────────────────────


def test_r9_other_customer_transactions_excluded():
    """cus_b's charges at same merchant do not affect cus_a's detection."""
    tx_cus_b = _sub_tx("mer_gym", at("2026-07-25T06:00:00+08:00"), 19900, "b1")
    tx_cus_b = tx_cus_b.model_copy(update={"customer_id": "cus_b", "account_id": "acc_b"})
    state = with_txns(
        base_state(),
        _sub_tx("mer_gym", at("2026-07-25T06:00:00+08:00"), 19900, "1"),
        _sub_tx("mer_gym", at("2026-08-25T06:00:00+08:00"), 19900, "2"),
        tx_cus_b,  # cus_b charge at same merchant, not counted
    )
    result = subject.detect_subscriptions(state, customer_id="cus_a", lookback_days=180)
    # cus_a only has 2 charges → below threshold
    assert len(result["items"]) == 0


def test_r9_cus_b_three_charges_detected():
    """cus_b can independently detect subscriptions on its own accounts."""
    tx1 = _sub_tx("mer_gym", at("2026-07-25T06:00:00+08:00"), 19900, "b1")
    tx2 = _sub_tx("mer_gym", at("2026-08-25T06:00:00+08:00"), 19900, "b2")
    tx3 = _sub_tx("mer_gym", at("2026-09-25T06:00:00+08:00"), 19900, "b3")
    txns = [
        t.model_copy(update={"customer_id": "cus_b", "account_id": "acc_b"})
        for t in (tx1, tx2, tx3)
    ]
    state = with_txns(base_state(), *txns)
    result = subject.detect_subscriptions(state, customer_id="cus_b", lookback_days=180)
    assert len(result["items"]) == 1
    assert result["items"][0]["merchant_id"] == "mer_gym"


# ── lookback_days boundary ────────────────────────────────────────────


def test_r9_lookback_excludes_old_charges():
    """Only 2 charges in last 60 days, 3 in last 180 → detected at 180, not at 60."""
    state = with_txns(
        base_state(),
        _sub_tx("mer_gym", at("2026-07-25T06:00:00+08:00"), 19900, "1"),  # ~82 days ago
        _sub_tx("mer_gym", at("2026-08-25T06:00:00+08:00"), 19900, "2"),  # ~51 days ago
        _sub_tx("mer_gym", at("2026-09-25T06:00:00+08:00"), 19900, "3"),  # ~20 days ago
    )
    result_60 = subject.detect_subscriptions(state, customer_id="cus_a", lookback_days=60)
    assert len(result_60["items"]) == 0  # only 2 charges in 60d
    result_180 = subject.detect_subscriptions(state, customer_id="cus_a", lookback_days=180)
    assert len(result_180["items"]) == 1


# ── Idle detection (needs obligations for usage_expected) ──────────────


def _state_with_obligation(state, merchant_id="mer_gym", usage_expected=True):
    """Attach an obligations snapshot with the given merchant to the state."""
    from safebank_contracts.obligations import Merchant, ObligationSnapshot

    merchants = {
        merchant_id: Merchant(
            id=merchant_id,
            name="测试商户",
            mcc="7997",
            category="健身",
            usage_expected=usage_expected,
        )
    }
    snapshot = ObligationSnapshot(
        covered_customer_ids=("cus_a",),
        merchants=merchants,
        billings={},
        standing_instructions={},
        runs=(),
        instruction_targets={},
    )
    return state.model_copy(update={"obligations": snapshot})


def test_r9_idle_when_no_recent_usage_and_previous_has():
    """usage_expected=True, recent=0, previous>0 → is_idle=True."""
    state = with_txns(
        _state_with_obligation(base_state()),
        # 3 monthly charges
        _sub_tx("mer_gym", at("2026-07-25T06:00:00+08:00"), 19900, "1"),
        _sub_tx("mer_gym", at("2026-08-25T06:00:00+08:00"), 19900, "2"),
        _sub_tx("mer_gym", at("2026-09-25T06:00:00+08:00"), 19900, "3"),
        # 1 CARD_SPEND visit 90 days ago (in the 60-120d window)
        _sub_tx(
            "mer_gym",
            at("2026-07-17T18:00:00+08:00"),
            5000,
            "visit1",
            kind="CARD_SPEND",
        ),
    )
    result = subject.detect_subscriptions(state, customer_id="cus_a", lookback_days=180)
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["is_idle"] is True
    assert item["usage_evidence"]["recent_usage_count"] == 0
    assert item["usage_evidence"]["previous_usage_count"] >= 1


def test_r9_not_idle_when_recent_usage_present():
    """usage_expected=True, recent>0 → is_idle=False."""
    state = with_txns(
        _state_with_obligation(base_state()),
        # 3 monthly charges
        _sub_tx("mer_gym", at("2026-07-25T06:00:00+08:00"), 19900, "1"),
        _sub_tx("mer_gym", at("2026-08-25T06:00:00+08:00"), 19900, "2"),
        _sub_tx("mer_gym", at("2026-09-25T06:00:00+08:00"), 19900, "3"),
        # Recent visit (within 60 days)
        _sub_tx(
            "mer_gym",
            at("2026-10-01T18:00:00+08:00"),
            5000,
            "visit_recent",
            kind="CARD_SPEND",
        ),
    )
    result = subject.detect_subscriptions(state, customer_id="cus_a", lookback_days=180)
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["is_idle"] is False
    assert item["usage_evidence"]["recent_usage_count"] >= 1


def test_r9_not_idle_when_usage_expected_false():
    """usage_expected=False → never idle (e.g., video subscription)."""
    state = with_txns(
        _state_with_obligation(base_state(), merchant_id="mer_v", usage_expected=False),
        _sub_tx("mer_v", at("2026-07-18T06:00:00+08:00"), 3000, "1"),
        _sub_tx("mer_v", at("2026-08-18T06:00:00+08:00"), 3000, "2"),
        _sub_tx("mer_v", at("2026-09-18T06:00:00+08:00"), 3000, "3"),
    )
    result = subject.detect_subscriptions(state, customer_id="cus_a", lookback_days=180)
    assert len(result["items"]) == 1
    assert result["items"][0]["is_idle"] is False


# ── Multiple merchants ────────────────────────────────────────────────


def test_r9_multiple_subscriptions_detected():
    """Two merchants both have recurring charges → two items."""
    state = with_txns(
        base_state(),
        _sub_tx("mer_gym", at("2026-07-25T06:00:00+08:00"), 19900, "g1"),
        _sub_tx("mer_gym", at("2026-08-25T06:00:00+08:00"), 19900, "g2"),
        _sub_tx("mer_gym", at("2026-09-25T06:00:00+08:00"), 19900, "g3"),
        _sub_tx("mer_v", at("2026-07-18T06:00:00+08:00"), 3000, "v1"),
        _sub_tx("mer_v", at("2026-08-18T06:00:00+08:00"), 3000, "v2"),
        _sub_tx("mer_v", at("2026-09-18T06:00:00+08:00"), 3000, "v3"),
    )
    result = subject.detect_subscriptions(state, customer_id="cus_a", lookback_days=180)
    mids = {i["merchant_id"] for i in result["items"]}
    assert mids == {"mer_gym", "mer_v"}


# ── Annual cost ───────────────────────────────────────────────────────


def test_r9_annual_cost_monthly():
    """MONTHLY 19900 → annual = 19900 × 12 = 238800."""
    state = with_txns(
        base_state(),
        _sub_tx("mer_gym", at("2026-07-25T06:00:00+08:00"), 19900, "1"),
        _sub_tx("mer_gym", at("2026-08-25T06:00:00+08:00"), 19900, "2"),
        _sub_tx("mer_gym", at("2026-09-25T06:00:00+08:00"), 19900, "3"),
    )
    result = subject.detect_subscriptions(state, customer_id="cus_a", lookback_days=180)
    assert result["items"][0]["annual_cost_cents"] == 238800


def test_r9_annual_cost_weekly():
    """WEEKLY 1500 → annual = 1500 × 52 = 78000."""
    state = with_txns(
        base_state(),
        _sub_tx("mer_w", at("2026-09-22T09:00:00+08:00"), 1500, "1"),
        _sub_tx("mer_w", at("2026-09-29T09:00:00+08:00"), 1500, "2"),
        _sub_tx("mer_w", at("2026-10-06T09:00:00+08:00"), 1500, "3"),
    )
    result = subject.detect_subscriptions(state, customer_id="cus_a", lookback_days=180)
    assert result["items"][0]["annual_cost_cents"] == 78000


# ── Mixed interval (monthly + one 28d) is still MONTHLY ───────────────


def test_r9_mixed_monthly_intervals_still_monthly():
    """Intervals 30, 28, 31 → all within 28-31 → MONTHLY."""
    state = with_txns(
        base_state(),
        _sub_tx("mer_gym", at("2026-07-25T06:00:00+08:00"), 19900, "1"),
        _sub_tx("mer_gym", at("2026-08-24T06:00:00+08:00"), 19900, "2"),  # 30 days
        _sub_tx("mer_gym", at("2026-09-21T06:00:00+08:00"), 19900, "3"),  # 28 days
        _sub_tx("mer_gym", at("2026-10-22T06:00:00+08:00"), 19900, "4"),  # 31 days
    )
    result = subject.detect_subscriptions(state, customer_id="cus_a", lookback_days=180)
    assert len(result["items"]) == 1
    assert result["items"][0]["period"] == "MONTHLY"


# ── MANDATE charges excluded from usage evidence ─────────────────────


def test_r9_mandate_not_counted_as_usage():
    """MANDATE transactions are NOT counted in usage evidence (only CARD_SPEND)."""
    state = with_txns(
        _state_with_obligation(base_state()),
        # 3 monthly MANDATE charges
        _sub_tx("mer_gym", at("2026-06-25T06:00:00+08:00"), 19900, "1"),
        _sub_tx("mer_gym", at("2026-07-25T06:00:00+08:00"), 19900, "2"),
        _sub_tx("mer_gym", at("2026-08-25T06:00:00+08:00"), 19900, "3"),
        # Recent MANDATE (not CARD_SPEND) at the next monthly interval: still
        # part of the charge series, must NOT count as usage.
        _sub_tx(
            "mer_gym",
            at("2026-09-25T06:00:00+08:00"),
            19900,
            "recent_charge",
            kind="MANDATE",
        ),
        # Previous CARD_SPEND visit in 60-120d window
        _sub_tx(
            "mer_gym",
            at("2026-07-17T18:00:00+08:00"),
            5000,
            "old_visit",
            kind="CARD_SPEND",
        ),
    )
    result = subject.detect_subscriptions(state, customer_id="cus_a", lookback_days=180)
    item = result["items"][0]
    assert item["is_idle"] is True
    assert item["usage_evidence"]["recent_usage_count"] == 0
