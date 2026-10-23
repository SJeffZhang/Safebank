"""Pure cross-session workflow sequence analysis (PRD §7.8).

Only committed operations participate in success sequences. L3
``operation_events`` are the authority; gateway ``workflow_events`` with status
``COMMITTED`` are a de-duplicated cache that must not double-count the same
``event_id``. Business windows (24h, 30 minutes, cooldown) use the simulated
clock ``sim_now``; anti-retry windows (DUPLICATE_PLAN, REJECTED_RETRY) use the
wall clock.

This module performs no IO and no authorization decision; the workflow stage
owns both.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# Card actions whose occurrence near a PAYEE_ADD(T) + transfer-to-T forms the
# A3 RISKY_SEQUENCE (PRD §7.8). Order is irrelevant; the no-card savings variant
# loosens the checking card or any card bound to the customer.
LOOSEN_KINDS = frozenset({"CARD_LIMIT_LOOSEN", "CARD_CONTROL_LOOSEN", "CARD_UNFREEZE"})

# External-outflow transfer kinds that participate in split/cumulative windows.
OUTFLOW_KINDS = frozenset({"TRANSFER_EXTERNAL"})


@dataclass(frozen=True)
class CommittedOp:
    """A single committed operation, comparable across L3 and gateway sources."""

    event_id: str
    customer_id: str
    action_kind: str
    target_fingerprint: str | None
    amount_cents: int
    occurred_at: datetime

    def sort_key(self):
        return (self.occurred_at, self.event_id)


def merge_committed(
    ops_7d: tuple[object, ...],
    workflow_rows: tuple[object, ...],
) -> tuple[CommittedOp, ...]:
    """Merge L3 operation events with gateway COMMITTED workflow events.

    L3 wins on duplicate ``event_id``; only ``COMMITTED`` gateway rows count
    (BLOCKED / PENDING rows are rejected-after-retry or in-flight, never a
    success chain). Rows from other customers or with a non-committed status
    are dropped here so callers cannot accidentally leak them into a window.
    """
    by_id: dict[str, CommittedOp] = {}
    for row in workflow_rows:
        status = row.get("status") if isinstance(row, dict) else getattr(row, "status", None)
        if status != "COMMITTED":
            continue
        event_id = row.get("event_id") if isinstance(row, dict) else getattr(row, "event_id", None)
        if not event_id:
            continue
        by_id[event_id] = CommittedOp(
            event_id=event_id,
            customer_id=row.get("customer_id", "")
            if isinstance(row, dict)
            else getattr(row, "customer_id", ""),
            action_kind=row.get("action_kind", "")
            if isinstance(row, dict)
            else getattr(row, "action_kind", ""),
            target_fingerprint=row.get("target_id", None)
            if isinstance(row, dict)
            else getattr(row, "target_id", None),
            amount_cents=int(
                row.get("amount_cents", 0)
                if isinstance(row, dict)
                else getattr(row, "amount_cents", 0)
            ),
            occurred_at=row.get("occurred_at", datetime.max)
            if isinstance(row, dict)
            else getattr(row, "occurred_at", datetime.max),
        )
    for op in ops_7d:
        # L3 operation events are authoritative and always committed.
        by_id[op.event_id] = CommittedOp(
            event_id=op.event_id,
            customer_id=op.customer_id,
            action_kind=op.action_kind,
            target_fingerprint=op.target_fingerprint,
            amount_cents=op.amount_cents,
            occurred_at=op.occurred_at,
        )
    return tuple(sorted(by_id.values(), key=CommittedOp.sort_key))


def window_ops(
    ops: tuple[CommittedOp, ...],
    *,
    customer_id: str,
    start: datetime,
    end: datetime,
) -> tuple[CommittedOp, ...]:
    """Operations for one customer in ``(start, end]`` (business time)."""
    return tuple(
        op for op in ops if op.customer_id == customer_id and start < op.occurred_at <= end
    )


def risky_sequence(
    window: tuple[CommittedOp, ...],
    *,
    target_fingerprint: str,
) -> bool:
    """A3: PAYEE_ADD(T) and any card loosen both occur in the 24h window.

    Order is irrelevant (§7.8): a loosen before the payee add still qualifies.
    """
    payee_add = any(
        op.action_kind == "PAYEE_ADD" and op.target_fingerprint == target_fingerprint
        for op in window
    )
    loosen = any(op.action_kind in LOOSEN_KINDS for op in window)
    return payee_add and loosen


def split_transfer(
    window: tuple[CommittedOp, ...],
    *,
    target_fingerprint: str,
) -> tuple[int, int]:
    """A1: count and total of 24h external transfers to one target."""
    hits = [
        op
        for op in window
        if op.action_kind in OUTFLOW_KINDS and op.target_fingerprint == target_fingerprint
    ]
    return len(hits), sum(op.amount_cents for op in hits)


def cumulative_outflow(window: tuple[CommittedOp, ...]) -> int:
    """Total 24h external outflow for the window's customer."""
    return sum(op.amount_cents for op in window if op.action_kind in OUTFLOW_KINDS)
