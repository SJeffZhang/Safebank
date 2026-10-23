from dataclasses import FrozenInstanceError, replace

import pytest

from services.gateway.app.state import ExecutionIdentity


@pytest.mark.parametrize(
    "change",
    [
        {"attempt": True},
        {"attempt": 0},
        {"attempt": 1.0},
        {"revision": False},
        {"revision": -1},
        {"origin": "other"},
        {"customer_id": ""},
        {"plan_id": ""},
    ],
)
def test_execution_identity_rejects_invalid_program_inputs(change):
    with pytest.raises(ValueError):
        ExecutionIdentity(
            **{
                "customer_id": "cus",
                "plan_id": "pln",
                "origin": "agent",
                "attempt": 1,
                "revision": 1,
                **change,
            }
        )


def test_execution_identity_is_frozen_and_revision_replacement_is_explicit():
    original = ExecutionIdentity("cus", "pln", "agent", 1, 1)
    with pytest.raises(FrozenInstanceError):
        original.attempt = 2
    fresh = replace(original, revision=2)
    assert original.revision == 1 and fresh.revision == 2 and fresh.attempt == original.attempt
