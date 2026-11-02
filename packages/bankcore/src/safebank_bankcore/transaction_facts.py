"""Shared pure classification for persisted transaction facts, including old events."""

from collections.abc import Collection, Mapping

from safebank_contracts.state import LedgerTransfer, Transaction

_EXTERNAL_KINDS = frozenset({"P2P_INTERNAL", "P2P_EXTERNAL", "TRANSFER_EXTERNAL"})


def is_external_transfer(
    tx: Transaction, transfers: Mapping[str, LedgerTransfer], owned_account_ids: Collection[str]
) -> bool:
    """Classify one owned outgoing row; a legacy schedule needs its real ledger leg.

    Historical SCHEDULED_TRANSFER did not distinguish self from other targets.
    A matching credit account owned by this customer proves a self transfer.
    Missing/mismatched historical ledger evidence stays conservative for limits.
    No payee label, memo, supplied scope or current instruction can rewrite it.
    """
    if tx.direction != "OUT" or tx.account_id not in owned_account_ids:
        return False
    if tx.kind in _EXTERNAL_KINDS:
        return True
    if tx.kind != "SCHEDULED_TRANSFER":
        return False
    transfer = transfers.get(tx.transfer_id)
    return (
        transfer is None
        or transfer.debit_account_id != tx.account_id
        or transfer.credit_account_id not in owned_account_ids
    )
