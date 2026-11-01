"""A stored ACTIVE mandate does not authorize a charge after its inclusive end."""

from datetime import timedelta

import pytest
from safebank_bankcore.events import apply_merchant_charge
from safebank_contracts.codes import Code

from tests.bankcore.obligations_fixtures import (
    raw_billing,
    raw_mandate,
    raw_merchant,
    raw_projection_state,
)


@pytest.mark.parametrize("expired", [False, True])
def test_merchant_charge_respects_exact_mandate_end_boundary_without_moving_money(expired):
    bill = raw_billing()
    mandate = raw_mandate(
        end_at=bill.next_due_at - timedelta(seconds=1) if expired else bill.next_due_at
    )
    state = raw_projection_state(billings=[bill], mandates=[mandate], merchants=[raw_merchant()])
    after, effects, run = apply_merchant_charge(state, billing_id=bill.id, due_at=bill.next_due_at)
    if expired:
        assert run.status == "BLOCKED" and run.code == Code.MANDATE_NOT_ACTIVE.value
        assert run.transfer_id is None and not effects
        assert after.accounts == state.accounts and after.transactions == state.transactions
    else:
        assert run.status == "SUCCESS" and effects
    assert after.obligations.billings[bill.id].next_due_at > bill.next_due_at
    again, duplicate_effects, duplicate_run = apply_merchant_charge(
        after, billing_id=bill.id, due_at=bill.next_due_at
    )
    assert again == after and not duplicate_effects and duplicate_run is None
