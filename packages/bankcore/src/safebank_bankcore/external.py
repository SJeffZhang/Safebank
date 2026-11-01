"""Simulated external (system/demo) events: inbound credit, card/debit spend,
and AA split payments (PRD §5.1, §7.7, §8.5, §10.2 DEMO_EVENT).

These are pure, deterministic appliers for whitelisted synthetic jobs — they
are not write tools. Money moves between a customer account and the external
clearing account; counterparty names and memos are stored verbatim as untrusted
data (PRD §5.2). Additive module per ADR-011: ledger.py is not modified, so the
posting path is reimplemented locally with shared `account_deltas` arithmetic.
"""

from dataclasses import dataclass
from datetime import datetime

from pydantic import ValidationError
from safebank_contracts.codes import Code
from safebank_contracts.enums import CardStatus, Phase
from safebank_contracts.plan import Violation
from safebank_contracts.state import (
    Account,
    AppendTransaction,
    BankState,
    Counters,
    LedgerTransfer,
    Operation,
    OperationEvent,
    PostTransfer,
    Transaction,
)

from .ids import derive
from .ledger import account_deltas, replace_state

EXTERNAL_CLEARING_ACCOUNT = "acc_external_clearing"


@dataclass(frozen=True)
class ExternalCredit:
    account_id: str
    amount_cents: int
    counterparty: str | None = None
    memo: str | None = None
    kind: str = "SALARY"


@dataclass(frozen=True)
class ExternalDebit:
    account_id: str
    amount_cents: int
    counterparty: str | None = None
    memo: str | None = None
    kind: str = "CARD_SPEND"
    card_id: str | None = None
    channel: str | None = None
    merchant_id: str | None = None


@dataclass(frozen=True)
class SplitPayment:
    split_id: str
    item_id: str
    amount_cents: int


def _reject(state, ctx, code):
    return state, (), (Violation(code=code, step_id=ctx.step_id, message=code.value),)


def _operation(ctx, now, action_kind, target, amount):
    return OperationEvent(
        operation=Operation(
            event_id=derive(ctx.plan_id, ctx.step_id, "evt"),
            customer_id=ctx.customer_id,
            plan_id=ctx.plan_id,
            step_id=ctx.step_id,
            action_kind=action_kind,
            target_fingerprint=target,
            amount_cents=amount,
            occurred_at=now,
        )
    )


def _valid_amount_cents(amount) -> bool:
    return isinstance(amount, int) and not isinstance(amount, bool) and amount > 0


def _post_simple(
    state: BankState,
    ctx,
    now: datetime,
    *,
    debit_account_id: str,
    credit_account_id: str,
    amount_cents: int,
    kind: str,
    counterparty: str,
    memo: str | None,
    card_id: str | None,
    tag: str,
    merchant_id: str | None = None,
):
    """Post a POSTED transfer to/from the external clearing account.

    Mirrors ``ledger.post_transfer``'s POSTED path, but takes an explicit
    untrusted counterparty string instead of hardcoding ``外部结算``.
    """
    cmd = LedgerTransfer(
        id=derive(ctx.plan_id, ctx.step_id, tag),
        debit_account_id=debit_account_id,
        credit_account_id=credit_account_id,
        amount_cents=amount_cents,
        code=kind,
        phase=Phase.POSTED,
        pending_id=None,
        plan_id=ctx.plan_id,
        step_id=ctx.step_id,
        occurred_at=now,
        expires_at=None,
    )
    if cmd.id in state.transfers:
        if cmd == state.transfers[cmd.id]:
            return state, (), ()
        return _reject(state, ctx, Code.IDEMPOTENCY_CONFLICT)
    if debit_account_id == credit_account_id or cmd.occurred_at != now:
        return _reject(state, ctx, Code.CONTRACT_INVALID)
    if any(a not in state.accounts for a in (debit_account_id, credit_account_id)):
        return _reject(state, ctx, Code.NOT_FOUND)
    debit, credit = state.accounts[debit_account_id], state.accounts[credit_account_id]
    d, c = debit.model_dump(), credit.model_dump()
    delta = account_deltas(cmd, None)
    for field, amount in delta[debit.id].items():
        d[field] += amount
    for field, amount in delta[credit.id].items():
        c[field] += amount
    if (
        debit.customer_id is not None
        and d["debits_posted_cents"] + d["debits_pending_cents"] > d["credits_posted_cents"]
    ):
        return _reject(state, ctx, Code.INSUFFICIENT_FUNDS)
    try:
        new_debit, new_credit = Account(**d), Account(**c)
    except ValidationError:
        return _reject(state, ctx, Code.CONTRACT_INVALID)
    accounts = dict(state.accounts) | {debit.id: new_debit, credit.id: new_credit}
    transfers = dict(state.transfers) | {cmd.id: cmd}
    effects = [PostTransfer(transfer=cmd)]
    transactions = []
    category = "消费" if kind == "CARD_SPEND" else "其他"
    for before, after, direction in ((debit, new_debit, "OUT"), (credit, new_credit, "IN")):
        if before.customer_id is None:
            continue
        tx = Transaction(
            id=derive(cmd.id, cmd.step_id, "tx", direction),
            transfer_id=cmd.id,
            customer_id=before.customer_id,
            account_id=before.id,
            direction=direction,
            kind=kind,
            phase=Phase.POSTED,
            status="POSTED",
            amount_cents=amount_cents,
            balance_after_cents=after.balance_cents,
            available_after_cents=after.available_cents,
            counterparty=counterparty,
            category=category,
            memo=memo,
            card_id=card_id if direction == "OUT" else None,
            merchant_id=merchant_id,
            occurred_at=now,
        )
        transactions.append(tx)
        effects.append(AppendTransaction(transaction=tx))
    return (
        replace_state(
            state,
            accounts=accounts,
            transfers=transfers,
            transactions=(*state.transactions, *transactions),
        ),
        tuple(effects),
        (),
    )


def apply_external_credit(state: BankState, args: ExternalCredit, now: datetime, ctx):
    if not _valid_amount_cents(args.amount_cents):
        return _reject(state, ctx, Code.CONTRACT_INVALID)
    account = state.accounts.get(args.account_id)
    if account is None or account.customer_id != ctx.customer_id:
        return _reject(state, ctx, Code.NOT_FOUND)
    after, effects, errors = _post_simple(
        state,
        ctx,
        now,
        debit_account_id=EXTERNAL_CLEARING_ACCOUNT,
        credit_account_id=account.id,
        amount_cents=args.amount_cents,
        kind=args.kind or "SALARY",
        counterparty=args.counterparty if args.counterparty is not None else "外部结算",
        memo=args.memo,
        card_id=None,
        tag="excr",
    )
    if errors or not effects:
        return after, effects, errors
    return (
        after,
        (*effects, _operation(ctx, now, "EXTERNAL_CREDIT", account.id, args.amount_cents)),
        (),
    )


def apply_external_debit(state: BankState, args: ExternalDebit, now: datetime, ctx):
    if not _valid_amount_cents(args.amount_cents):
        return _reject(state, ctx, Code.CONTRACT_INVALID)
    account = state.accounts.get(args.account_id)
    if account is None or account.customer_id != ctx.customer_id:
        return _reject(state, ctx, Code.NOT_FOUND)
    card = state.cards.get(account.current_card_id) if account.current_card_id else None
    if args.card_id is not None:
        if card is None or card.id != args.card_id or card.account_id != account.id:
            return _reject(state, ctx, Code.OWNERSHIP_MISMATCH)
        channel = args.channel or "POS"
        control = {
            "ONLINE": "online",
            "OVERSEAS": "overseas",
            "ATM": "atm",
            "QUICKPASS": "quickpass",
        }
        if channel != "POS" and (
            channel not in control or not getattr(card.controls, control[channel])
        ):
            return _reject(state, ctx, Code.CARD_STATE_CONFLICT)
    if card:
        if card.status != CardStatus.ACTIVE:
            return _reject(state, ctx, Code.CARD_STATE_CONFLICT)
        single, daily = card.single_limit_cents, card.daily_limit_cents
    else:
        single, daily = account.single_limit_cents, account.daily_limit_cents
    if args.amount_cents > single:
        return _reject(state, ctx, Code.CARD_SINGLE_LIMIT)
    used = state.counters.daily_used_cents.get(account.id, 0)
    if used + args.amount_cents > daily:
        return _reject(state, ctx, Code.CARD_DAILY_LIMIT)
    after, effects, errors = _post_simple(
        state,
        ctx,
        now,
        debit_account_id=account.id,
        credit_account_id=EXTERNAL_CLEARING_ACCOUNT,
        amount_cents=args.amount_cents,
        kind=args.kind or "CARD_SPEND",
        counterparty=args.counterparty if args.counterparty is not None else "外部结算",
        memo=args.memo,
        card_id=card.id if card else None,
        tag="exdb",
        merchant_id=args.merchant_id,
    )
    if errors or not effects:
        return after, effects, errors
    used_map = dict(state.counters.daily_used_cents)
    used_map[account.id] = used + args.amount_cents
    after = replace_state(
        after,
        counters=Counters(
            daily_outflow_cents=dict(state.counters.daily_outflow_cents),
            rolling_outflow_cents=dict(state.counters.rolling_outflow_cents),
            daily_used_cents=used_map,
        ),
    )
    return (
        after,
        (*effects, _operation(ctx, now, "EXTERNAL_DEBIT", account.id, args.amount_cents)),
        (),
    )


def apply_split_payment(state: BankState, args: SplitPayment, now: datetime, ctx):
    if not _valid_amount_cents(args.amount_cents):
        return _reject(state, ctx, Code.CONTRACT_INVALID)
    split = next((s for s in state.splits if s.id == args.split_id), None)
    if split is None:
        return _reject(state, ctx, Code.NOT_FOUND)
    item = next((it for it in split.items if it.id == args.item_id), None)
    if item is None:
        return _reject(state, ctx, Code.SPLIT_INVALID)
    if item.status == "PAID":
        return _reject(state, ctx, Code.SPLIT_INVALID)
    if args.amount_cents != item.amount_cents:
        return _reject(state, ctx, Code.SPLIT_INVALID)
    account = state.accounts.get(split.account_id)
    if account is None or account.customer_id != ctx.customer_id:
        return _reject(state, ctx, Code.NOT_FOUND)
    after, effects, errors = _post_simple(
        state,
        ctx,
        now,
        debit_account_id=EXTERNAL_CLEARING_ACCOUNT,
        credit_account_id=account.id,
        amount_cents=args.amount_cents,
        kind="SPLIT_PAYMENT",
        counterparty=item.payee_name,
        memo=None,
        card_id=None,
        tag="splp",
    )
    if errors or not effects:
        return after, effects, errors
    new_items = tuple(
        it.model_copy(update={"status": "PAID", "paid_at": now}) if it.id == item.id else it
        for it in split.items
    )
    new_split = split.model_copy(
        update={
            "items": new_items,
            "received_cents": split.received_cents + args.amount_cents,
            "outstanding_cents": split.outstanding_cents - args.amount_cents,
        }
    )
    splits = tuple(new_split if s.id == split.id else s for s in state.splits)
    after = replace_state(after, splits=splits)
    return after, (*effects, _operation(ctx, now, "SPLIT_PAYMENT", split.id, args.amount_cents)), ()
