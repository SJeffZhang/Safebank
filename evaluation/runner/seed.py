"""E0b T1: load an evaluation seed JSON into the real bank backend (tmpfs PG).

Writes customers, state_versions, accounts (plus one internal clearing account
for the synthetic transaction transfers), cards, payees (recomputing
``target_fingerprint`` with the real gateway fingerprint algorithm), merchants
and the seed's transaction history. Every seed transaction gets a synthetic
POSTED ledger transfer so the ``transactions.transfer_id`` NOT NULL foreign key
is satisfied without running the ledger engine.

Fail-closed contract:

- every cross-reference, limit chain and balance is validated before anything
  is written;
- a non-empty database is never overwritten (reload raises ``SeedError``);
- billings / standing_instructions are outside T1 and are rejected loudly
  rather than silently dropped.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app import models as m
from app.state import fingerprint
from sqlalchemy import select

INTERNAL_CLEARING_ID = "acc_external_clearing"
INTERNAL_CLEARING_NO = "internal-external"
SEED_PLAN_ID = "pln_e0b_seed"
DEFAULT_CATEGORY = "其他"
DEFAULT_MCC = "0000"

_REQUIRED_KEYS = (
    "fixture_id",
    "dataset_revision",
    "sim_now",
    "customers",
    "accounts",
    "cards",
    "payees",
    "merchants",
    "billings",
    "standing_instructions",
    "transactions",
)


class SeedError(Exception):
    """The seed cannot be loaded safely; nothing was written."""


def read_seed(path: str | Path) -> dict[str, Any]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SeedError(f"seed root must be a JSON object, got {type(raw).__name__}")
    missing = [key for key in _REQUIRED_KEYS if key not in raw]
    if missing:
        raise SeedError(f"seed is missing required keys: {', '.join(missing)}")
    return raw


def _parse_dt(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise SeedError(f"{label} must be an ISO-8601 string, got {value!r}")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise SeedError(f"{label} is not a valid timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise SeedError(f"{label} has no timezone: {value!r}")
    return parsed


def _require_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SeedError(f"{label} must be an integer, got {value!r}")
    return value


def _non_empty_str(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise SeedError(f"{label} must be a non-empty string, got {value!r}")
    return value


def _unique_ids(rows: list[dict[str, Any]], label: str) -> set[str]:
    seen: set[str] = set()
    for row in rows:
        value = row.get("id")
        if not isinstance(value, str) or not value:
            raise SeedError(f"{label} entry is missing a non-empty string id: {row!r}")
        if value in seen:
            raise SeedError(f"duplicate {label} id {value!r}")
        seen.add(value)
    return seen


def _prepare(seed: dict[str, Any], fingerprint_key: str) -> dict[str, Any]:
    if seed.get("billings") or seed.get("standing_instructions"):
        raise SeedError(
            "billings / standing_instructions are outside E0b T1 scope; "
            "refusing to load a seed that contains them"
        )
    sim_now = _parse_dt(seed["sim_now"], "sim_now")
    dataset_revision = _non_empty_str(seed["dataset_revision"], "dataset_revision")

    customers = seed["customers"]
    accounts = seed["accounts"]
    cards = seed["cards"]
    payees = seed["payees"]
    merchants = seed["merchants"]
    transactions = seed["transactions"]

    customer_ids = _unique_ids(customers, "customer")
    account_ids = _unique_ids(accounts, "account")
    card_ids = _unique_ids(cards, "card")
    payee_ids = _unique_ids(payees, "payee")
    merchant_ids = _unique_ids(merchants, "merchant")
    _unique_ids(transactions, "transaction")

    cards_by_id = {c["id"]: c for c in cards}
    accounts_by_id = {a["id"]: a for a in accounts}

    # --- cards -------------------------------------------------------------
    prepared_cards: list[dict[str, Any]] = []
    active_per_account: dict[str, int] = {}
    for card in cards:
        card_id = card["id"]
        account_id = card.get("account_id")
        if account_id not in account_ids:
            raise SeedError(f"card {card_id} references unknown account {account_id!r}")
        status = card.get("status")
        if status not in ("ACTIVE", "FROZEN", "LOST"):
            raise SeedError(f"card {card_id} has invalid status {status!r}")
        single = _require_int(card.get("single_limit_cents"), f"card {card_id} single_limit_cents")
        daily = _require_int(card.get("daily_limit_cents"), f"card {card_id} daily_limit_cents")
        max_single = _require_int(
            card.get("max_single_limit_cents"), f"card {card_id} max_single_limit_cents"
        )
        max_daily = _require_int(
            card.get("max_daily_limit_cents"), f"card {card_id} max_daily_limit_cents"
        )
        if not (single > 0 and daily >= single and daily <= max_daily and single <= max_single):
            raise SeedError(f"card {card_id} has an invalid limit chain")
        controls = card.get("controls")
        if not isinstance(controls, dict):
            raise SeedError(f"card {card_id} controls must be an object")
        last4 = card.get("last4")
        if not isinstance(last4, str) or len(last4) != 4:
            raise SeedError(f"card {card_id} last4 must be a 4-character string")
        replaces_id = card.get("replaces_id")
        if replaces_id is not None and replaces_id not in card_ids:
            raise SeedError(f"card {card_id} references unknown replaces card {replaces_id!r}")
        prepared_cards.append(
            {
                "id": card_id,
                "account_id": account_id,
                "last4": last4,
                "status": status,
                "single_limit_cents": single,
                "daily_limit_cents": daily,
                "max_single_limit_cents": max_single,
                "max_daily_limit_cents": max_daily,
                "controls": controls,
                "replaces_id": replaces_id,
                "created_at": _parse_dt(card.get("created_at"), f"card {card_id} created_at"),
            }
        )
        if status != "LOST":
            active_per_account[account_id] = active_per_account.get(account_id, 0) + 1
    for account_id, count in active_per_account.items():
        if count > 1:
            raise SeedError(f"account {account_id} has {count} non-LOST cards")

    # --- accounts ----------------------------------------------------------
    account_nos: set[str] = set()
    prepared_accounts: list[dict[str, Any]] = []
    for account in accounts:
        account_id = account["id"]
        number = _non_empty_str(account.get("account_no"), f"account {account_id} account_no")
        if number in account_nos:
            raise SeedError(f"duplicate account_no {number!r}")
        account_nos.add(number)
        customer_id = account.get("customer_id")
        kind = account.get("type")
        if kind not in ("CHECKING", "SAVINGS", "INTERNAL"):
            raise SeedError(f"account {account_id} has invalid type {kind!r}")
        if (customer_id is None) != (kind == "INTERNAL"):
            raise SeedError(
                f"account {account_id}: type {kind!r} and customer_id {customer_id!r} disagree"
            )
        if customer_id is not None and customer_id not in customer_ids:
            raise SeedError(f"account {account_id} references unknown customer {customer_id!r}")
        single = _require_int(
            account.get("single_limit_cents"), f"account {account_id} single_limit_cents"
        )
        daily = _require_int(
            account.get("daily_limit_cents"), f"account {account_id} daily_limit_cents"
        )
        if single <= 0 or daily < single:
            raise SeedError(f"account {account_id} has an invalid limit chain")
        credits = _require_int(
            account.get("credits_posted_cents"), f"account {account_id} credits_posted_cents"
        )
        debits = _require_int(
            account.get("debits_posted_cents"), f"account {account_id} debits_posted_cents"
        )
        pending_debits = _require_int(
            account.get("debits_pending_cents"), f"account {account_id} debits_pending_cents"
        )
        pending_credits = _require_int(
            account.get("credits_pending_cents"), f"account {account_id} credits_pending_cents"
        )
        for value in (credits, debits, pending_debits, pending_credits):
            if value < 0:
                raise SeedError(f"account {account_id} has a negative balance field")
        if customer_id is not None and debits + pending_debits > credits:
            raise SeedError(f"account {account_id} would overdraw")
        ledger_balance = _require_int(
            account.get("ledger_balance_cents"), f"account {account_id} ledger_balance_cents"
        )
        if credits - debits != ledger_balance:
            raise SeedError(
                f"account {account_id} ledger balance {ledger_balance} != credits-debits "
                f"{credits - debits}"
            )
        current_card_id = account.get("current_card_id")
        if current_card_id is not None:
            if current_card_id not in card_ids:
                raise SeedError(f"account {account_id} references unknown card {current_card_id!r}")
            if cards_by_id[current_card_id].get("account_id") != account_id:
                raise SeedError(
                    f"account {account_id} current card {current_card_id} belongs to another"
                    " account"
                )
        prepared_accounts.append(
            {
                "id": account_id,
                "customer_id": customer_id,
                "type": kind,
                "display_name": _non_empty_str(
                    account.get("display_name"), f"account {account_id} display_name"
                ),
                "account_no": number,
                "currency": account.get("currency", "CNY"),
                "ledger": account.get("ledger", "CNY"),
                "flags": account.get("flags", 1),
                "current_card_id": current_card_id,
                "single_limit_cents": single,
                "daily_limit_cents": daily,
                "debits_posted_cents": debits,
                "credits_posted_cents": credits,
                "debits_pending_cents": pending_debits,
                "credits_pending_cents": pending_credits,
            }
        )

    # --- payees ------------------------------------------------------------
    prepared_payees: list[dict[str, Any]] = []
    seen_fingerprints: set[tuple[str, str]] = set()
    for payee in payees:
        payee_id = payee["id"]
        customer_id = payee.get("customer_id")
        if customer_id not in customer_ids:
            raise SeedError(f"payee {payee_id} references unknown customer {customer_id!r}")
        number = _non_empty_str(payee.get("account_no"), f"payee {payee_id} account_no")
        target_account_id = payee.get("target_account_id")
        if target_account_id is not None:
            if target_account_id not in account_ids:
                raise SeedError(
                    f"payee {payee_id} references unknown account {target_account_id!r}"
                )
            if accounts_by_id[target_account_id].get("customer_id") is None:
                raise SeedError(
                    f"payee {payee_id} targets an internal account {target_account_id!r}"
                )
        computed = fingerprint(number, fingerprint_key)
        key = (customer_id, computed)
        if key in seen_fingerprints:
            raise SeedError(f"payee {payee_id} duplicates another payee's target fingerprint")
        seen_fingerprints.add(key)
        prepared_payees.append(
            {
                "id": payee_id,
                "customer_id": customer_id,
                "name": _non_empty_str(payee.get("name"), f"payee {payee_id} name"),
                "alias": payee.get("alias"),
                "phone": payee.get("phone"),
                "bank_name": _non_empty_str(payee.get("bank_name"), f"payee {payee_id} bank_name"),
                "account_no": number,
                "target_account_id": target_account_id,
                "target_fingerprint": computed,
                "created_at": _parse_dt(payee.get("created_at"), f"payee {payee_id} created_at"),
            }
        )

    # --- merchants ---------------------------------------------------------
    prepared_merchants: list[dict[str, Any]] = []
    for merchant in merchants:
        merchant_id = merchant["id"]
        prepared_merchants.append(
            {
                "id": merchant_id,
                "name": _non_empty_str(merchant.get("name"), f"merchant {merchant_id} name"),
                "mcc": merchant.get("mcc", DEFAULT_MCC),
                "category": _non_empty_str(
                    merchant.get("category"), f"merchant {merchant_id} category"
                ),
                "usage_expected": bool(merchant.get("usage_expected", False)),
            }
        )

    # --- transactions + synthetic transfers --------------------------------
    prepared_transfers: list[dict[str, Any]] = []
    prepared_transactions: list[dict[str, Any]] = []
    latest_by_account: dict[str, tuple[datetime, str, int]] = {}
    for txn in transactions:
        txn_id = txn["id"]
        account_id = txn.get("account_id")
        if account_id not in account_ids:
            raise SeedError(f"transaction {txn_id} references unknown account {account_id!r}")
        owner = accounts_by_id[account_id].get("customer_id")
        if owner is None:
            raise SeedError(f"transaction {txn_id} references an internal account {account_id!r}")
        direction = txn.get("direction")
        if direction not in ("IN", "OUT"):
            raise SeedError(f"transaction {txn_id} has invalid direction {direction!r}")
        amount = _require_int(txn.get("amount_cents"), f"transaction {txn_id} amount_cents")
        if amount <= 0:
            raise SeedError(f"transaction {txn_id} amount_cents must be positive")
        balance_after = _require_int(
            txn.get("balance_after_cents"), f"transaction {txn_id} balance_after_cents"
        )
        available_after = _require_int(
            txn.get("available_after_cents", balance_after),
            f"transaction {txn_id} available_after_cents",
        )
        if balance_after < 0 or available_after < 0:
            raise SeedError(f"transaction {txn_id} has a negative balance")
        payee_id = txn.get("payee_id")
        if payee_id is not None and payee_id not in payee_ids:
            raise SeedError(f"transaction {txn_id} references unknown payee {payee_id!r}")
        merchant_id = txn.get("merchant_id")
        if merchant_id is not None and merchant_id not in merchant_ids:
            raise SeedError(f"transaction {txn_id} references unknown merchant {merchant_id!r}")
        counterparty = _non_empty_str(txn.get("counterparty"), f"transaction {txn_id} counterparty")
        kind = _non_empty_str(txn.get("kind"), f"transaction {txn_id} kind")
        occurred_at = _parse_dt(txn.get("occurred_at"), f"transaction {txn_id} occurred_at")
        previous = latest_by_account.get(account_id)
        if previous is None or (occurred_at, txn_id) > (previous[0], previous[1]):
            latest_by_account[account_id] = (occurred_at, txn_id, balance_after)
        if direction == "OUT":
            debit_account_id, credit_account_id = account_id, INTERNAL_CLEARING_ID
        else:
            debit_account_id, credit_account_id = INTERNAL_CLEARING_ID, account_id
        prepared_transfers.append(
            {
                "id": f"trf_seed_{txn_id}",
                "debit_account_id": debit_account_id,
                "credit_account_id": credit_account_id,
                "amount_cents": amount,
                "code": "SEED",
                "phase": "POSTED",
                "pending_id": None,
                "reverses_id": None,
                "plan_id": SEED_PLAN_ID,
                "event_id": None,
                "step_id": txn_id,
                "leg": "main",
                "occurred_at": occurred_at,
                "expires_at": None,
            }
        )
        prepared_transactions.append(
            {
                "id": txn_id,
                "transfer_id": f"trf_seed_{txn_id}",
                "customer_id": owner,
                "account_id": account_id,
                "direction": direction,
                "kind": kind,
                "phase": "POSTED",
                "status": "POSTED",
                "amount_cents": amount,
                "balance_after_cents": balance_after,
                "available_after_cents": available_after,
                "counterparty": counterparty,
                "memo": txn.get("memo"),
                "category": txn.get("category", DEFAULT_CATEGORY),
                "merchant_id": merchant_id,
                "payee_id": payee_id,
                "card_id": txn.get("card_id"),
                "channel": txn.get("channel"),
                "occurred_at": occurred_at,
            }
        )

    for account_id, (_, txn_id, balance_after) in latest_by_account.items():
        ledger = accounts_by_id[account_id]["ledger_balance_cents"]
        if balance_after != ledger:
            raise SeedError(
                f"account {account_id}: latest transaction {txn_id} balance "
                f"{balance_after} != ledger balance {ledger}"
            )

    if prepared_transactions:
        out_total = sum(t["amount_cents"] for t in prepared_transactions if t["direction"] == "OUT")
        in_total = sum(t["amount_cents"] for t in prepared_transactions if t["direction"] == "IN")
        prepared_accounts.append(
            {
                "id": INTERNAL_CLEARING_ID,
                "customer_id": None,
                "type": "INTERNAL",
                "display_name": "外部清算",
                "account_no": INTERNAL_CLEARING_NO,
                "currency": "CNY",
                "ledger": "CNY",
                "flags": 0,
                "current_card_id": None,
                "single_limit_cents": 1,
                "daily_limit_cents": 1,
                "debits_posted_cents": in_total,
                "credits_posted_cents": out_total,
                "debits_pending_cents": 0,
                "credits_pending_cents": 0,
            }
        )

    # --- customers ---------------------------------------------------------
    prepared_customers: list[dict[str, Any]] = []
    for customer in customers:
        customer_id = customer["id"]
        risk_level = customer.get("risk_level")
        if risk_level is not None:
            risk_level = _require_int(risk_level, f"customer {customer_id} risk_level")
        risk_assessed_at = customer.get("risk_assessed_at")
        if risk_assessed_at is not None:
            risk_assessed_at = _parse_dt(
                risk_assessed_at, f"customer {customer_id} risk_assessed_at"
            )
        prepared_customers.append(
            {
                "id": customer_id,
                "display_name": _non_empty_str(
                    customer.get("display_name"), f"customer {customer_id} display_name"
                ),
                "phone": _non_empty_str(customer.get("phone"), f"customer {customer_id} phone"),
                "risk_level": risk_level,
                "risk_assessed_at": risk_assessed_at,
                "risk_valid_until": customer.get("risk_valid_until"),
            }
        )

    return {
        "sim_now": sim_now,
        "dataset_revision": dataset_revision,
        "customers": prepared_customers,
        "accounts": prepared_accounts,
        "cards": prepared_cards,
        "payees": prepared_payees,
        "merchants": prepared_merchants,
        "transfers": prepared_transfers,
        "transactions": prepared_transactions,
    }


async def load_seed(conn, seed: dict[str, Any], *, fingerprint_key: str) -> None:
    """Validate ``seed`` and write it into ``conn`` in one transaction.

    Raises ``SeedError`` (leaving the database unchanged) when the seed is
    invalid or the database is not empty.
    """
    prepared = _prepare(seed, fingerprint_key)
    async with conn.begin():
        existing = await conn.scalar(select(m.customers.c.id).limit(1))
        if existing is not None:
            raise SeedError("database is not empty; refusing to overwrite existing data")
        for customer in prepared["customers"]:
            await conn.execute(m.customers.insert().values(**customer))
            await conn.execute(
                m.state_versions.insert().values(customer_id=customer["id"], version=0)
            )
        for account in prepared["accounts"]:
            await conn.execute(m.accounts.insert().values(**account))
        for card in prepared["cards"]:
            await conn.execute(m.cards.insert().values(**card))
        for payee in prepared["payees"]:
            await conn.execute(m.payees.insert().values(**payee))
        for merchant in prepared["merchants"]:
            await conn.execute(m.merchants.insert().values(**merchant))
        for transfer in prepared["transfers"]:
            await conn.execute(m.transfers.insert().values(**transfer))
        for txn in prepared["transactions"]:
            await conn.execute(m.transactions.insert().values(**txn))
        await conn.execute(
            m.clock.insert().values(
                id=1,
                sim_now=prepared["sim_now"],
                version=1,
                dataset_revision=prepared["dataset_revision"],
            )
        )
