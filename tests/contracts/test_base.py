from decimal import Decimal

import pytest
from pydantic import ValidationError
from safebank_contracts.base import (
    BIGINT_MAX,
    Cents,
    Contract,
    Decimal4,
    Int64,
    PositiveCents,
    Timestamp,
)


class Sample(Contract):
    amount_cents: Cents
    delta_cents: Int64
    units: Decimal4
    occurred_at: Timestamp


def good():
    return dict(
        amount_cents=100, delta_cents=-1, units="1.2000", occurred_at="2026-10-23T12:00:00+08:00"
    )


@pytest.mark.parametrize("value", [True, 1.0, "100", -1, BIGINT_MAX + 1])
def test_cents_rejects_coercion_and_bounds(value):
    with pytest.raises(ValidationError):
        Sample(**(good() | {"amount_cents": value}))


@pytest.mark.parametrize(
    "value", [float("nan"), 1, 1.5, "NaN", "Infinity", "-1", "1.00001", "100000000000000.0000"]
)
def test_decimal_rejects_invalid(value):
    with pytest.raises(ValidationError):
        Sample(**(good() | {"units": value}))


def test_round_trip_exact_and_immutable():
    value = Sample(**(good() | {"amount_cents": BIGINT_MAX}))
    assert value.units == Decimal("1.2000")
    assert '"units":"1.2000"' in value.model_dump_json()
    assert Sample.model_validate_json(value.model_dump_json()) == value
    with pytest.raises(ValidationError):
        value.amount_cents = 1
    for overrides in ({"occurred_at": "2026-10-23T12:00:00"}, {"extra": 1}):
        with pytest.raises(ValidationError):
            Sample(**(good() | overrides))


def test_positive_command_amount():
    class Command(Contract):
        amount_cents: PositiveCents

    assert Command(amount_cents=1)
    assert Command(amount_cents=100_000_000)
    for amount in (0, -1, 100_000_001, True):
        with pytest.raises(ValidationError):
            Command(amount_cents=amount)


@pytest.mark.parametrize(
    "value", [True, 123, 1.5, "123", "2026-10-23", "2026-10-23 12:00:00+08:00"]
)
def test_timestamp_rejects_epoch_or_non_rfc3339(value):
    with pytest.raises(ValidationError):
        Sample(**(good() | {"occurred_at": value}))
