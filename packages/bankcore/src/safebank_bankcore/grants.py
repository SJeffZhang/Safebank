"""Canonical persistent consent terms; no IO or authorization decisions.

Execution cursors and lifecycle status are mutable and excluded. Revocation is
checked separately. All contractual targets, amounts/caps and recurrence terms
remain bound, including the mandate's billing identity/calendar.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime, time
from typing import Any

_COMMON = ("id", "customer_id", "kind", "from_account_id", "end_at", "source_plan_id", "created_at")
_SCHEDULED = (
    "payee_id",
    "to_account_id",
    "amount_cents",
    "period",
    "anchor_day",
    "weekday",
    "time_of_day",
)
_MANDATE = ("merchant_id", "billing_id", "max_amount_cents")
_BILLING = (
    "id",
    "customer_id",
    "account_id",
    "merchant_id",
    "period",
    "anchor_day",
    "weekday",
    "time_of_day",
)


def _value(key: str, value: Any) -> Any:
    if value is None:
        return None
    if key in ("end_at", "created_at"):
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        if value.tzinfo is None:
            raise ValueError("consent timestamp must be aware")
        return value.astimezone(UTC).isoformat()
    if key == "time_of_day":
        if isinstance(value, str):
            value = time.fromisoformat(value)
        return value.isoformat()
    return value


def instruction_scope(
    instruction: Mapping[str, Any], billing: Mapping[str, Any] | None = None
) -> dict:
    """Fail on incomplete terms; accept DB/model JSON with identical semantics."""
    kind = instruction["kind"]
    if kind not in ("SCHEDULED_TRANSFER", "MERCHANT_MANDATE"):
        raise ValueError("unsupported consent kind")
    fields = _COMMON + (_SCHEDULED if kind == "SCHEDULED_TRANSFER" else _MANDATE)
    scope = {key: _value(key, instruction[key]) for key in fields}
    if kind == "MERCHANT_MANDATE":
        if billing is None:
            raise ValueError("mandate consent requires its billing")
        if (
            billing["id"],
            billing["customer_id"],
            billing["account_id"],
            billing["merchant_id"],
        ) != (
            instruction["billing_id"],
            instruction["customer_id"],
            instruction["from_account_id"],
            instruction["merchant_id"],
        ):
            raise ValueError("billing does not belong to mandate")
        scope["billing"] = {key: _value(key, billing[key]) for key in _BILLING}
    return scope


def scope_hash(scope: dict) -> str:
    return hashlib.sha256(
        json.dumps(scope, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def seed_consent(scope: dict, first_run_at: datetime | str | None) -> dict:
    """Preserve original start independently from a mutable execution cursor."""
    first = _value("created_at", first_run_at)
    if scope["kind"] == "SCHEDULED_TRANSFER" and first is None:
        raise ValueError("scheduled seed consent requires its first execution date")
    return {"kind": "SEED_CONSENT", "terms": scope, "first_run_at": first}


def due_in_consent(scope: dict, first_run_at: str, due_at: str) -> bool:
    """Allow only authorized recurrence instants, with no pre-start execution."""
    import calendar

    from .reserve import _business_time

    first = _business_time(datetime.fromisoformat(first_run_at))
    due = _business_time(datetime.fromisoformat(due_at))
    if due < first:
        return False
    if scope.get("end_at") and due > _business_time(datetime.fromisoformat(scope["end_at"])):
        return False
    if due.timetz().replace(tzinfo=None).isoformat() != scope["time_of_day"]:
        return False
    if scope["period"] == "ONCE":
        return due == first
    if scope["period"] == "WEEKLY":
        return due.weekday() == scope["weekday"] and (due - first).days % 7 == 0
    if scope["period"] == "MONTHLY":
        return due.day == min(scope["anchor_day"], calendar.monthrange(due.year, due.month)[1])
    return False
