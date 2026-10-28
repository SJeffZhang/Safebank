"""AA split request: allocate, create, query projection (PRD §5.1 T-4, §7.7.1, §9.3 R8, §9.4 W5).

Pure functions — no database, IO, or randomness.
Does NOT initiate any funds transfer; payment events are a separate later component.
"""

from dataclasses import dataclass
from datetime import datetime

from safebank_contracts.codes import Code
from safebank_contracts.plan import Violation
from safebank_contracts.state import FUTURE_EFFECTS, BankState
from safebank_contracts.tools import CreateSplit
from safebank_contracts.views import SplitItem, SplitView

from .ids import derive
from .reserve import _business_time

# -- Internal domain types (to be wired into BankState by coordinator later) --


@dataclass(frozen=True)
class _SplitItemData:
    """Internal representation — mirrors contracts.SplitItem."""

    id: str
    payee_id: str
    payee_name: str
    amount_cents: int
    status: str  # "UNPAID" | "PAID"
    paid_at: datetime | None


@dataclass(frozen=True)
class _SplitRequestData:
    """Internal representation — mirrors contracts.SplitView fields."""

    id: str
    customer_id: str
    account_id: str
    total_cents: int
    include_self: bool
    self_share_cents: int
    created_at: datetime
    items: tuple[_SplitItemData, ...] = ()


# -- Allocate ----------------------------------------------------------------


def allocate(
    total_cents: int, participant_payee_ids: tuple[str, ...], include_self: bool
) -> tuple[tuple[int, ...], int]:
    """Allocate *total_cents* among participants (+ optionally self).

    Returns ``(amounts, self_share_cents)`` where *amounts* is one per
    participant (same order as *participant_payee_ids*), and
    *self_share_cents* is zero when *include_self* is False.

    Remainder (total not evenly divisible by head count) is given to self
    when *include_self* is True; otherwise to the first participant.
    """
    num_participants = len(participant_payee_ids)
    head_count = num_participants + int(include_self)
    if head_count <= 0 or total_cents < head_count:
        raise ValueError("head count must be positive and each share >= 1 cent")

    base = total_cents // head_count
    remainder = total_cents % head_count

    amounts = [base] * num_participants
    self_share = 0

    if include_self:
        self_share = base + remainder
    elif remainder > 0:
        amounts[0] += remainder

    return tuple(amounts), self_share


# -- Create split request ----------------------------------------------------


def apply_create_split_request(
    state: BankState,
    args: CreateSplit,
    now: datetime,
    ctx,  # ctx: Context
) -> tuple[BankState, tuple, tuple]:
    """Validate and record an AA split request.

    Returns ``(state, effects, errors)``.  *state* is never mutated
    (no ledger transfers).  On success *effects* contains a single
    ``InsertSplit`` effect and an ``OperationEvent``.
    """
    errors: list[Violation] = []

    # Account must belong to the customer.
    account = state.accounts.get(args.account_id)
    if account is None:
        errors.append(
            Violation(code=Code.NOT_FOUND, step_id=ctx.step_id, message="account not found")
        )
    elif account.customer_id != ctx.customer_id:
        errors.append(
            Violation(
                code=Code.OWNERSHIP_MISMATCH,
                step_id=ctx.step_id,
                message="account not owned by customer",
            )
        )

    # Every payee must belong to this customer.
    payee_objects: list = []
    for pid in args.participant_payee_ids:
        p = state.payees.get(pid)
        if p is None:
            errors.append(
                Violation(
                    code=Code.PAYEE_NOT_FOUND, step_id=ctx.step_id, message=f"payee {pid} not found"
                )
            )
        elif p.customer_id != ctx.customer_id:
            errors.append(
                Violation(
                    code=Code.OWNERSHIP_MISMATCH,
                    step_id=ctx.step_id,
                    message=f"payee {pid} not owned by customer",
                )
            )
        else:
            payee_objects.append(p)

    if errors:
        return state, (), tuple(errors)

    # Allocate.
    try:
        amounts, self_share = allocate(
            args.total_cents, args.participant_payee_ids, args.include_self
        )
    except ValueError as exc:
        return (
            state,
            (),
            (Violation(code=Code.SPLIT_INVALID, step_id=ctx.step_id, message=str(exc)),),
        )

    # Each share must be >= 1 cent (already guaranteed by allocate, but belt-and-suspenders).
    if any(a < 1 for a in amounts) or (args.include_self and self_share < 1):
        return (
            state,
            (),
            (
                Violation(
                    code=Code.SPLIT_INVALID,
                    step_id=ctx.step_id,
                    message="each share must be >= 1 cent",
                ),
            ),
        )

    now_biz = _business_time(now)
    request_id = derive(ctx.plan_id, ctx.step_id, "spl")

    items = tuple(
        SplitItem(
            id=derive(request_id, pid, "spi"),
            payee_id=pid,
            payee_name=po.name,
            amount_cents=amt,
            status="UNPAID",
            paid_at=None,
        )
        for pid, po, amt in zip(args.participant_payee_ids, payee_objects, amounts, strict=False)
    )

    split_view = SplitView(
        id=request_id,
        account_id=args.account_id,
        total_cents=args.total_cents,
        include_self=args.include_self,
        self_share_cents=self_share,
        received_cents=0,
        outstanding_cents=args.total_cents,
        created_at=now_biz,
        items=items,
    )

    InsertSplit = FUTURE_EFFECTS["InsertSplit"]
    from safebank_contracts.state import (
        Operation,
        OperationEvent,
    )

    op = OperationEvent(
        operation=Operation(
            event_id=derive(ctx.plan_id, ctx.step_id, "evt"),
            customer_id=ctx.customer_id,
            plan_id=ctx.plan_id,
            step_id=ctx.step_id,
            action_kind="SPLIT_CREATE",
            target_fingerprint=request_id,
            amount_cents=args.total_cents,
            occurred_at=now_biz,
        )
    )

    effects = (InsertSplit(split=split_view), op)
    return state, effects, ()


# -- Query projection --------------------------------------------------------


def _received_cents(items: tuple[SplitItem, ...]) -> int:
    return sum(it.amount_cents for it in items if it.status == "PAID")


def build_split_view(request: _SplitRequestData) -> SplitView:
    """Build a ``SplitView`` from internal split-request data."""
    received = _received_cents(tuple(request.items))
    return SplitView(
        id=request.id,
        account_id=request.account_id,
        total_cents=request.total_cents,
        include_self=request.include_self,
        self_share_cents=request.self_share_cents,
        received_cents=received,
        outstanding_cents=request.total_cents - received,
        created_at=request.created_at,
        items=tuple(
            SplitItem(
                id=it.id,
                payee_id=it.payee_id,
                payee_name=it.payee_name,
                amount_cents=it.amount_cents,
                status=it.status,  # type: ignore[arg-type]
                paid_at=it.paid_at,
            )
            for it in request.items
        ),
    )


def paginate_splits(
    requests: tuple[_SplitRequestData, ...],
    cursor: str | None,
    limit: int,
) -> tuple[tuple[SplitView, ...], str | None]:
    """Stable pagination over split requests (ordered by created_at, then id).

    Returns ``(page, next_cursor)``.
    """
    sorted_requests = sorted(requests, key=lambda r: (r.created_at, r.id))
    start = 0
    if cursor is not None:
        for i, r in enumerate(sorted_requests):
            if r.id == cursor:
                start = i + 1
                break
    page = sorted_requests[start : start + limit]
    next_cursor = (
        page[-1].id if len(page) == limit and start + limit < len(sorted_requests) else None
    )
    return tuple(build_split_view(r) for r in page), next_cursor
