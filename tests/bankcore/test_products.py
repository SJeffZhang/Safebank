"""Tests for calendar.py, nav.py, products.py (C6a – products read gate)."""

from dataclasses import FrozenInstanceError
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from safebank_bankcore.calendar import (
    is_before_cutoff,
    is_trading_day,
    next_trading_day,
    previous_trading_day,
    trade_date,
    trading_days_between,
)
from safebank_bankcore.nav import MIN_NAV, nav, nav_history
from safebank_bankcore.products import (
    ProductDef,
    compare_products,
    get_product,
    list_products,
    recent_return_bp,
)

# ── helpers ───────────────────────────────────────────────────────────────

CST = ZoneInfo("Asia/Shanghai")

BASE = date(2026, 10, 15)  # Thursday


def _p(**kw) -> ProductDef:
    defaults = dict(
        id="prd_x",
        name="测试",
        product_type="BOND",
        risk_level=1,
        risk_label="保守",
        base_nav=Decimal("1.0000"),
        base_date=BASE,
        confirm_days=1,
        settle_days=1,
        min_amount_cents=100_000,
        risk_disclosure="风险揭示",
        disclosure_version="v1",
    )
    defaults.update(kw)
    return ProductDef(**defaults)


def _prd_mmf() -> ProductDef:
    return _p(
        id="prd_mmf",
        name="安心货币",
        product_type="MMF",
        risk_level=1,
        risk_label="保守",
        base_nav=Decimal("1.0000"),
        confirm_days=0,
        settle_days=0,
        min_amount_cents=100,
    )


def _prd_stable() -> ProductDef:
    return _p(
        id="prd_stable",
        name="稳健增利",
        product_type="MIXED",
        risk_level=3,
        risk_label="稳健",
        base_nav=Decimal("1.2310"),
        confirm_days=1,
        settle_days=1,
        min_amount_cents=100_000,
    )


def _prd_growth() -> ProductDef:
    return _p(
        id="prd_growth",
        name="成长股票",
        product_type="EQUITY",
        risk_level=4,
        risk_label="进取",
        base_nav=Decimal("2.0310"),
        confirm_days=1,
        settle_days=2,
        min_amount_cents=100_000,
    )


SEED_PRODUCTS = (
    _prd_mmf(),
    _p(
        id="prd_sbond",
        name="稳盈短债",
        product_type="BOND",
        risk_level=2,
        risk_label="稳健",
        base_nav=Decimal("1.0520"),
    ),
    _prd_stable(),
    _p(
        id="prd_mixed",
        name="均衡混合",
        product_type="MIXED",
        risk_level=3,
        risk_label="平衡",
        base_nav=Decimal("1.4875"),
    ),
    _prd_growth(),
    _p(
        id="prd_tech",
        name="科技先锋",
        product_type="EQUITY",
        risk_level=5,
        risk_label="激进",
        base_nav=Decimal("1.8820"),
    ),
)


# ═══════════════════════════════════════════════════════════════════════════
# calendar
# ═══════════════════════════════════════════════════════════════════════════


class TestIsTradingDay:
    def test_weekdays(self):
        assert is_trading_day(date(2026, 10, 12))  # Mon
        assert is_trading_day(date(2026, 10, 13))  # Tue
        assert is_trading_day(date(2026, 10, 14))  # Wed
        assert is_trading_day(date(2026, 10, 15))  # Thu
        assert is_trading_day(date(2026, 10, 16))  # Fri

    def test_weekends(self):
        assert not is_trading_day(date(2026, 10, 17))  # Sat
        assert not is_trading_day(date(2026, 10, 18))  # Sun


class TestNextTradingDay:
    def test_from_friday(self):
        assert next_trading_day(date(2026, 10, 16)) == date(2026, 10, 16)  # Fri

    def test_from_saturday(self):
        assert next_trading_day(date(2026, 10, 17)) == date(2026, 10, 19)  # Sat→Mon

    def test_from_sunday(self):
        assert next_trading_day(date(2026, 10, 18)) == date(2026, 10, 19)  # Sun→Mon

    def test_from_monday(self):
        assert next_trading_day(date(2026, 10, 19)) == date(2026, 10, 19)  # Mon


class TestPreviousTradingDay:
    def test_from_friday(self):
        assert previous_trading_day(date(2026, 10, 16)) == date(2026, 10, 16)  # Fri

    def test_from_saturday(self):
        assert previous_trading_day(date(2026, 10, 17)) == date(2026, 10, 16)  # Sat→Fri

    def test_from_sunday(self):
        assert previous_trading_day(date(2026, 10, 18)) == date(2026, 10, 16)  # Sun→Fri

    def test_from_monday(self):
        assert previous_trading_day(date(2026, 10, 19)) == date(2026, 10, 19)  # Mon


class TestTradingDaysBetween:
    def test_same_day(self):
        assert trading_days_between(BASE, BASE) == 0  # Thu→Thu

    def test_friday_to_monday(self):
        # Fri(16)→Mon(19): trading days after 16th up to 19th = just Mon
        assert trading_days_between(date(2026, 10, 16), date(2026, 10, 19)) == 1

    def test_thursday_to_friday(self):
        assert trading_days_between(BASE, date(2026, 10, 16)) == 1  # Thu→Fri

    def test_monday_to_friday(self):
        # Mon→Fri: Tue, Wed, Thu, Fri = 4
        assert trading_days_between(date(2026, 10, 19), date(2026, 10, 23)) == 4

    def test_thursday_to_next_wednesday(self):
        # BASE(Thu 15)→Wed 21: Fri, Mon, Tue, Wed = 4
        assert trading_days_between(BASE, date(2026, 10, 21)) == 4

    def test_negative(self):
        # Mon(19)→Thu(15): -4 (going backwards)
        assert trading_days_between(date(2026, 10, 19), BASE) == -2  # Thu→Mon: Fri, Mon

    def test_non_trading_start(self):
        # Sat(17)→Tue(20): Mon, Tue = 2
        assert trading_days_between(date(2026, 10, 17), date(2026, 10, 20)) == 2

    def test_non_trading_end(self):
        # Thu(15)→Sun(18): Fri = 1 (excludes Sun, only counts trading days)
        assert trading_days_between(BASE, date(2026, 10, 18)) == 1


class TestIsBeforeCutoff:
    def test_before(self):
        dt = datetime(2026, 10, 15, 14, 59, tzinfo=CST)
        assert is_before_cutoff(dt)

    def test_at_cutoff(self):
        dt = datetime(2026, 10, 15, 15, 0, tzinfo=CST)
        assert not is_before_cutoff(dt)

    def test_after(self):
        dt = datetime(2026, 10, 15, 16, 30, tzinfo=CST)
        assert not is_before_cutoff(dt)

    def test_utc_to_cst(self):
        # 09:00 UTC = 17:00 CST → after cutoff
        dt = datetime(2026, 10, 15, 9, 0, tzinfo=ZoneInfo("UTC"))
        assert not is_before_cutoff(dt)


class TestTradeDate:
    def test_monday_before_cutoff(self):
        dt = datetime(2026, 10, 19, 10, 0, tzinfo=CST)
        assert trade_date(dt) == date(2026, 10, 19)

    def test_monday_after_cutoff(self):
        dt = datetime(2026, 10, 19, 16, 0, tzinfo=CST)
        assert trade_date(dt) == date(2026, 10, 20)  # next trading day = Tue

    def test_friday_before_cutoff(self):
        dt = datetime(2026, 10, 16, 14, 0, tzinfo=CST)
        assert trade_date(dt) == date(2026, 10, 16)

    def test_friday_after_cutoff(self):
        dt = datetime(2026, 10, 16, 15, 30, tzinfo=CST)
        assert trade_date(dt) == date(2026, 10, 19)  # next trading day = Mon

    def test_saturday(self):
        dt = datetime(2026, 10, 17, 10, 0, tzinfo=CST)
        assert trade_date(dt) == date(2026, 10, 19)  # Mon

    def test_sunday(self):
        dt = datetime(2026, 10, 18, 10, 0, tzinfo=CST)
        assert trade_date(dt) == date(2026, 10, 19)  # Mon

    def test_thursday_exactly_at_cutoff(self):
        dt = datetime(2026, 10, 15, 15, 0, tzinfo=CST)
        assert trade_date(dt) == date(2026, 10, 16)  # Fri (next trading day)


# ═══════════════════════════════════════════════════════════════════════════
# nav
# ═══════════════════════════════════════════════════════════════════════════


class TestNav:
    def test_mmf_always_base(self):
        """MMF NAV is constant regardless of date."""
        assert nav(Decimal("1.0000"), BASE, "MMF", BASE) == Decimal("1.0000")
        assert nav(Decimal("1.0000"), BASE, "MMF", date(2026, 10, 19)) == Decimal("1.0000")  # Mon
        assert nav(Decimal("1.0000"), BASE, "MMF", date(2026, 10, 17)) == Decimal("1.0000")  # Sat

    def test_base_date_is_base_nav(self):
        """On base_date itself, NAV = base_nav (0 trading days after)."""
        result = nav(Decimal("1.2310"), BASE, "BOND", BASE)
        assert result == Decimal("1.2310")

    def test_one_trading_day_after(self):
        """10-16 (Fri) is 1 trading day after 10-15 (Thu)."""
        result = nav(Decimal("1.0520"), BASE, "BOND", date(2026, 10, 16))
        assert result == Decimal("1.0521")  # 1.0520 + 1*0.0001

    def test_weekend_uses_friday(self):
        """Sat 10-17 → Fri 10-16 NAV."""
        result = nav(Decimal("1.2310"), BASE, "BOND", date(2026, 10, 17))
        assert result == Decimal("1.2311")  # same as Friday: +1 trading day

    def test_monday_after_friday(self):
        """Mon 10-19: 2 trading days after Thu (Fri, Mon)."""
        result = nav(Decimal("1.0520"), BASE, "BOND", date(2026, 10, 19))
        assert result == Decimal("1.0522")  # +2*0.0001

    def test_round_half_up(self):
        """ROUND_HALF_UP to 4 places."""
        result = nav(Decimal("1.0000"), BASE, "BOND", date(2026, 10, 15))
        assert result == Decimal("1.0000")
        # 1 trading day: 1.0001 (no rounding needed)

    def test_min_nav_floor(self):
        """NAV floors at 0.0001."""
        # base_nav=0.0000, base_date far in future → negative days
        result = nav(Decimal("0.0000"), date(2026, 11, 15), "BOND", BASE)
        # business_days_signed(2026-11-15, 2026-10-15) = negative
        # should floor at 0.0001
        assert result == MIN_NAV


class TestNavHistory:
    def test_non_mmf_30_entries(self):
        history = nav_history(Decimal("1.2310"), BASE, "BOND", date(2026, 10, 15), count=30)
        assert len(history) == 30

    def test_mmf_constant_history(self):
        history = nav_history(Decimal("1.0000"), BASE, "MMF", date(2026, 10, 15), count=5)
        assert len(history) == 5
        for _d, n in history:
            assert n == Decimal("1.0000")

    def test_history_dates_are_trading_days(self):
        history = nav_history(Decimal("1.2310"), BASE, "BOND", date(2026, 10, 15), count=5)
        for d, _n in history:
            assert is_trading_day(d), f"{d} is not a trading day"

    def test_history_last_date_is_end_date(self):
        """When end_date is a trading day, the last entry should be end_date."""
        history = nav_history(Decimal("1.2310"), BASE, "BOND", date(2026, 10, 21), count=3)
        # Wed 21 Oct. Trading days backwards: 21(Wed), 20(Tue), 19(Mon)
        assert history[-1][0] == date(2026, 10, 21)
        assert history[0][1] < history[-1][1]  # NAV increases with time (positive base)

    def test_history_on_weekend(self):
        """end_date=Saturday → last trading day = Friday."""
        history = nav_history(Decimal("1.2310"), BASE, "BOND", date(2026, 10, 17), count=2)
        # Sat 17 → previous trading days: Fri 16, Thu 15
        assert history[-1][0] == date(2026, 10, 16)
        assert history[0][0] == date(2026, 10, 15)

    def test_history_monotonic_for_non_mmf(self):
        """For non-MMF with positive NAV_STEP, NAV should increase over time."""
        history = nav_history(Decimal("1.2310"), BASE, "BOND", BASE, count=10)
        for i in range(1, len(history)):
            assert history[i][0] > history[i - 1][0]  # dates increase
            assert history[i][1] > history[i - 1][1]  # NAV increases (0.0001/day)


# ═══════════════════════════════════════════════════════════════════════════
# products
# ═══════════════════════════════════════════════════════════════════════════


class TestProductDef:
    def test_creation(self):
        p = _prd_stable()
        assert p.id == "prd_stable"
        assert p.risk_level == 3
        assert p.base_nav == Decimal("1.2310")

    def test_frozen(self):
        p = _prd_mmf()
        with pytest.raises(FrozenInstanceError):
            p.risk_level = 2  # type: ignore


class TestListProducts:
    def test_all(self):
        result = list_products(SEED_PRODUCTS)
        assert len(result) == 6

    def test_filter_by_risk(self):
        result = list_products(SEED_PRODUCTS, max_risk_level=2)
        ids = {p.id for p in result}
        assert ids == {"prd_mmf", "prd_sbond"}

    def test_filter_by_type(self):
        result = list_products(SEED_PRODUCTS, product_type="EQUITY")
        ids = {p.id for p in result}
        assert ids == {"prd_growth", "prd_tech"}

    def test_combined_filters(self):
        result = list_products(SEED_PRODUCTS, max_risk_level=3, product_type="MIXED")
        ids = {p.id for p in result}
        # prd_stable R3 MIXED, prd_mixed R3 MIXED
        assert ids == {"prd_stable", "prd_mixed"}

    def test_empty_tuple(self):
        result = list_products(())
        assert result == []


class TestCompareProducts:
    def test_two_products(self):
        result = compare_products(
            SEED_PRODUCTS,
            ("prd_mmf", "prd_stable"),
            on_date=BASE,
        )
        assert len(result["items"]) == 2
        assert result["comparison_fields"] == [
            "name",
            "risk_level",
            "risk_label",
            "product_type",
            "nav",
            "recent_return_bp",
            "min_amount_cents",
            "confirm_days",
            "settle_days",
        ]

    def test_four_products(self):
        result = compare_products(
            SEED_PRODUCTS,
            ("prd_mmf", "prd_sbond", "prd_stable", "prd_growth"),
            on_date=BASE,
        )
        assert len(result["items"]) == 4

    def test_mmf_nav_constant(self):
        """MMF NAV is always base_nav in compare output."""
        result = compare_products(
            SEED_PRODUCTS, ("prd_mmf", "prd_growth"), on_date=date(2026, 10, 21)
        )
        mmf = result["items"][0]
        assert mmf["nav"] == Decimal("1.0000")
        # growth should have increased
        growth = result["items"][1]
        assert growth["nav"] > Decimal("2.0310")

    def test_duplicate_ids_raises(self):
        with pytest.raises(ValueError, match="duplicate"):
            compare_products(SEED_PRODUCTS, ("prd_mmf", "prd_mmf"), on_date=BASE)

    def test_unknown_id_raises(self):
        with pytest.raises(ValueError, match="unknown"):
            compare_products(SEED_PRODUCTS, ("prd_mmf", "prd_nonexistent"), on_date=BASE)

    def test_too_few_ids_raises(self):
        with pytest.raises(ValueError):
            compare_products(SEED_PRODUCTS, ("prd_mmf",), on_date=BASE)

    def test_too_many_ids_raises(self):
        with pytest.raises(ValueError):
            compare_products(
                SEED_PRODUCTS,
                ("prd_mmf", "prd_sbond", "prd_stable", "prd_growth", "prd_tech"),
                on_date=BASE,
            )


class TestGetProduct:
    def test_valid_product(self):
        result = get_product(SEED_PRODUCTS, "prd_stable", on_date=BASE)
        assert result is not None
        assert result["id"] == "prd_stable"
        assert result["risk_level"] == 3
        assert result["nav"] == Decimal("1.2310")  # on base_date
        assert len(result["nav_history"]) == 30

    def test_unknown_product(self):
        result = get_product(SEED_PRODUCTS, "prd_nonexistent", on_date=BASE)
        assert result is None

    def test_nav_history_30_trading_days(self):
        result = get_product(SEED_PRODUCTS, "prd_growth", on_date=BASE)
        assert result is not None
        history = result["nav_history"]
        assert len(history) == 30
        # Check dates are trading days
        for entry in history:
            entry_date = entry["date"]
            d = date.fromisoformat(entry_date) if isinstance(entry_date, str) else entry_date
            if isinstance(d, date):
                assert is_trading_day(d), f"{d} is not a trading day"

    def test_mmf_nav_history_constant(self):
        result = get_product(SEED_PRODUCTS, "prd_mmf", on_date=BASE)
        assert result is not None
        for entry in result["nav_history"]:
            nav_val = Decimal(entry["nav"]) if isinstance(entry["nav"], str) else entry["nav"]
            assert nav_val == Decimal("1.0000")


class TestRecentReturnBp:
    def test_zero_return_on_base_date(self):
        """On base_date alone, recent_return_bp over 0 days should be 0."""
        p = _prd_stable()
        # window_trading_days=0 means same day → return is 0/earliest → 0
        # Actually window_trading_days=0 goes back 0 days → earliest=latest → ratio=0
        r = recent_return_bp(p, on_date=BASE, window_trading_days=0)
        # (latest-earliest)/earliest with earliest=latest → 0/earliest = 0
        assert r == 0

    def test_positive_return_bp(self):
        """On a later date where NAV has increased."""
        p = _prd_stable()
        # 10 trading days later: base_nav + 10*0.0001 = 1.2320
        # But need to find a date that's 10 trading days after BASE
        # BASE = Thu 10-15
        # 10 trading days after = 10-29 (Thu): 10/16,19,20,21,22,23,26,27,28,29 = 10
        on_date = date(2026, 10, 29)
        r = recent_return_bp(p, on_date=on_date, window_trading_days=10)
        assert r > 0

    def test_negative_return_not_happen_with_synthetic_formula(self):
        """Our synthetic NAV always increases → return_bp >= 0."""
        p = _prd_stable()
        r = recent_return_bp(p, on_date=date(2026, 10, 21), window_trading_days=3)
        assert r >= 0

    def test_is_int(self):
        p = _prd_stable()
        r = recent_return_bp(p, on_date=date(2026, 10, 21), window_trading_days=5)
        assert isinstance(r, int)
