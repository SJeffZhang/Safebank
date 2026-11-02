"""Pure reminder definitions and current mandate checks; no banking writes or IO."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from safebank_bankcore.ids import digest
from safebank_contracts.state import EVENT_KIND_RANK

_BUSINESS_ZONE = ZoneInfo("Asia/Shanghai")


def _aware(value: object) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("aware reminder timestamp required")
    return value.astimezone(_BUSINESS_ZONE)


def reminder_scan_at(sim_now: datetime) -> datetime | None:
    """Today's 09:00 scan is eligible only once the simulated clock reaches it."""
    now = _aware(sim_now)
    scan = now.replace(hour=9, minute=0, second=0, microsecond=0)
    return scan if scan <= now else None


def next_reminder_scan(sim_now: datetime) -> datetime:
    """Strictly next Shanghai 09:00, including days with an empty event queue."""
    now = _aware(sim_now)
    scan = now.replace(hour=9, minute=0, second=0, microsecond=0)
    return scan if scan > now else scan + timedelta(days=1)


def _valid_sources(billing: Mapping, mandate: Mapping, at: datetime) -> bool:
    due = _aware(billing["next_due_at"])
    amount, cap = billing["amount_cents"], mandate["max_amount_cents"]
    return (
        billing["active"] is True
        and mandate["kind"] == "MERCHANT_MANDATE"
        and mandate["status"] == "ACTIVE"
        and (billing["id"], billing["customer_id"], billing["account_id"], billing["merchant_id"])
        == (
            mandate["billing_id"],
            mandate["customer_id"],
            mandate["from_account_id"],
            mandate["merchant_id"],
        )
        and all(
            isinstance(value, str) and value
            for value in (
                billing["id"],
                billing["customer_id"],
                billing["account_id"],
                mandate["id"],
            )
        )
        and type(amount) is int
        and 0 < amount <= 100_000_000
        and type(cap) is int
        and amount <= cap
        and _aware(billing["created_at"]) <= at
        and _aware(mandate["created_at"]) <= at
        and (mandate["end_at"] is None or _aware(mandate["end_at"]) >= due)
        and at < due <= at + timedelta(days=3)
    )


def reminder_is_current(payload: Mapping, billing: Mapping, mandate: Mapping, at: datetime) -> bool:
    """Recheck the immutable reminder against current rows before a SUCCESS.

    Callers must additionally enforce job.due_at <= clock and L2 grant revocation;
    these facts are outside this pure L3 billing/mandate comparison.
    """
    try:
        now = _aware(at)
        return (
            _valid_sources(billing, mandate, now)
            and (
                payload["billing_id"],
                payload["customer_id"],
                payload["account_id"],
                payload["amount_cents"],
                _aware(payload["charge_due_at"]),
                payload["instruction_id"],
            )
            == (
                billing["id"],
                billing["customer_id"],
                billing["account_id"],
                billing["amount_cents"],
                _aware(billing["next_due_at"]),
                mandate["id"],
            )
            and type(payload["amount_cents"]) is int
        )
    except (KeyError, TypeError, ValueError):
        return False


def build_reminder_job(*, billing: Mapping, mandate: Mapping, scan_at: datetime) -> dict | None:
    """One stable (billing, concrete charge instant, reminder) identity across scans."""
    try:
        scan = _aware(scan_at)
        if scan.time().isoformat() != "09:00:00" or not _valid_sources(billing, mandate, scan):
            return None
        due = _aware(billing["next_due_at"]).astimezone(UTC).isoformat()
        payload = {
            "billing_id": billing["id"],
            "customer_id": billing["customer_id"],
            "account_id": billing["account_id"],
            "amount_cents": billing["amount_cents"],
            "charge_due_at": due,
            "instruction_id": mandate["id"],
        }
        return {
            "id": digest(["REMINDER", billing["id"], due]),
            "kind": "REMINDER",
            "rank": EVENT_KIND_RANK["REMINDER"],
            "due_at": scan,
            "immutable_payload": {"payload": payload},
            "payload_hash": digest(payload),
            "source_id": billing["id"],
            "period_key": due + "|reminder",
            "status": "PENDING",
            "attempt_count": 0,
        }
    except (KeyError, TypeError, ValueError):
        return None
