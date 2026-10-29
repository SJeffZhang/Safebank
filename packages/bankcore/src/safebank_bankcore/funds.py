"""Wealth-management fund domain (PRD §8.2) — pure bankcore functions.

No IO, no database, no randomness.  ``apply_subscribe`` / ``apply_redeem`` are
invoked through :func:`banking.apply_plan`; ``apply_fund_confirm``,
``apply_fund_settle`` and ``apply_fund_expiry`` are order-driven system events
that operate only on stored order data (re-checking suitability before confirm,
releasing the freeze on confirm failure or expiry).

Money-market funds (MMF) settle immediately; other products freeze the
subscription as a PENDING transfer until the confirm event posts it, and hold
redemptions as PENDING_SETTLEMENT until the settle event pays them out.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from safebank_contracts.codes import Code
from safebank_contracts.enums import Phase
from safebank_contracts.plan import Violation
from safebank_contracts.state import (
    FUTURE_EFFECTS,
    BankState,
    FundOrderRecord,
    LedgerTransfer,
    Operation,
    OperationEvent,
)
from safebank_contracts.views import (
    READ_RESULTS,
    FundOrderView,
    HoldingView,
    ProductView,
)

from .calendar import next_trading_day, trade_date
from .ids import derive
from .ledger import post_transfer, replace_state
from .nav import nav
from .reserve import BUSINESS_TIMEZONE, _business_time

if TYPE_CHECKING:
    from .banking import Context

InsertFundOrder = FUTURE_EFFECTS["InsertFundOrder"]
UpdateFundOrder = FUTURE_EFFECTS["UpdateFundOrder"]


def _reject(state, ctx, code):
    return state, (), (Violation(code=code, step_id=ctx.step_id, message=code.value),)


def _op(state, ctx, now, kind, *, target=None, amount=0):
    return OperationEvent(
        operation=Operation(
            event_id=derive(ctx.plan_id, ctx.step_id, "evt"),
            customer_id=ctx.customer_id,
            plan_id=ctx.plan_id,
            step_id=ctx.step_id,
            action_kind=kind,
            target_fingerprint=target,
            amount_cents=amount,
            occurred_at=now,
        )
    )


def _floor4(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"), rounding=ROUND_DOWN)


def _floor_cents(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_DOWN))


def _round_cents(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _plus_trading_days(d: date, n: int) -> date:
    cur = d
    for _ in range(n):
        cur = next_trading_day(cur + timedelta(days=1))
    return cur


def _at_09(d: date) -> datetime:
    return datetime.combine(d, time(9, 0), tzinfo=BUSINESS_TIMEZONE)


def _settlement_account_id(product_id: str) -> str:
    return f"acc_fund_{product_id}"


def _product(state: BankState, product_id: str) -> ProductView | None:
    return next((p for p in state.products if p.id == product_id), None)


def _find_order(state: BankState, order_id: str) -> FundOrderRecord | None:
    return next((o for o in state.fund_orders if o.id == order_id), None)


def _put_order(state: BankState, order: FundOrderRecord) -> BankState:
    if any(o.id == order.id for o in state.fund_orders):
        return replace_state(
            state, fund_orders=tuple(o if o.id != order.id else order for o in state.fund_orders)
        )
    return replace_state(state, fund_orders=(*state.fund_orders, order))


def _assessed_level(state: BankState, customer_id: str, now: datetime) -> int | None:
    customer = state.customers.get(customer_id)
    if customer is None or customer.risk_level is None or not customer.risk_valid_until:
        return None
    try:
        valid_until = date.fromisoformat(customer.risk_valid_until)
    except ValueError:
        return None
    if valid_until < _business_time(now).date():
        return None
    return customer.risk_level


def _holding_index(state: BankState) -> dict[tuple[str, str], HoldingView]:
    return {(h.account_id, h.product_id): h for h in state.holdings}


def _rebuild_holdings(state: BankState, now: datetime) -> BankState:
    products = {p.id: p for p in state.products}
    receivables: dict[tuple[str, str], int] = {}
    for o in state.fund_orders:
        if o.type == "RED" and o.status == "PENDING_SETTLEMENT":
            key = (o.account_id, o.product_id)
            receivables[key] = receivables.get(key, 0) + o.amount_cents
    rebuilt = []
    for h in state.holdings:
        product = products.get(h.product_id)
        if product is None:
            rebuilt.append(h)
            continue
        nav_value = nav(product.nav, product.nav_date, product.product_type, trade_date(now))
        market_value = _floor_cents(h.units * nav_value * Decimal(100))
        rebuilt.append(
            h.model_copy(
                update={
                    "available_units": (h.units - h.reserved_units).quantize(
                        Decimal("0.0001"), rounding=ROUND_DOWN
                    ),
                    "available_cost_cents": h.cost_cents - h.reserved_cost_cents,
                    "nav": nav_value,
                    "market_value_cents": market_value,
                    "unrealized_change_cents": market_value - h.cost_cents,
                    "reserved_receivable_cents": receivables.get((h.account_id, h.product_id), 0),
                }
            )
        )
    return replace_state(state, holdings=tuple(rebuilt))


def _apply_holding(
    state: BankState,
    account_id: str,
    product_id: str,
    *,
    units_delta: Decimal = Decimal("0"),
    reserved_units_delta: Decimal = Decimal("0"),
    cost_delta: int = 0,
    reserved_cost_delta: int = 0,
    now: datetime,
) -> BankState:
    index = _holding_index(state)
    holding = index.get((account_id, product_id))
    if holding is None:
        holding = HoldingView(
            account_id=account_id,
            product_id=product_id,
            units=Decimal("0"),
            reserved_units=Decimal("0"),
            available_units=Decimal("0"),
            cost_cents=0,
            reserved_cost_cents=0,
            available_cost_cents=0,
            nav=Decimal("0"),
            market_value_cents=0,
            unrealized_change_cents=0,
            reserved_receivable_cents=0,
        )
    raw = holding.model_copy(
        update={
            "units": (holding.units + units_delta).quantize(Decimal("0.0001"), rounding=ROUND_DOWN),
            "reserved_units": (holding.reserved_units + reserved_units_delta).quantize(
                Decimal("0.0001"), rounding=ROUND_DOWN
            ),
            "cost_cents": holding.cost_cents + cost_delta,
            "reserved_cost_cents": holding.reserved_cost_cents + reserved_cost_delta,
        }
    )
    index[(account_id, product_id)] = raw
    return _rebuild_holdings(replace_state(state, holdings=tuple(index.values())), now)


def apply_subscribe(state: BankState, args, now: datetime, ctx: Context):
    account = state.accounts.get(args.account_id)
    if account is None or account.customer_id != ctx.customer_id:
        return _reject(state, ctx, Code.NOT_FOUND)
    product = _product(state, args.product_id)
    if product is None:
        return _reject(state, ctx, Code.NOT_FOUND)
    settlement = state.accounts.get(_settlement_account_id(product.id))
    if settlement is None:
        return _reject(state, ctx, Code.DEPENDENCY_UNMET)
    level = _assessed_level(state, ctx.customer_id, now)
    if level is None:
        return _reject(state, ctx, Code.ASSESSMENT_MISSING)
    if level < product.risk_level:
        return _reject(state, ctx, Code.SUITABILITY_MISMATCH)
    if args.amount_cents < product.min_amount_cents:
        return _reject(state, ctx, Code.MIN_PURCHASE_NOT_MET)
    t_date = trade_date(now)
    order_id = derive(ctx.plan_id, ctx.step_id, "ord")
    transfer_id = derive(ctx.plan_id, ctx.step_id, "trf")
    if product.product_type == "MMF":
        nav_value = nav(product.nav, product.nav_date, product.product_type, t_date)
        units = _floor4(Decimal(args.amount_cents) / Decimal(100) / nav_value)
        cmd = LedgerTransfer(
            id=transfer_id,
            debit_account_id=account.id,
            credit_account_id=settlement.id,
            amount_cents=args.amount_cents,
            code="FUND_SUBSCRIBE",
            phase=Phase.POSTED,
            pending_id=None,
            plan_id=ctx.plan_id,
            step_id=ctx.step_id,
            occurred_at=now,
            expires_at=None,
        )
        after, effects, errors = post_transfer(state, cmd, now)
        if errors:
            return after, effects, errors
        order = FundOrderRecord(
            id=order_id,
            customer_id=ctx.customer_id,
            account_id=account.id,
            product_id=product.id,
            type="SUB",
            status="SETTLED",
            amount_cents=args.amount_cents,
            units=units,
            nav=nav_value,
            reserved_cost_cents=0,
            pending_transfer_id=None,
            plan_id=ctx.plan_id,
            step_id=ctx.step_id,
            trade_date=t_date,
            confirm_at=now,
            settle_at=now,
            expires_at=None,
            failure_code=None,
        )
        after = _put_order(after, order)
        after = _apply_holding(
            after, account.id, product.id, units_delta=units, cost_delta=args.amount_cents, now=now
        )
        return (
            after,
            (
                *effects,
                InsertFundOrder(order=order),
                _op(state, ctx, now, "FUND_SUBSCRIBE", target=product.id, amount=args.amount_cents),
            ),
            (),
        )
    confirm_date = _plus_trading_days(t_date, product.confirm_days)
    confirm_at = _at_09(confirm_date)
    expires_at = _at_09(_plus_trading_days(confirm_date, 2))
    cmd = LedgerTransfer(
        id=transfer_id,
        debit_account_id=account.id,
        credit_account_id=settlement.id,
        amount_cents=args.amount_cents,
        code="FUND_SUBSCRIBE",
        phase=Phase.PENDING,
        pending_id=None,
        plan_id=ctx.plan_id,
        step_id=ctx.step_id,
        occurred_at=now,
        expires_at=expires_at,
    )
    after, effects, errors = post_transfer(state, cmd, now)
    if errors:
        return after, effects, errors
    order = FundOrderRecord(
        id=order_id,
        customer_id=ctx.customer_id,
        account_id=account.id,
        product_id=product.id,
        type="SUB",
        status="PENDING_CONFIRM",
        amount_cents=args.amount_cents,
        units=Decimal("0.0000"),
        nav=None,
        reserved_cost_cents=0,
        pending_transfer_id=transfer_id,
        plan_id=ctx.plan_id,
        step_id=ctx.step_id,
        trade_date=t_date,
        confirm_at=confirm_at,
        settle_at=None,
        expires_at=expires_at,
        failure_code=None,
    )
    after = _put_order(after, order)
    return (
        after,
        (
            *effects,
            InsertFundOrder(order=order),
            _op(state, ctx, now, "FUND_SUBSCRIBE", target=product.id, amount=args.amount_cents),
        ),
        (),
    )


def apply_redeem(state: BankState, args, now: datetime, ctx: Context):
    account = state.accounts.get(args.account_id)
    if account is None or account.customer_id != ctx.customer_id:
        return _reject(state, ctx, Code.NOT_FOUND)
    product = _product(state, args.product_id)
    if product is None:
        return _reject(state, ctx, Code.NOT_FOUND)
    settlement = state.accounts.get(_settlement_account_id(product.id))
    if settlement is None:
        return _reject(state, ctx, Code.DEPENDENCY_UNMET)
    units = args.units
    if units <= 0:
        return _reject(state, ctx, Code.CONTRACT_INVALID)
    holding = _holding_index(state).get((account.id, product.id))
    if holding is None or holding.available_units < units:
        return _reject(state, ctx, Code.HOLDING_INSUFFICIENT)
    t_date = trade_date(now)
    nav_value = nav(product.nav, product.nav_date, product.product_type, t_date)
    amount_cents = _floor_cents(units * nav_value * Decimal(100))
    if units == holding.available_units:
        reserved_cost = holding.cost_cents - holding.reserved_cost_cents
    else:
        reserved_cost = _round_cents(
            Decimal(holding.cost_cents - holding.reserved_cost_cents)
            * units
            / holding.available_units
        )
    order_id = derive(ctx.plan_id, ctx.step_id, "ord")
    if product.product_type == "MMF":
        cmd = LedgerTransfer(
            id=derive(ctx.plan_id, ctx.step_id, "trf"),
            debit_account_id=settlement.id,
            credit_account_id=account.id,
            amount_cents=amount_cents,
            code="FUND_REDEEM",
            phase=Phase.POSTED,
            pending_id=None,
            plan_id=ctx.plan_id,
            step_id=ctx.step_id,
            occurred_at=now,
            expires_at=None,
        )
        after, effects, errors = post_transfer(state, cmd, now)
        if errors:
            return after, effects, errors
        order = FundOrderRecord(
            id=order_id,
            customer_id=ctx.customer_id,
            account_id=account.id,
            product_id=product.id,
            type="RED",
            status="SETTLED",
            amount_cents=amount_cents,
            units=units,
            nav=nav_value,
            reserved_cost_cents=reserved_cost,
            pending_transfer_id=None,
            plan_id=ctx.plan_id,
            step_id=ctx.step_id,
            trade_date=t_date,
            confirm_at=None,
            settle_at=now,
            expires_at=None,
            failure_code=None,
        )
        after = _put_order(after, order)
        after = _apply_holding(
            after, account.id, product.id, units_delta=-units, cost_delta=-reserved_cost, now=now
        )
        return (
            after,
            (
                *effects,
                InsertFundOrder(order=order),
                _op(state, ctx, now, "FUND_REDEEM", target=product.id, amount=amount_cents),
            ),
            (),
        )
    settle_at = _at_09(_plus_trading_days(t_date, product.settle_days))
    order = FundOrderRecord(
        id=order_id,
        customer_id=ctx.customer_id,
        account_id=account.id,
        product_id=product.id,
        type="RED",
        status="PENDING_SETTLEMENT",
        amount_cents=amount_cents,
        units=units,
        nav=nav_value,
        reserved_cost_cents=reserved_cost,
        pending_transfer_id=None,
        plan_id=ctx.plan_id,
        step_id=ctx.step_id,
        trade_date=t_date,
        confirm_at=None,
        settle_at=settle_at,
        expires_at=None,
        failure_code=None,
    )
    after = _put_order(state, order)
    after = _apply_holding(
        after,
        account.id,
        product.id,
        reserved_units_delta=units,
        reserved_cost_delta=reserved_cost,
        now=now,
    )
    return (
        after,
        (
            InsertFundOrder(order=order),
            _op(state, ctx, now, "FUND_REDEEM", target=product.id, amount=amount_cents),
        ),
        (),
    )


def _resolve_pending(
    state: BankState,
    order: FundOrderRecord,
    settlement_id: str,
    at: datetime,
    phase: Phase,
    leg: str,
):
    cmd = LedgerTransfer(
        id=derive(order.plan_id, order.step_id, "trf", leg),
        debit_account_id=order.account_id,
        credit_account_id=settlement_id,
        amount_cents=order.amount_cents,
        code="FUND_SUBSCRIBE",
        phase=phase,
        pending_id=order.pending_transfer_id,
        plan_id=order.plan_id,
        step_id=order.step_id,
        occurred_at=at,
        expires_at=None,
    )
    return post_transfer(state, cmd, at)


def apply_fund_confirm(state: BankState, order_id: str, at: datetime, ctx: Context):
    order = _find_order(state, order_id)
    if order is None or order.type != "SUB":
        return _reject(state, ctx, Code.NOT_FOUND)
    if order.status != "PENDING_CONFIRM":
        return state, (), ()
    product = _product(state, order.product_id)
    settlement = state.accounts.get(_settlement_account_id(order.product_id))
    if product is None or settlement is None:
        return _reject(state, ctx, Code.DEPENDENCY_UNMET)
    level = _assessed_level(state, ctx.customer_id, at)
    if level is None:
        return _fail_confirm(state, order, settlement.id, at, Code.ASSESSMENT_MISSING)
    if level < product.risk_level:
        return _fail_confirm(state, order, settlement.id, at, Code.SUITABILITY_MISMATCH)
    nav_value = nav(product.nav, product.nav_date, product.product_type, _business_time(at).date())
    units = _floor4(Decimal(order.amount_cents) / Decimal(100) / nav_value)
    after, effects, errors = _resolve_pending(
        state, order, settlement.id, at, Phase.POST_PENDING, "confirm"
    )
    if errors:
        return after, effects, errors
    updated = order.model_copy(
        update={
            "status": "SETTLED",
            "units": units,
            "nav": nav_value,
            "settle_at": at,
            "expires_at": None,
        }
    )
    after = _put_order(after, updated)
    after = _apply_holding(
        after,
        order.account_id,
        order.product_id,
        units_delta=units,
        cost_delta=order.amount_cents,
        now=at,
    )
    return after, (*effects, UpdateFundOrder(order=updated)), ()


def _fail_confirm(
    state: BankState, order: FundOrderRecord, settlement_id: str, at: datetime, code: Code
):
    after, effects, errors = _resolve_pending(
        state, order, settlement_id, at, Phase.VOID_PENDING, "void"
    )
    if errors:
        return after, effects, errors
    updated = order.model_copy(
        update={"status": "FAILED", "failure_code": code.value, "expires_at": None}
    )
    after = _put_order(after, updated)
    return after, (*effects, UpdateFundOrder(order=updated)), ()


def apply_fund_expiry(state: BankState, order_id: str, at: datetime, ctx: Context):
    order = _find_order(state, order_id)
    if order is None or order.type != "SUB":
        return _reject(state, ctx, Code.NOT_FOUND)
    if order.status != "PENDING_CONFIRM":
        return state, (), ()
    settlement = state.accounts.get(_settlement_account_id(order.product_id))
    if settlement is None:
        return _reject(state, ctx, Code.DEPENDENCY_UNMET)
    after, effects, errors = _resolve_pending(
        state, order, settlement.id, at, Phase.VOID_PENDING, "void"
    )
    if errors:
        return after, effects, errors
    updated = order.model_copy(update={"status": "EXPIRED", "expires_at": None})
    after = _put_order(after, updated)
    return after, (*effects, UpdateFundOrder(order=updated)), ()


def apply_fund_settle(state: BankState, order_id: str, at: datetime, ctx: Context):
    order = _find_order(state, order_id)
    if order is None or order.type != "RED":
        return _reject(state, ctx, Code.NOT_FOUND)
    if order.status != "PENDING_SETTLEMENT":
        return state, (), ()
    settlement = state.accounts.get(_settlement_account_id(order.product_id))
    if settlement is None:
        return _reject(state, ctx, Code.DEPENDENCY_UNMET)
    cmd = LedgerTransfer(
        id=derive(order.plan_id, order.step_id, "trf", "redeem"),
        debit_account_id=settlement.id,
        credit_account_id=order.account_id,
        amount_cents=order.amount_cents,
        code="FUND_REDEEM",
        phase=Phase.POSTED,
        pending_id=None,
        plan_id=order.plan_id,
        step_id=order.step_id,
        occurred_at=at,
        expires_at=None,
    )
    after, effects, errors = post_transfer(state, cmd, at)
    if errors:
        return after, effects, errors
    updated = order.model_copy(update={"status": "SETTLED", "settle_at": at})
    after = _put_order(after, updated)
    after = _apply_holding(
        after,
        order.account_id,
        order.product_id,
        units_delta=-order.units,
        reserved_units_delta=-order.units,
        cost_delta=-order.reserved_cost_cents,
        reserved_cost_delta=-order.reserved_cost_cents,
        now=at,
    )
    return after, (*effects, UpdateFundOrder(order=updated)), ()


def order_view(order: FundOrderRecord) -> FundOrderView:
    return FundOrderView(
        id=order.id,
        account_id=order.account_id,
        product_id=order.product_id,
        type=order.type,
        status=order.status,
        amount_cents=order.amount_cents,
        units=order.units,
        nav=order.nav,
        trade_date=order.trade_date,
        confirm_at=order.confirm_at,
        settle_at=order.settle_at,
        failure_code=order.failure_code,
    )


def holdings_view(state: BankState, customer_id: str, account_id=None, product_id=None):
    holdings = tuple(
        h
        for h in state.holdings
        if state.accounts[h.account_id].customer_id == customer_id
        and (account_id is None or h.account_id == account_id)
        and (product_id is None or h.product_id == product_id)
    )
    orders = tuple(
        order_view(o)
        for o in state.fund_orders
        if o.customer_id == customer_id
        and (account_id is None or o.account_id == account_id)
        and (product_id is None or o.product_id == product_id)
    )
    return READ_RESULTS["get_holdings"](holdings=holdings, orders=orders)
