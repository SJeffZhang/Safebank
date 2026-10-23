"""PRD §7.7.1: the only state loader and financial persistence implementation."""

import hashlib
import hmac
from datetime import timedelta
from zoneinfo import ZoneInfo

from safebank_bankcore.ledger import account_deltas
from safebank_bankcore.scope import scope_for
from safebank_contracts.state import (
    Account,
    AppendTransaction,
    BankState,
    Card,
    Counters,
    Customer,
    DirectoryEntry,
    InsertPayee,
    LedgerTransfer,
    Operation,
    OperationEvent,
    Payee,
    PostTransfer,
    StateScope,
    Transaction,
    UpsertCard,
)
from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert

from . import models as m


def fingerprint(account_no: str, key: str) -> str:
    return hmac.new(key.encode(), account_no.encode(), hashlib.sha256).hexdigest()


async def resolve_scope(conn, customer_id: str, steps, key: str) -> StateScope:
    # Ownership/target identity is immutable; execution re-resolves after customer locks.
    directory = {}
    payees = (
        (await conn.execute(select(m.payees).where(m.payees.c.customer_id == customer_id)))
        .mappings()
        .all()
    )
    numbers = {s.args.account_no for s in steps if s.tool == "add_payee"}
    for row in payees:
        peer = None
        if row["target_account_id"]:
            peer = await conn.scalar(
                select(m.accounts.c.customer_id).where(m.accounts.c.id == row["target_account_id"])
            )
        directory["payee:" + row["id"]] = DirectoryEntry(
            target_fingerprint=row["target_fingerprint"],
            target_account_id=row["target_account_id"],
            customer_id=peer,
        )
    for row in (
        await conn.execute(
            select(m.accounts.c.id, m.accounts.c.account_no, m.accounts.c.customer_id).where(
                or_(m.accounts.c.customer_id == customer_id, m.accounts.c.account_no.in_(numbers))
            )
        )
    ).mappings():
        entry = DirectoryEntry(
            target_fingerprint=fingerprint(row["account_no"], key),
            target_account_id=row["id"],
            customer_id=row["customer_id"],
        )
        directory["account:" + row["id"]] = entry
        if row["account_no"] in numbers:
            directory["number:" + row["account_no"]] = entry
    for number in numbers:
        directory.setdefault(
            "number:" + number,
            DirectoryEntry(
                target_fingerprint=fingerprint(number, key),
                target_account_id=None,
                customer_id=None,
            ),
        )
    return scope_for(customer_id, steps, directory)


async def load_bank_state(conn, scope: StateScope, now, *, customer_id: str) -> BankState:
    now = now.astimezone(ZoneInfo("Asia/Shanghai"))
    clock = (await conn.execute(select(m.clock).where(m.clock.c.id == 1))).mappings().one()
    ids = scope.customer_ids
    accounts = [
        Account(**r)
        for r in (
            await conn.execute(
                select(m.accounts).where(
                    or_(m.accounts.c.customer_id.in_(ids), m.accounts.c.customer_id.is_(None))
                )
            )
        ).mappings()
    ]
    owned_ids = [a.id for a in accounts if a.customer_id is not None]
    owner_ids = [a.id for a in accounts if a.customer_id == customer_id]
    customers = {}
    for row in (
        await conn.execute(select(m.customers).where(m.customers.c.id.in_(ids)))
    ).mappings():
        # Other customers' contact/profile details never enter the snapshot.
        data = dict(row)
        if row["id"] != customer_id:
            data.update(phone="", risk_level=None, risk_assessed_at=None, risk_valid_until=None)
        customers[row["id"]] = Customer(**data)
    cards = [
        Card(**r)
        for r in (
            await conn.execute(select(m.cards).where(m.cards.c.account_id.in_(owned_ids)))
        ).mappings()
    ]
    payees = [
        Payee(**r)
        for r in (
            await conn.execute(select(m.payees).where(m.payees.c.customer_id == customer_id))
        ).mappings()
    ]
    transfers = [
        LedgerTransfer(**r)
        for r in (
            await conn.execute(
                select(m.transfers).where(
                    or_(
                        m.transfers.c.debit_account_id.in_(owned_ids),
                        m.transfers.c.credit_account_id.in_(owned_ids),
                    )
                )
            )
        ).mappings()
    ]
    # Keep history for public curves and per-window counters; only the requesting customer's views.
    transactions = [
        Transaction(**r)
        for r in (
            await conn.execute(
                select(m.transactions)
                .where(m.transactions.c.customer_id == customer_id)
                .order_by(m.transactions.c.sequence)
            )
        ).mappings()
    ]
    daily_outflow, rolling_outflow, daily_used = {}, {}, {}
    for tx in transactions:
        posted = tx.phase in ("POSTED", "POST_PENDING")
        day = tx.occurred_at.astimezone(now.tzinfo).date() == now.date()
        external = posted and tx.direction == "OUT" and tx.kind in ("P2P_INTERNAL", "P2P_EXTERNAL")
        if external and now - timedelta(hours=24) <= tx.occurred_at <= now:
            rolling_outflow[tx.customer_id] = (
                rolling_outflow.get(tx.customer_id, 0) + tx.amount_cents
            )
        if external and day:
            daily_outflow[tx.customer_id] = daily_outflow.get(tx.customer_id, 0) + tx.amount_cents
        if posted and day and tx.direction == "OUT" and (external or tx.kind == "CARD_SPEND"):
            daily_used[tx.account_id] = daily_used.get(tx.account_id, 0) + tx.amount_cents
    ops = [
        Operation(**r)
        for r in (
            await conn.execute(
                select(m.operation_events)
                .where(
                    m.operation_events.c.customer_id == customer_id,
                    m.operation_events.c.occurred_at >= now - timedelta(days=7),
                )
                .order_by(m.operation_events.c.commit_seq)
            )
        ).mappings()
    ]
    versions = dict(
        (
            await conn.execute(
                select(m.state_versions.c.customer_id, m.state_versions.c.version).where(
                    m.state_versions.c.customer_id.in_(ids)
                )
            )
        ).all()
    )
    if customer_id not in versions or not owner_ids:
        raise LookupError("customer not found")
    return BankState(
        sim_now=now,
        clock_version=clock["version"],
        versions=versions,
        accounts={a.id: a for a in accounts},
        customers=customers,
        cards={c.id: c for c in cards},
        payees={p.id: p for p in payees},
        transfers={t.id: t for t in transfers},
        transactions=transactions,
        counters=Counters(
            daily_outflow_cents=daily_outflow,
            rolling_outflow_cents=rolling_outflow,
            daily_used_cents=daily_used,
        ),
        directory=scope.directory,
        ops_7d=ops,
        dataset_revision=clock["dataset_revision"],
    )


async def persist(conn, effects) -> None:
    for effect in effects:
        if isinstance(effect, PostTransfer):
            transfer = effect.transfer
            original = None
            if transfer.pending_id:
                row = (
                    (
                        await conn.execute(
                            select(m.transfers).where(m.transfers.c.id == transfer.pending_id)
                        )
                    )
                    .mappings()
                    .one()
                )
                original = LedgerTransfer(**row)
            for account_id, delta in sorted(account_deltas(transfer, original).items()):
                await conn.execute(
                    update(m.accounts)
                    .where(m.accounts.c.id == account_id)
                    .values(**{k: m.accounts.c[k] + v for k, v in delta.items()})
                )
            await conn.execute(m.transfers.insert().values(**transfer.model_dump()))
        elif isinstance(effect, AppendTransaction):
            data = effect.transaction.model_dump(exclude={"sequence"})
            await conn.execute(m.transactions.insert().values(**data))
        elif isinstance(effect, UpsertCard):
            data = effect.card.model_dump(mode="python")
            statement = insert(m.cards).values(**data)
            await conn.execute(
                statement.on_conflict_do_update(
                    index_elements=["id"],
                    set_={
                        k: statement.excluded[k]
                        for k in data
                        if k not in ("id", "account_id", "created_at", "replaces_id")
                    },
                )
            )
            if effect.make_current:
                await conn.execute(
                    update(m.accounts)
                    .where(m.accounts.c.id == effect.card.account_id)
                    .values(current_card_id=effect.card.id)
                )
        elif isinstance(effect, InsertPayee):
            await conn.execute(m.payees.insert().values(**effect.payee.model_dump()))
        elif isinstance(effect, OperationEvent):
            data = effect.operation.model_dump(mode="python", exclude={"commit_seq"})
            data["payload"] = effect.operation.model_dump(mode="json")["payload"]
            await conn.execute(m.operation_events.insert().values(**data))
            await conn.execute(
                m.outbox.insert().values(
                    event_id=effect.operation.event_id,
                    payload=effect.operation.model_dump(mode="json"),
                )
            )
        else:
            raise ValueError("Unsupported effect; P1 never silently ignores future business")
