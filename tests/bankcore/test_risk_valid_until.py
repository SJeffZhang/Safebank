"""risk_valid_until may be stored as a date or an aware datetime (demo seed, PRD §2.2).

Every reader must treat it as the Asia/Shanghai business date it covers: bankcore
suitability, L3 get_profile and the gateway predicate disagreed before (a seeded
datetime made subscribe fail with ASSESSMENT_MISSING and get_profile return 500).
"""

from __future__ import annotations

from datetime import date

import pytest
from app.reads import read
from safebank_bankcore.risk import risk_valid_until_date

from tests.bankcore.test_funds import funds_state, simulate
from tests.bankcore.test_reads_profile import _state
from tests.contracts.samples import step


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2027-03-01", date(2027, 3, 1)),
        ("2027-03-01T10:00:00+08:00", date(2027, 3, 1)),
        ("2027-02-28T17:30:00+00:00", date(2027, 3, 1)),
        ("2027-03-01T10:00:00", date(2027, 3, 1)),
    ],
)
def test_risk_valid_until_date_accepts_date_and_datetime(raw, expected):
    assert risk_valid_until_date(raw) == expected


def _with_valid_until(value: str):
    state = funds_state()
    customers = dict(state.customers)
    customers["cus_a"] = customers["cus_a"].model_copy(update={"risk_valid_until": value})
    return state.model_copy(update={"customers": customers})


def test_subscribe_accepts_datetime_valid_until():
    state = _with_valid_until("2027-12-31T10:00:00+08:00")
    result = simulate(
        state, [step("subscribe_product", product_id="prd_bond", amount_cents=100_000)]
    )
    assert result.error is None


def test_subscribe_rejects_expired_datetime_valid_until():
    state = _with_valid_until("2020-01-01T10:00:00+08:00")
    result = simulate(
        state, [step("subscribe_product", product_id="prd_bond", amount_cents=100_000)]
    )
    assert result.error == "ASSESSMENT_MISSING"


def test_get_profile_returns_business_date_for_datetime_valid_until():
    state = _state(risk_level=3, risk_valid_until="2027-03-01T10:00:00+08:00")
    profile = read(state, "get_profile", {}, "cus_test")
    assert profile.risk_profile.valid_until == date(2027, 3, 1)
    assert profile.risk_profile.is_valid is True
