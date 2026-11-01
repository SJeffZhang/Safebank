"""Notify immutable L3 event outcomes inside the caller's producer transaction.

Never parse payload text for a customer, plan, merchant name or notification body.
S1 must pass the L3 definition and actual terminal result, including on recovery.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncConnection

from .notify import notify

_TERMINAL = frozenset({"SUCCESS", "BLOCKED", "FAILED_INSUFFICIENT", "SKIPPED_REAPPROVAL"})
_FUND = {"FUND_CONFIRM": "CONFIRM", "FUND_SETTLE": "SETTLE", "FUND_EXPIRY": "EXPIRY"}


def _identity(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError("event definition requires an authoritative identity")
    return value


async def notify_event_result(conn: AsyncConnection, *, event: dict, status: str) -> None:
    """Emit fixed templates; UNIQUE(event_id,type) handles replay and recovery.

    Only top-level ``source_plan_id`` is an authoritative optional plan link.
    Fund-specific wording additionally requires ``execution.result.order_status``;
    ``execution.result.no_op=True`` suppresses a redundant fund lifecycle alert.
    """
    kind = event.get("kind")
    if status not in _TERMINAL or kind not in {
        "SCHEDULED_TRANSFER",
        "MERCHANT_CHARGE",
        "REMINDER",
        *_FUND,
    }:
        return
    if (kind == "MERCHANT_CHARGE" and status == "SUCCESS") or (
        kind == "REMINDER" and status != "SUCCESS"
    ):
        return
    event_id = _identity(event.get("event_id"))
    customer_id = _identity(event.get("customer_id"))
    _identity(event.get("source_id"))
    plan_id = event.get("source_plan_id")
    if plan_id is not None:
        plan_id = _identity(plan_id)
    execution = event.get("execution") or {}
    if not isinstance(execution, dict):
        raise ValueError("invalid event execution")
    for field, expected in (
        ("event_id", event_id),
        ("customer_id", customer_id),
        ("status", status),
    ):
        if field in execution and execution[field] != expected:
            raise ValueError("event execution identity or outcome mismatch")
    outcome = status
    if kind == "SCHEDULED_TRANSFER":
        notification_type = "SCHEDULE_RESULT"
    elif kind == "MERCHANT_CHARGE":
        notification_type = "CHARGE_BLOCKED"
    elif kind == "REMINDER":
        notification_type = "CHARGE_UPCOMING"
    else:
        notification_type = "FUND_RESULT"
        result = execution.get("result") or {}
        if not isinstance(result, dict):
            raise ValueError("invalid fund event result")
        if status == "SUCCESS" and result.get("no_op") is True:
            return
        if status != "SUCCESS":
            outcome = "FAILED"
        elif kind == "FUND_EXPIRY" and result.get("order_status") == "EXPIRED":
            outcome = "EXPIRED"
        elif kind == "FUND_CONFIRM" and result.get("order_status") == "SETTLED":
            outcome = "CONFIRMED"
        elif kind == "FUND_SETTLE" and result.get("order_status") == "SETTLED":
            outcome = "SETTLED"
        else:
            outcome = _FUND[kind] + "_PROCESSED"
    await notify(
        conn,
        customer_id=customer_id,
        event_id=event_id,
        type=notification_type,
        outcome=outcome,
        plan_id=plan_id,
    )
