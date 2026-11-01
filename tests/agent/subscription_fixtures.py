"""Canonical detect_subscriptions fixtures, distinct from list_subscriptions."""

from safebank_contracts.views import READ_RESULTS


def detected_subscription(
    instruction_id: str,
    typical_amount_cents: int,
    *,
    is_idle: bool,
    merchant_name: str,
) -> dict:
    suffix = instruction_id.removeprefix("si_")
    item = {
        "merchant_id": "mer_" + suffix,
        "merchant_name": merchant_name,
        "instruction_id": instruction_id,
        "billing_id": "bil_" + suffix,
        "period": "MONTHLY",
        "typical_amount_cents": typical_amount_cents,
        "occurrences": 3,
        "last_charged_at": "2026-09-30T06:00:00+08:00",
        "is_idle": is_idle,
        "usage_evidence": {"recent_usage_count": 0, "previous_usage_count": 0},
        "annual_cost_cents": typical_amount_cents * 12,
    }
    # Fail the fixture at its boundary if future changes reintroduce the wrong
    # list_subscriptions amount field or omit required detection evidence.
    return (
        READ_RESULTS["detect_subscriptions"]
        .model_validate({"items": [item]})
        .model_dump(mode="json")["items"][0]
    )
