"""PRD §7.7.1: the only state loader and financial persistence implementation."""

import hashlib
import hmac
from datetime import timedelta
from zoneinfo import ZoneInfo

from safebank_bankcore.ledger import account_deltas
from safebank_bankcore.scope import scope_for
from safebank_contracts.obligations import (
    InstructionRun,
    InstructionTarget,
    Merchant,
    MerchantBilling,
    MerchantMandate,
    ObligationSnapshot,
    ScheduledInstruction,
)
from safebank_contracts.state import (
    Account,
    AppendTransaction,
    BankState,
    Card,
    Counters,
    Customer,
    DirectoryEntry,
    EVENT_KIND_RANK,
    InsertInstruction,
    InsertPayee,
    LedgerTransfer,
    Operation,
    OperationEvent,
    Payee,
    PostTransfer,
    ScheduleEventJob,
    SetInstructionStatus,
    StateScope,
    Transaction,
    UpsertCard,
)
from safebank_contracts.views import RunView, ScheduledView, SubscriptionView
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


async def _load_obligations(conn, ids: tuple[str, ...], customer_id: str):
    """Load the raw obligation snapshot and the requesting customer's views.

    Reads all four obligation tables in the caller's transaction and never opens
    another connection or reads the wall clock. Any missing merchant, billing,
    source account, owner or target reference fails loudly instead of returning a
    partial empty snapshot.
    """
    billings = {
        r["id"]: MerchantBilling(**r)
        for r in (
            await conn.execute(
                select(m.merchant_billing).where(m.merchant_billing.c.customer_id.in_(ids))
            )
        ).mappings()
    }
    instructions = [
        (
            ScheduledInstruction(**{k: r[k] for k in ScheduledInstruction.model_fields})
            if r["kind"] == "SCHEDULED_TRANSFER"
            else MerchantMandate(**{k: r[k] for k in MerchantMandate.model_fields})
        )
        for r in (
            await conn.execute(
                select(m.standing_instructions).where(
                    m.standing_instructions.c.customer_id.in_(ids)
                )
            )
        ).mappings()
    ]
    runs = [
        InstructionRun(**r)
        for r in (
            await conn.execute(
                select(m.instruction_runs)
                .where(m.instruction_runs.c.customer_id.in_(ids))
                .order_by(m.instruction_runs.c.due_at, m.instruction_runs.c.id)
            )
        ).mappings()
    ]
    merchant_ids = {b.merchant_id for b in billings.values()} | {
        i.merchant_id for i in instructions if isinstance(i, MerchantMandate)
    }
    merchants = {
        r["id"]: Merchant(**r)
        for r in (
            await conn.execute(select(m.merchants).where(m.merchants.c.id.in_(merchant_ids)))
        ).mappings()
        if merchant_ids
    }
    by_id = {i.id: i for i in instructions}
    targets: dict[str, InstructionTarget] = {}
    for item in instructions:
        if not isinstance(item, ScheduledInstruction):
            continue
        if item.to_account_id is not None:
            owner = await conn.scalar(
                select(m.accounts.c.customer_id).where(m.accounts.c.id == item.to_account_id)
            )
            if owner != item.customer_id:
                raise ValueError(f"instruction {item.id} own target belongs to another customer")
            targets[item.id] = InstructionTarget(
                instruction_id=item.id,
                source_customer_id=item.customer_id,
                ownership="OWN",
                target_account_id=item.to_account_id,
                target_customer_id=owner,
            )
            continue
        payee = (
            (
                await conn.execute(
                    select(m.payees.c.target_account_id, m.payees.c.customer_id).where(
                        m.payees.c.id == item.payee_id, m.payees.c.customer_id == item.customer_id
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if payee is None:
            raise ValueError(f"instruction {item.id} references an unknown payee")
        if payee["target_account_id"] is None:
            targets[item.id] = InstructionTarget(
                instruction_id=item.id,
                source_customer_id=item.customer_id,
                ownership="EXTERNAL",
                target_account_id=None,
                target_customer_id=None,
            )
            continue
        peer = await conn.scalar(
            select(m.accounts.c.customer_id).where(m.accounts.c.id == payee["target_account_id"])
        )
        if peer is None or peer == item.customer_id:
            raise ValueError(f"instruction {item.id} payee target ownership is invalid")
        targets[item.id] = InstructionTarget(
            instruction_id=item.id,
            source_customer_id=item.customer_id,
            ownership="OTHER",
            target_account_id=payee["target_account_id"],
            target_customer_id=peer,
        )
    snapshot = ObligationSnapshot(
        covered_customer_ids=ids,
        merchants=merchants,
        billings=billings,
        standing_instructions=by_id,
        runs=tuple(runs),
        instruction_targets=targets,
    )
    instructions, subscriptions = _derive_views(snapshot, customer_id)
    return snapshot, instructions, subscriptions


def _last_run(snapshot: ObligationSnapshot, instruction_id: str) -> RunView | None:
    candidates = [r for r in snapshot.runs if r.instruction_id == instruction_id]
    if not candidates:
        return None
    latest = max(candidates, key=lambda r: (r.due_at, r.id))
    return RunView(status=latest.status, code=latest.code, due_at=latest.due_at)


def _derive_views(snapshot: ObligationSnapshot, customer_id: str):
    instructions = []
    subscriptions = []
    for item in snapshot.standing_instructions.values():
        if item.customer_id != customer_id:
            continue
        if isinstance(item, ScheduledInstruction):
            target = snapshot.instruction_targets.get(item.id)
            if target is not None and target.ownership == "OWN":
                display = "本人账户"
            elif target is not None and target.ownership == "EXTERNAL":
                display = "他行收款人"
            else:
                display = "本行收款人"
            instructions.append(
                ScheduledView(
                    id=item.id,
                    from_account_id=item.from_account_id,
                    payee_id=item.payee_id,
                    to_account_id=item.to_account_id,
                    target_display=display,
                    amount_cents=item.amount_cents,
                    period=item.period,
                    anchor_day=item.anchor_day,
                    weekday=item.weekday,
                    time_of_day=item.time_of_day.isoformat(),
                    next_run_at=item.next_run_at,
                    end_at=item.end_at,
                    status=item.status,
                    last_run=_last_run(snapshot, item.id),
                )
            )
            continue
        billing = snapshot.billings[item.billing_id]
        merchant = snapshot.merchants[item.merchant_id]
        annual = billing.amount_cents * (12 if billing.period == "MONTHLY" else 52)
        subscriptions.append(
            SubscriptionView(
                instruction_id=item.id,
                billing_id=item.billing_id,
                account_id=billing.account_id,
                merchant_id=item.merchant_id,
                merchant_name=merchant.name,
                period=billing.period,
                amount_cents=billing.amount_cents,
                max_amount_cents=item.max_amount_cents,
                next_due_at=billing.next_due_at,
                status=item.status,
                annual_cost_cents=annual,
            )
        )
    instructions.sort(key=lambda v: v.id)
    subscriptions.sort(key=lambda v: v.billing_id)
    return tuple(instructions), tuple(subscriptions)


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
    obligations, instructions, subscriptions = await _load_obligations(conn, ids, customer_id)
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
        obligations=obligations,
        instructions=instructions,
        subscriptions=subscriptions,
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
        elif isinstance(effect, InsertInstruction):
            inst_data = effect.instruction.model_dump(mode="python")
            await conn.execute(m.standing_instructions.insert().values(**inst_data))
        elif isinstance(effect, SetInstructionStatus):
            await conn.execute(
                update(m.standing_instructions)
                .where(m.standing_instructions.c.id == effect.instruction_id)
                .values(status=effect.new_status)
            )
        elif isinstance(effect, ScheduleEventJob):
            rank = EVENT_KIND_RANK.get(effect.kind)
            if rank is None:
                raise ValueError(f"unknown event job kind: {effect.kind}")
            await conn.execute(
                m.event_jobs.insert().values(
                    id=effect.job_id,
                    kind=effect.kind,
                    rank=rank,
                    due_at=effect.due_at,
                    immutable_payload={"ciphertext": effect.payload_ciphertext},
                    payload_hash=effect.payload_hash,
                    source_id=effect.source_id,
                    period_key=effect.period_key,
                    status="PENDING",
                    attempt_count=0,
                )
            )
        else:
            raise ValueError("Unsupported effect; P1 never silently ignores future business")
