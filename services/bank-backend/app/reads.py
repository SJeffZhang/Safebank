"""P1 implemented read tools. Future tools fail explicitly, never return placeholders."""

import base64
import json
from datetime import timedelta

from safebank_bankcore.banking import card_view
from safebank_bankcore.ids import digest
from safebank_contracts.codes import Code
from safebank_contracts.views import READ_RESULTS

from .errors import BankError

SUPPORTED_READS = {
    "get_accounts",
    "list_payees",
    "get_transactions",
    "get_cards",
    "get_profile",
    "list_scheduled",
    "list_subscriptions",
}


def mask(value):
    return "****" + value[-4:]


def require_account(state, account_id, customer_id):
    if account_id is not None:
        account = state.accounts.get(account_id)
        if not account or account.customer_id != customer_id:
            raise BankError(Code.NOT_FOUND, 404)


def paginate(items, args, customer_id, key):
    scope = digest(
        {"customer_id": customer_id, "query": args.model_dump(mode="json", exclude={"cursor"})}
    )
    if args.cursor:
        try:
            cursor = json.loads(base64.urlsafe_b64decode(args.cursor.encode()))
            if cursor["scope"] != scope:
                raise ValueError("cursor from another query")
            items = [item for item in items if key(item) > tuple(cursor["after"])]
        except (ValueError, KeyError, TypeError):
            raise BankError(Code.CONTRACT_INVALID, 422) from None
    page = items[: args.limit]
    more = None
    if len(items) > args.limit:
        more = base64.urlsafe_b64encode(
            json.dumps({"scope": scope, "after": key(page[-1])}).encode()
        ).decode()
    return page, more


def read(state, tool, args, customer_id):
    owned = [a for a in state.accounts.values() if a.customer_id == customer_id]
    if hasattr(args, "account_id"):
        require_account(state, args.account_id, customer_id)
    if tool == "get_accounts":
        history = []
        for account in owned:
            transactions = sorted(
                (t for t in state.transactions if t.account_id == account.id),
                key=lambda t: t.sequence,
            )
            for day in range(args.history_days - 1, -1, -1):
                date = state.sim_now.date() - timedelta(days=day)
                last = next(
                    (
                        t
                        for t in reversed(transactions)
                        if t.occurred_at.astimezone(state.sim_now.tzinfo).date() <= date
                    ),
                    None,
                )
                history.append(
                    dict(
                        account_id=account.id,
                        date=date,
                        balance_cents=last.balance_after_cents if last else 0,
                        available_cents=last.available_after_cents if last else 0,
                    )
                )
        outflow = state.counters.daily_outflow_cents.get(customer_id, 0)
        data = dict(
            accounts=[
                dict(
                    id=a.id,
                    type=a.type,
                    display_name=a.display_name,
                    account_no_masked=mask(a.account_no),
                    currency=a.currency,
                    balance_cents=a.balance_cents,
                    pending_debit_cents=a.debits_pending_cents,
                    pending_credit_cents=a.credits_pending_cents,
                    available_cents=a.available_cents,
                    current_card_id=a.current_card_id,
                    single_limit_cents=a.single_limit_cents if a.current_card_id is None else None,
                    daily_limit_cents=a.daily_limit_cents if a.current_card_id is None else None,
                )
                for a in owned
            ],
            daily_outflow_cents=outflow,
            daily_outflow_limit_cents=5_000_000,
            remaining_daily_outflow_cents=max(0, 5_000_000 - outflow),
            balance_history=history,
        )
    elif tool == "list_payees":
        payees = sorted(state.payees.values(), key=lambda p: p.id)
        if args.query:
            payees = [
                p
                for p in payees
                if any(args.query in (value or "") for value in (p.name, p.alias, p.phone))
            ]
        payees, cursor = paginate(payees, args, customer_id, lambda p: (p.id,))
        data = dict(
            items=[
                dict(
                    id=p.id,
                    name=p.name,
                    alias=p.alias,
                    phone_masked=mask(p.phone) if p.phone else None,
                    bank_name=p.bank_name,
                    account_no_masked=mask(p.account_no),
                    target_fingerprint=p.target_fingerprint,
                    is_internal=p.target_account_id is not None,
                    created_at=p.created_at,
                    has_transfer_history=any(
                        t.payee_id == p.id
                        and t.direction == "OUT"
                        and t.phase in ("POSTED", "POST_PENDING")
                        for t in state.transactions
                    ),
                )
                for p in payees
            ],
            next_cursor=cursor,
        )
    elif tool == "get_cards":
        data = dict(
            items=[
                card_view(state, c)
                for c in state.cards.values()
                if state.accounts[c.account_id].customer_id == customer_id
                and (args.account_id is None or c.account_id == args.account_id)
            ]
        )
    elif tool == "get_transactions":
        transactions = [
            t
            for t in state.transactions
            if args.from_at <= t.occurred_at < args.to_at
            and (args.account_id is None or args.account_id == t.account_id)
            and (args.category is None or args.category == t.category)
            and (args.merchant_id is None or args.merchant_id == t.merchant_id)
            and (args.kind is None or args.kind == t.kind)
        ]
        totals = {
            direction: sum(
                t.amount_cents
                for t in transactions
                if t.direction == direction and t.phase in ("POSTED", "POST_PENDING")
            )
            for direction in ("IN", "OUT")
        }
        transactions.sort(key=lambda t: (t.occurred_at.isoformat(), t.id))
        transactions, cursor = paginate(
            transactions, args, customer_id, lambda t: (t.occurred_at.isoformat(), t.id)
        )
        data = dict(
            items=[
                dict(
                    **t.model_dump(
                        exclude={"customer_id", "transfer_id", "sequence", "phase", "channel"}
                    ),
                    merchant_name=None,
                )
                for t in transactions
            ],
            next_cursor=cursor,
            income_cents=totals["IN"],
            expense_cents=totals["OUT"],
            totals_scope="filtered_all",
        )
    elif tool == "get_profile":
        customer = state.customers[customer_id]
        data = dict(
            customer_id=customer.id,
            display_name=customer.display_name,
            phone_masked=mask(customer.phone),
            risk_profile=dict(
                level=customer.risk_level,
                is_valid=(
                    customer.risk_level is not None
                    and customer.risk_valid_until is not None
                    and date.fromisoformat(customer.risk_valid_until) >= state.sim_now.date()
                ),
                valid_until=customer.risk_valid_until,
            ),
            sim_now=state.sim_now,
        )
    elif tool == "list_scheduled":
        items = [
            v for v in state.instructions if args.status == "ALL" or v.status == args.status
        ]
        items, cursor = paginate(items, args, customer_id, lambda v: (v.id,))
        data = dict(
            items=[v.model_dump(mode="json") for v in items],
            next_cursor=cursor,
        )
    elif tool == "list_subscriptions":
        items = [
            v for v in state.subscriptions if args.status == "ALL" or v.status == args.status
        ]
        items, cursor = paginate(items, args, customer_id, lambda v: (v.billing_id,))
        active = [v for v in state.subscriptions if v.status == "ACTIVE"]
        total_annual = sum(v.annual_cost_cents for v in active)
        total_monthly = 0
        for v in active:
            if v.period == "MONTHLY":
                total_monthly += v.amount_cents
            elif v.period == "WEEKLY":
                total_monthly += v.annual_cost_cents // 12
        data = dict(
            items=[v.model_dump(mode="json") for v in items],
            next_cursor=cursor,
            total_monthly_cents=total_monthly,
            total_annual_cents=total_annual,
            normalization_basis=dict(
                weekly_to_annual_factor=52,
                monthly_to_annual_factor=12,
                annual_to_monthly="floor_div_12",
            ),
        )
    else:
        raise BankError(Code.TOOL_NOT_ALLOWED, 403)
    return READ_RESULTS[tool].model_validate(data)
