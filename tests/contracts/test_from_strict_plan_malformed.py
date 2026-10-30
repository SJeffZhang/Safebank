"""A malformed strict-plan shape must raise ValueError (retried with feedback), not crash.

Live e2e case 37 got a bare string for to_account_id; from_strict_plan indexed it as a
tagged entity and raised TypeError, which escaped the plan retry loop and ended the run.
"""

from __future__ import annotations

import pytest
from safebank_contracts.schema import from_strict_plan


def _plan(args: dict, tool: str = "transfer") -> dict:
    return {"steps": [{"step_id": "s1", "tool": tool, "depends_on": [], "args": args}]}


@pytest.mark.parametrize(
    "args",
    [
        {"to_account_id": "acc_zs_chk"},
        {"payee_id": "pye_zs_ww"},
        {"to_account_id": {"id": "acc_zs_chk"}},
        {"payee_id": None},
    ],
)
def test_malformed_entity_raises_value_error(args):
    with pytest.raises(ValueError):
        from_strict_plan(_plan(args))


def test_unknown_channel_value_raises_value_error():
    args = {
        "card_id": "crd_zs_1",
        "online": "MAYBE",
        "overseas": "KEEP",
        "atm": "KEEP",
        "quickpass": "KEEP",
    }
    with pytest.raises(ValueError):
        from_strict_plan(_plan(args, tool="update_card_controls"))


def test_well_formed_entity_still_converts():
    out = from_strict_plan(
        _plan({"to_account_id": {"kind": "id", "id": "acc_zs_sav"}, "payee_id": {"kind": "none"}})
    )
    assert out["steps"][0]["args"] == {"to_account_id": "acc_zs_sav", "payee_id": None}
