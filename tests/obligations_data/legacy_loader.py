"""Frozen pre-S3a ``load_bank_state`` for the 0002-upgrade history gate.

S3a's production ``load_bank_state`` reads the four 0003 obligation tables, which
do not exist yet on a database migrated only to ``0002_identity``. The 0002
history gate needs to run the *unmodified* P1 ``ExecutionService`` against a 0002
database to produce real COMMITTED/PREPARED/CANCELLED rows, so this module keeps a
verbatim copy of the pre-S3a loader (which reads only the P1 tables) and the test
patches ``app.execution.load_bank_state`` to it only while seeding the 0002 state.

Source: ``cca074b:services/bank-backend/app/state.py`` (git blob
``e998108e0026dc113b57b334c87a3bea97845e36``). Only ``load_bank_state`` is frozen
here; ``resolve_scope``/``persist`` are unchanged in S3a and still come from the
production module. This is test-only history compatibility: it never replaces the
production loader and never makes a missing 0003 table look like an empty snapshot.
"""

from datetime import timedelta
from zoneinfo import ZoneInfo

from app import models as m
from safebank_contracts.state import (
    Account,
    BankState,
    Card,
    Counters,
    Customer,
    LedgerTransfer,
    Operation,
    Payee,
    StateScope,
    Transaction,
)
from sqlalchemy import or_, select


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
