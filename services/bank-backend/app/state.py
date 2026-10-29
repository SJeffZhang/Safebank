"""PRD §7.7.1: the only state loader and financial persistence implementation."""

import hashlib
import hmac
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from safebank_bankcore.funds import _rebuild_holdings
from safebank_bankcore.ledger import account_deltas
from safebank_bankcore.products import ProductDef, get_product
from safebank_bankcore.reserve import BUSINESS_TIMEZONE
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
    EVENT_KIND_RANK,
    FUTURE_EFFECTS,
    Account,
    AppendTransaction,
    BankState,
    Card,
    Counters,
    Customer,
    DirectoryEntry,
    FundOrderRecord,
    InsertAssessment,
    InsertInstruction,
    InsertPayee,
    LedgerTransfer,
    Operation,
    OperationEvent,
    Payee,
    PostTransfer,
    RiskAssessmentRecord,
    ScheduleEventJob,
    SetInstructionStatus,
    StateScope,
    Transaction,
    UpsertCard,
)
from safebank_contracts.views import (
    HoldingView,
    NavPoint,
    ProductView,
    RunView,
    ScheduledView,
    SplitItem,
    SplitView,
    SubscriptionView,
)
from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert

from . import models as m

InsertSplit = FUTURE_EFFECTS["InsertSplit"]
InsertFundOrder = FUTURE_EFFECTS["InsertFundOrder"]
UpdateFundOrder = FUTURE_EFFECTS["UpdateFundOrder"]
UpdateHolding = FUTURE_EFFECTS["UpdateHolding"]


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
    assessments = [
        RiskAssessmentRecord(
            id=r["id"],
            customer_id=r["customer_id"],
            questionnaire_id=r["questionnaire_id"],
            answers=tuple(tuple(a) for a in r["answers"]),
            score=r["score"],
            level=r["level"],
            assessed_at=r["assessed_at"],
            valid_until=r["valid_until"].date(),
            plan_id=r["plan_id"],
        )
        for r in (
            await conn.execute(
                select(m.risk_assessments)
                .where(m.risk_assessments.c.customer_id == customer_id)
                .order_by(m.risk_assessments.c.assessed_at.desc())
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
    # Load split requests and items for the requesting customer only.
    _splits: list[SplitView] = []
    split_request_rows = (
        (
            await conn.execute(
                select(m.split_requests).where(m.split_requests.c.customer_id == customer_id)
            )
        )
        .mappings()
        .all()
    )
    for sr in split_request_rows:
        items = (
            (
                await conn.execute(
                    select(m.split_items).where(m.split_items.c.request_id == sr["id"])
                )
            )
            .mappings()
            .all()
        )
        _splits.append(
            SplitView(
                id=sr["id"],
                account_id=sr["account_id"],
                total_cents=sr["total_cents"],
                include_self=sr["include_self"],
                self_share_cents=sr["self_share_cents"],
                created_at=sr["created_at"],
                received_cents=sum(it["amount_cents"] for it in items if it["status"] == "PAID"),
                outstanding_cents=sr["total_cents"]
                - sr["self_share_cents"]
                - sum(it["amount_cents"] for it in items if it["status"] == "PAID"),
                items=tuple(
                    SplitItem(
                        id=it["id"],
                        payee_id=it["payee_id"],
                        payee_name=it["payee_id"],  # resolved to real name below
                        amount_cents=it["amount_cents"],
                        status=it["status"],
                        paid_at=it["paid_at"],
                    )
                    for it in items
                ),
            )
        )
    # Resolve payee names from the already-loaded payees.
    payee_names = {p.id: p.name for p in payees}
    _splits = [
        sv.model_copy(
            update={
                "items": tuple(
                    si.model_copy(
                        update={"payee_name": payee_names.get(si.payee_id, si.payee_name)}
                    )
                    for si in sv.items
                )
            }
        )
        for sv in _splits
    ]
    # Global product catalog (customer-agnostic). Project rows through bankcore
    # at the snapshot time; order by id for stable pagination.
    product_rows = (
        (await conn.execute(select(m.products).order_by(m.products.c.id))).mappings().all()
    )
    product_defs = [
        ProductDef(
            id=r["id"],
            name=r["name"],
            product_type=r["product_type"],
            risk_level=r["risk_level"],
            risk_label=r["risk_label"],
            base_nav=r["base_nav"],
            base_date=r["base_date"].date(),
            confirm_days=r["confirm_days"],
            settle_days=r["settle_days"],
            min_amount_cents=r["min_amount_cents"],
            risk_disclosure=r["risk_disclosure"],
            disclosure_version=r["disclosure_version"],
        )
        for r in product_rows
    ]
    _products: list[ProductView] = []
    for p in product_defs:
        view = get_product(tuple(product_defs), p.id, now.date())
        _products.append(
            ProductView(
                id=view["id"],
                name=view["name"],
                product_type=view["product_type"],
                risk_level=view["risk_level"],
                risk_label=view["risk_label"],
                nav=view["nav"],
                nav_date=view["nav_date"],
                recent_return_bp=view["recent_return_bp"],
                confirm_days=view["confirm_days"],
                settle_days=view["settle_days"],
                min_amount_cents=view["min_amount_cents"],
                risk_disclosure=view["risk_disclosure"],
                disclosure_version=view["disclosure_version"],
                nav_history=tuple(
                    NavPoint(date=pt["date"], nav=Decimal(pt["nav"])) for pt in view["nav_history"]
                ),
            )
        )
    # Load fund holdings and orders for the requesting customer.
    _holdings = tuple(
        HoldingView(
            account_id=r["account_id"],
            product_id=r["product_id"],
            units=Decimal(r["units"]),
            reserved_units=Decimal(r["reserved_units"]),
            available_units=Decimal("0"),
            cost_cents=r["cost_cents"],
            reserved_cost_cents=r["reserved_cost_cents"],
            available_cost_cents=0,
            nav=Decimal("0"),
            market_value_cents=0,
            unrealized_change_cents=0,
            reserved_receivable_cents=0,
        )
        for r in (
            await conn.execute(select(m.holdings).where(m.holdings.c.customer_id == customer_id))
        ).mappings()
    )
    _fund_orders = tuple(
        FundOrderRecord(
            id=r["id"],
            customer_id=r["customer_id"],
            account_id=r["account_id"],
            product_id=r["product_id"],
            type=r["type"],
            status=r["status"],
            amount_cents=r["amount_cents"],
            units=Decimal(r["units"]),
            nav=Decimal(r["nav"]) if r["nav"] is not None else None,
            reserved_cost_cents=r["reserved_cost_cents"],
            pending_transfer_id=r["pending_transfer_id"],
            plan_id=r["plan_id"],
            step_id=r["step_id"],
            trade_date=r["trade_date"].date(),
            confirm_at=r["confirm_at"],
            settle_at=r["settle_at"],
            expires_at=r["expires_at"],
            failure_code=r["failure_code"],
        )
        for r in (
            await conn.execute(
                select(m.fund_orders).where(m.fund_orders.c.customer_id == customer_id)
            )
        ).mappings()
    )
    raw = BankState(
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
        splits=tuple(_splits),
        products=tuple(_products),
        dataset_revision=clock["dataset_revision"],
        assessments=tuple(assessments),
        holdings=_holdings,
        fund_orders=_fund_orders,
    )
    return _rebuild_holdings(raw, now)


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
                    immutable_payload={"payload": effect.payload_ciphertext},
                    payload_hash=effect.payload_hash,
                    source_id=effect.source_id,
                    period_key=effect.period_key,
                    status="PENDING",
                    attempt_count=0,
                )
            )
        elif isinstance(effect, InsertAssessment):
            record = effect.record
            valid_until = datetime.combine(
                record.valid_until,
                record.assessed_at.time(),
                tzinfo=record.assessed_at.tzinfo,
            )
            await conn.execute(
                m.risk_assessments.insert().values(
                    id=record.id,
                    customer_id=record.customer_id,
                    questionnaire_id=record.questionnaire_id,
                    answers=[[q, o] for q, o in record.answers],
                    score=record.score,
                    level=record.level,
                    assessed_at=record.assessed_at,
                    valid_until=valid_until,
                    plan_id=record.plan_id,
                )
            )
            await conn.execute(
                update(m.customers)
                .where(m.customers.c.id == record.customer_id)
                .values(
                    risk_level=int(record.level[1:]),
                    risk_assessed_at=record.assessed_at,
                    risk_valid_until=record.valid_until.isoformat(),
                )
            )
        elif isinstance(effect, InsertSplit):
            split = effect.split
            customer_id_row = await conn.scalar(
                select(m.accounts.c.customer_id).where(m.accounts.c.id == split.account_id)
            )
            if customer_id_row is None:
                raise ValueError(f"account {split.account_id} not found for split insert")
            await conn.execute(
                m.split_requests.insert().values(
                    id=split.id,
                    customer_id=customer_id_row,
                    account_id=split.account_id,
                    total_cents=split.total_cents,
                    include_self=split.include_self,
                    self_share_cents=split.self_share_cents,
                    created_at=split.created_at,
                )
            )
            for item in split.items:
                await conn.execute(
                    m.split_items.insert().values(
                        id=item.id,
                        request_id=split.id,
                        customer_id=customer_id_row,
                        account_id=split.account_id,
                        payee_id=item.payee_id,
                        amount_cents=item.amount_cents,
                        status=item.status,
                        paid_at=item.paid_at,
                        payment_event_id=None,
                    )
                )
        elif isinstance(effect, InsertFundOrder):
            order = effect.order
            await conn.execute(
                m.fund_orders.insert().values(
                    id=order.id,
                    customer_id=order.customer_id,
                    account_id=order.account_id,
                    product_id=order.product_id,
                    type=order.type,
                    status=order.status,
                    amount_cents=order.amount_cents,
                    units=order.units,
                    nav=order.nav,
                    reserved_cost_cents=order.reserved_cost_cents,
                    pending_transfer_id=order.pending_transfer_id,
                    plan_id=order.plan_id,
                    step_id=order.step_id,
                    trade_date=datetime.combine(
                        order.trade_date, time(0, 0), tzinfo=BUSINESS_TIMEZONE
                    ),
                    confirm_at=order.confirm_at,
                    settle_at=order.settle_at,
                    expires_at=order.expires_at,
                    failure_code=order.failure_code,
                )
            )
        elif isinstance(effect, UpdateFundOrder):
            order = effect.order
            await conn.execute(
                update(m.fund_orders)
                .where(m.fund_orders.c.id == order.id)
                .values(
                    status=order.status,
                    units=order.units,
                    nav=order.nav,
                    reserved_cost_cents=order.reserved_cost_cents,
                    pending_transfer_id=order.pending_transfer_id,
                    confirm_at=order.confirm_at,
                    settle_at=order.settle_at,
                    expires_at=order.expires_at,
                    failure_code=order.failure_code,
                )
            )
        elif isinstance(effect, UpdateHolding):
            h = effect.holding
            customer_id_row = await conn.scalar(
                select(m.accounts.c.customer_id).where(m.accounts.c.id == h.account_id)
            )
            if customer_id_row is None:
                raise ValueError(f"account {h.account_id} not found for holding update")
            data = dict(
                customer_id=customer_id_row,
                account_id=h.account_id,
                product_id=h.product_id,
                units=h.units,
                reserved_units=h.reserved_units,
                cost_cents=h.cost_cents,
                reserved_cost_cents=h.reserved_cost_cents,
            )
            statement = insert(m.holdings).values(**data)
            await conn.execute(
                statement.on_conflict_do_update(
                    index_elements=["customer_id", "account_id", "product_id"],
                    set_={
                        k: statement.excluded[k]
                        for k in (
                            "units",
                            "reserved_units",
                            "cost_cents",
                            "reserved_cost_cents",
                        )
                    },
                )
            )
        else:
            raise ValueError("Unsupported effect; P1 never silently ignores future business")
