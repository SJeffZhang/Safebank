"""Consent cannot grow through mutable instruction definitions."""

from datetime import UTC, datetime, time

import pytest
from safebank_bankcore.grants import instruction_scope, scope_hash


def instruction():
    return dict(
        id="si",
        customer_id="cus",
        kind="SCHEDULED_TRANSFER",
        from_account_id="acc",
        end_at=None,
        source_plan_id="seed",
        created_at=datetime(2026, 10, 15, tzinfo=UTC),
        payee_id="pye",
        to_account_id=None,
        amount_cents=100,
        period="MONTHLY",
        anchor_day=1,
        weekday=None,
        time_of_day=time(6),
        status="ACTIVE",
        next_run_at=None,
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("amount_cents", 101),
        ("payee_id", "attacker"),
        ("period", "WEEKLY"),
        ("end_at", "2027-01-01T00:00:00+08:00"),
        ("anchor_day", 2),
        ("time_of_day", time(7)),
        ("from_account_id", "other"),
        ("customer_id", "other"),
    ],
)
def test_contract_change_changes_consent(field, value):
    original = instruction()
    modified = {**original, field: value}
    assert scope_hash(instruction_scope(original)) != scope_hash(instruction_scope(modified))


def test_cursor_changes_do_not_expand_or_invalidate_consent():
    original = instruction()
    modified = {**original, "status": "DELETED", "next_run_at": "2026-12-01T06:00:00+08:00"}
    assert instruction_scope(original) == instruction_scope(modified)
    json_form = {**original, "created_at": "2026-10-15T08:00:00+08:00", "time_of_day": "06:00:00"}
    assert instruction_scope(original) == instruction_scope(json_form)


def test_incomplete_consent_fails_closed():
    original = instruction()
    del original["period"]
    with pytest.raises(KeyError):
        instruction_scope(original)


def test_seed_original_start_is_hashed_and_due_is_not_movable():
    from safebank_bankcore.grants import due_in_consent, seed_consent

    scope = instruction_scope({**instruction(), "period": "ONCE", "anchor_day": None})
    first = "2026-11-01T06:00:00+08:00"
    original = seed_consent(scope, first)
    moved = seed_consent(scope, "2026-11-02T06:00:00+08:00")
    assert scope_hash(original) != scope_hash(moved)
    assert due_in_consent(scope, first, first)
    assert not due_in_consent(scope, first, "2026-11-02T06:00:00+08:00")
    assert not due_in_consent(scope, first, "2026-10-01T06:00:00+08:00")


def test_seed_monthly_cursor_advances_without_extending_scope():
    from safebank_bankcore.grants import due_in_consent

    scope = instruction_scope({**instruction(), "anchor_day": 31})
    first = "2026-10-31T06:00:00+08:00"
    assert due_in_consent(scope, first, "2026-11-30T06:00:00+08:00")
    assert due_in_consent(scope, first, "2026-12-31T06:00:00+08:00")
    assert not due_in_consent(scope, first, "2026-11-29T06:00:00+08:00")
    assert not due_in_consent(scope, first, "2026-11-30T07:00:00+08:00")
