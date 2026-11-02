"""Conservative cost normalization for a verified, still-chargeable subscription.

This is reporting only: no effects, authorization, future balance prediction or
IO. Unknown/incomplete authority returns None; a known ineligible next charge
returns zero. Callers must use the pre-cancellation state.
"""

from safebank_contracts.obligations import MerchantMandate
from safebank_contracts.state import BankState

from .periods import normalize_period
from .reserve import _business_time, _validate_mandate_billing


def subscription_savings(
    state: BankState, instruction_id: str, customer_id: str
) -> tuple[int, int] | None:
    """Return monthly/annual cents for the current eligible billing rate.

    Rates normalize the current recurring bill; they do not promise how many
    future charges will succeed. L2 grants and execution remain independently
    checked by their existing paths.
    """
    snapshot = state.obligations
    if (
        snapshot is None
        or customer_id not in snapshot.covered_customer_ids
        or set(snapshot.covered_customer_ids) != set(state.versions)
    ):
        return None
    mandate = snapshot.standing_instructions.get(instruction_id)
    customer = state.customers.get(customer_id)
    if (
        not isinstance(mandate, MerchantMandate)
        or mandate.id != instruction_id
        or mandate.customer_id != customer_id
        or customer is None
        or customer.id != customer_id
    ):
        return None
    # The execution applier resolves a billing to one mandate. Ambiguous raw
    # definitions cannot establish that canceling this one removes the charge.
    matches = [
        item
        for item in snapshot.standing_instructions.values()
        if isinstance(item, MerchantMandate) and item.billing_id == mandate.billing_id
    ]
    if len(matches) != 1:
        return None
    try:
        billing = _validate_mandate_billing(state, mandate, snapshot)
        account = state.accounts[billing.account_id]
        merchant = snapshot.merchants.get(billing.merchant_id)
        if (
            account.customer_id != customer_id
            or merchant is None
            or merchant.id != billing.merchant_id
        ):
            return None
        now = _business_time(state.sim_now)
        due = _business_time(billing.next_due_at)
        if (
            mandate.status != "ACTIVE"
            or not billing.active
            or billing.amount_cents > mandate.max_amount_cents
            or _business_time(mandate.created_at) > now
            or _business_time(billing.created_at) > now
            or due <= now
            or (mandate.end_at is not None and _business_time(mandate.end_at) < due)
            or any(run.billing_id == billing.id and run.due_at == due for run in snapshot.runs)
        ):
            return (0, 0)
        return normalize_period(billing.amount_cents, billing.period)
    except (KeyError, TypeError, ValueError):
        return None
