"""TB-1/2/3/5/9: posted/pending accounting, adapted from TigerBeetle docs.

Sources: https://docs.tigerbeetle.com/reference/account/
https://docs.tigerbeetle.com/coding/two-phase-transfers/ (read 2026-10-23).
No upstream code copied. All operations return a new immutable state.
"""

from datetime import datetime

from pydantic import ValidationError
from safebank_contracts.codes import Code
from safebank_contracts.enums import Phase
from safebank_contracts.plan import Violation
from safebank_contracts.state import (
    Account,
    AppendTransaction,
    BankState,
    LedgerTransfer,
    PostTransfer,
    Transaction,
)

from .ids import derive


def replace_state(state: BankState, **updates) -> BankState:
    return BankState.model_validate(state.model_dump() | updates)


def post_transfer(
    state: BankState,
    cmd: LedgerTransfer,
    now: datetime,
    *,
    memo: str | None = None,
    payee_id: str | None = None,
    card_id: str | None = None,
    merchant_id: str | None = None,
    category: str | None = None,
):
    def reject(code: Code):
        return state, (), (Violation(code=code, step_id=cmd.step_id, message=code.value),)

    if cmd.id in state.transfers:
        if cmd == state.transfers[cmd.id]:
            return state, (), ()
        return reject(Code.IDEMPOTENCY_CONFLICT)
    if cmd.debit_account_id == cmd.credit_account_id or cmd.occurred_at != now:
        return reject(Code.CONTRACT_INVALID)
    if any(a not in state.accounts for a in (cmd.debit_account_id, cmd.credit_account_id)):
        return reject(Code.NOT_FOUND)
    debit, credit = state.accounts[cmd.debit_account_id], state.accounts[cmd.credit_account_id]
    d, c = debit.model_dump(), credit.model_dump()
    original = None
    if cmd.phase in (Phase.POST_PENDING, Phase.VOID_PENDING):
        original = state.transfers.get(cmd.pending_id)
        if original is None or original.phase != Phase.PENDING:
            return reject(Code.DEPENDENCY_UNMET)
        if any(t.pending_id == original.id for t in state.transfers.values()):
            return reject(Code.PENDING_ALREADY_RESOLVED)
        if (cmd.debit_account_id, cmd.credit_account_id) != (
            original.debit_account_id,
            original.credit_account_id,
        ):
            return reject(Code.CONTRACT_INVALID)
        if cmd.amount_cents > original.amount_cents:
            return reject(Code.CONTRACT_INVALID)
        if cmd.phase == Phase.POST_PENDING and original.expires_at and now >= original.expires_at:
            return reject(Code.PENDING_EXPIRED)
        if cmd.phase == Phase.VOID_PENDING and cmd.amount_cents != original.amount_cents:
            return reject(Code.CONTRACT_INVALID)
    elif cmd.pending_id is not None:
        return reject(Code.CONTRACT_INVALID)
    if cmd.phase == Phase.PENDING:
        if cmd.expires_at is None or cmd.expires_at <= now:
            return reject(Code.CONTRACT_INVALID)
    delta = account_deltas(cmd, original)
    for field, amount in delta[debit.id].items():
        d[field] += amount
    for field, amount in delta[credit.id].items():
        c[field] += amount
    if (
        debit.customer_id is not None
        and d["debits_posted_cents"] + d["debits_pending_cents"] > d["credits_posted_cents"]
    ):
        return reject(Code.INSUFFICIENT_FUNDS)
    try:
        new_debit, new_credit = Account(**d), Account(**c)
    except ValidationError:
        return reject(Code.CONTRACT_INVALID)
    accounts = dict(state.accounts) | {debit.id: new_debit, credit.id: new_credit}
    transfers = dict(state.transfers) | {cmd.id: cmd}
    effects = [PostTransfer(transfer=cmd)]
    transactions = []
    status = {
        Phase.POSTED: "POSTED",
        Phase.PENDING: "PENDING",
        Phase.POST_PENDING: "SETTLED",
        Phase.VOID_PENDING: "RELEASED",
    }[cmd.phase]
    for before, after, direction, other in (
        (debit, new_debit, "OUT", credit),
        (credit, new_credit, "IN", debit),
    ):
        if before.customer_id is None:
            continue
        tx = Transaction(
            id=derive(cmd.id, cmd.step_id, "tx", direction),
            transfer_id=cmd.id,
            customer_id=before.customer_id,
            account_id=before.id,
            direction=direction,
            kind=cmd.code,
            phase=cmd.phase,
            status=status,
            amount_cents=cmd.amount_cents,
            balance_after_cents=after.balance_cents,
            available_after_cents=after.available_cents,
            counterparty=state.customers[other.customer_id].display_name
            if other.customer_id
            else "外部结算",
            category=category
            if category is not None
            else ("转账" if cmd.code.startswith(("P2P", "SELF")) else "其他"),
            merchant_id=merchant_id if direction == "OUT" else None,
            memo=memo,
            payee_id=payee_id if direction == "OUT" else None,
            card_id=card_id if direction == "OUT" else None,
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


def account_deltas(cmd: LedgerTransfer, original: LedgerTransfer | None):
    """Pure arithmetic consumed by both shadow accounting and SQL persistence."""
    debit, credit = {}, {}
    if original is not None:
        debit["debits_pending_cents"] = -original.amount_cents
        credit["credits_pending_cents"] = -original.amount_cents
    if cmd.phase == Phase.PENDING:
        debit["debits_pending_cents"] = cmd.amount_cents
        credit["credits_pending_cents"] = cmd.amount_cents
    elif cmd.phase in (Phase.POSTED, Phase.POST_PENDING):
        debit["debits_posted_cents"] = cmd.amount_cents
        credit["credits_posted_cents"] = cmd.amount_cents
    return {cmd.debit_account_id: debit, cmd.credit_account_id: credit}
