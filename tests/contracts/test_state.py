import pytest
from safebank_contracts.state import Account, BankState, Customer


def test_snapshot_deep_immutability_and_json_round_trip():
    state = BankState(
        sim_now="2026-10-23T12:00:00+08:00",
        clock_version=1,
        versions={"cus_a": 0},
        accounts={
            "acc_a": Account(
                id="acc_a",
                customer_id="cus_a",
                type="CHECKING",
                display_name="活期",
                account_no="6222020000000001",
            )
        },
        customers={"cus_a": Customer(id="cus_a", display_name="甲", phone="13800000000")},
        cards={},
        payees={},
    )
    assert BankState.model_validate_json(state.model_dump_json()) == state
    for data in (state.accounts, state.versions, state.counters.daily_used_cents):
        with pytest.raises(TypeError):
            data["mutated"] = 1
