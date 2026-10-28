"""Regression test for reads.py NameError: date.fromisoformat without date import.

Reproduces the bug first (red), then verifies the fix (green).
Does NOT require PostgreSQL — BankState + Customer are constructed in memory.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.reads import read
from safebank_contracts.state import BankState, Customer

CST = ZoneInfo("Asia/Shanghai")


def _state(*, risk_level: int | None, risk_valid_until: str | None) -> BankState:
    customer = Customer(
        id="cus_test",
        display_name="测试用户",
        phone="13800000000",
        risk_level=risk_level,
        risk_valid_until=risk_valid_until,
    )
    return BankState(
        sim_now=datetime(2026, 10, 15, 12, 0, tzinfo=CST),
        clock_version=1,
        versions={"cus_test": 1},
        accounts={},
        customers={"cus_test": customer},
        cards={},
        payees={},
    )


class TestGetProfileRiskFix:
    """Before the fix (only ``timedelta`` imported), any call where
    ``risk_level is not None`` and ``risk_valid_until is not None``
    raised ``NameError: name 'date' is not defined`` at line 209."""

    def test_has_valid_risk_assessment(self):
        """Smoke: a valid risk assessment should compute is_valid=True."""
        st = _state(risk_level=3, risk_valid_until="2027-10-15")
        result = read(st, "get_profile", {}, "cus_test")
        rp = result.risk_profile
        assert rp.level == 3
        assert rp.is_valid is True
        assert rp.valid_until.isoformat() == "2027-10-15"

    def test_expired_risk_assessment(self):
        """An expired risk assessment should compute is_valid=False."""
        st = _state(risk_level=3, risk_valid_until="2025-01-01")
        result = read(st, "get_profile", {}, "cus_test")
        rp = result.risk_profile
        assert rp.level == 3
        assert rp.is_valid is False

    def test_no_risk_assessment(self):
        """No risk assessment at all should return level=None, is_valid=False."""
        st = _state(risk_level=None, risk_valid_until=None)
        result = read(st, "get_profile", {}, "cus_test")
        rp = result.risk_profile
        assert rp.level is None
        assert rp.is_valid is False

    def test_level_set_but_valid_until_none(self):
        """Edge case: level present but no valid_until → is_valid=False."""
        st = _state(risk_level=2, risk_valid_until=None)
        result = read(st, "get_profile", {}, "cus_test")
        rp = result.risk_profile
        assert rp.level == 2
        assert rp.is_valid is False
