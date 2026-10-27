"""Public read/write results; no internal counterpart account IDs."""

from datetime import date
from typing import Literal

from pydantic import Field, StrictBool, StrictInt, create_model

from .base import Cents, Contract, Decimal4, Int64, SignedCents, Timestamp
from .enums import AccountType, CardStatus
from .tools import Id, Period, ProductType


class ChannelFlags(Contract):
    online: StrictBool
    overseas: StrictBool
    atm: StrictBool
    quickpass: StrictBool


class AccountView(Contract):
    id: Id
    type: AccountType
    display_name: str
    account_no_masked: str
    currency: Literal["CNY"]
    balance_cents: Cents
    pending_debit_cents: Cents
    pending_credit_cents: Cents
    available_cents: Cents
    current_card_id: Id | None
    single_limit_cents: Cents | None
    daily_limit_cents: Cents | None


class PayeeView(Contract):
    id: Id
    name: str
    alias: str | None
    phone_masked: str | None
    bank_name: str
    account_no_masked: str
    target_fingerprint: str
    is_internal: StrictBool
    created_at: Timestamp
    has_transfer_history: StrictBool


class TransactionView(Contract):
    id: Id
    account_id: Id
    direction: Literal["IN", "OUT"]
    kind: str
    status: Literal["POSTED", "PENDING", "SETTLED", "RELEASED"]
    amount_cents: Cents
    balance_after_cents: Cents
    available_after_cents: Cents
    counterparty: str
    memo: str | None
    category: str
    merchant_id: Id | None
    merchant_name: str | None
    payee_id: Id | None
    card_id: Id | None
    occurred_at: Timestamp


class CardView(Contract):
    id: Id
    account_id: Id
    last4: str
    status: CardStatus
    single_limit_cents: Cents
    daily_limit_cents: Cents
    daily_used_cents: Cents
    remaining_daily_cents: Cents
    max_single_limit_cents: Cents
    max_daily_limit_cents: Cents
    controls: ChannelFlags
    current: StrictBool
    replacement_id: Id | None


class RunView(Contract):
    status: str
    code: str | None
    due_at: Timestamp


class ScheduledView(Contract):
    id: Id
    from_account_id: Id
    payee_id: Id | None
    to_account_id: Id | None
    target_display: str
    amount_cents: Cents
    period: Period
    anchor_day: StrictInt | None
    weekday: StrictInt | None
    time_of_day: str
    next_run_at: Timestamp | None
    end_at: Timestamp | None
    status: str
    last_run: RunView | None


class SplitItem(Contract):
    id: Id
    payee_id: Id
    payee_name: str
    amount_cents: Cents
    status: Literal["UNPAID", "PAID"]
    paid_at: Timestamp | None


class SplitView(Contract):
    id: Id
    account_id: Id
    total_cents: Cents
    include_self: StrictBool
    self_share_cents: Cents
    received_cents: Cents
    outstanding_cents: Cents
    created_at: Timestamp
    items: tuple[SplitItem, ...]


class SubscriptionView(Contract):
    instruction_id: Id
    billing_id: Id
    account_id: Id
    merchant_id: Id
    merchant_name: str
    period: Period
    amount_cents: Cents
    max_amount_cents: Cents
    next_due_at: Timestamp
    status: str
    annual_cost_cents: Cents


class NavPoint(Contract):
    date: date
    nav: Decimal4


class ProductView(Contract):
    id: Id
    name: str
    product_type: ProductType
    risk_level: StrictInt = Field(ge=1, le=5)
    risk_label: str
    nav: Decimal4
    nav_date: date
    recent_return_bp: Int64
    confirm_days: StrictInt
    settle_days: StrictInt
    min_amount_cents: Cents
    risk_disclosure: str
    disclosure_version: str
    nav_history: tuple[NavPoint, ...]


class FundOrderView(Contract):
    id: Id
    account_id: Id
    product_id: Id
    type: Literal["SUB", "RED"]
    status: Literal["PENDING_CONFIRM", "PENDING_SETTLEMENT", "SETTLED", "FAILED", "EXPIRED"]
    amount_cents: Cents
    units: Decimal4
    nav: Decimal4 | None
    trade_date: date
    confirm_at: Timestamp | None
    settle_at: Timestamp | None
    failure_code: str | None


class BalancePoint(Contract):
    account_id: Id
    date: date
    balance_cents: Cents
    available_cents: Cents


class Category(Contract):
    category: str
    amount_cents: Cents
    share_bp: StrictInt
    previous_amount_cents: Cents


class DailySpending(Contract):
    date: date
    amount_cents: Cents


class AnomalyEvidence(Contract):
    baseline_count: StrictInt
    mean_cents: Cents | None
    threshold_cents: Cents | None
    previous_transaction_id: Id | None
    window_minutes: StrictInt | None


class Anomaly(Contract):
    transaction: TransactionView
    codes: tuple[str, ...]
    evidence: AnomalyEvidence


class UsageEvidence(Contract):
    recent_usage_count: StrictInt
    previous_usage_count: StrictInt


class DetectedSubscription(Contract):
    merchant_id: Id
    merchant_name: str
    instruction_id: Id | None
    billing_id: Id | None
    period: Period
    typical_amount_cents: Cents
    occurrences: StrictInt
    last_charged_at: Timestamp
    is_idle: StrictBool
    usage_evidence: UsageEvidence
    annual_cost_cents: Cents


class Normalization(Contract):
    weekly_to_annual_factor: Literal[52]
    monthly_to_annual_factor: Literal[12]
    annual_to_monthly: Literal["floor_div_12"]


class Charge(Contract):
    billing_id: Id
    instruction_id: Id
    account_id: Id
    merchant_id: Id
    merchant_name: str
    due_at: Timestamp
    amount_cents: Cents
    max_amount_cents: Cents


class ScheduledCharge(Contract):
    instruction_id: Id
    account_id: Id
    target_display: str
    due_at: Timestamp
    amount_cents: Cents


class AccountReserve(Contract):
    account_id: Id
    reserve_cents: Cents
    max_outflow_cents: SignedCents


class Option(Contract):
    id: str
    label: str


class Question(Contract):
    id: str
    prompt: str
    options: tuple[Option, ...]


class RiskProfile(Contract):
    assessment_id: Id | None
    level: StrictInt | None
    assessed_at: Timestamp | None
    valid_until: date | None
    is_valid: StrictBool


class ProfileRisk(Contract):
    level: StrictInt | None
    is_valid: StrictBool
    valid_until: date | None


class HoldingView(Contract):
    account_id: Id
    product_id: Id
    units: Decimal4
    reserved_units: Decimal4
    available_units: Decimal4
    cost_cents: Cents
    reserved_cost_cents: Cents
    available_cost_cents: Cents
    nav: Decimal4
    market_value_cents: Cents
    unrealized_change_cents: Int64
    reserved_receivable_cents: Cents


def result_model(name: str, **fields: type) -> type[Contract]:
    return create_model(
        name, __base__=Contract, **{key: (kind, ...) for key, kind in fields.items()}
    )


def page(name: str, item: type, **extra: type) -> type[Contract]:
    return result_model(name, items=tuple[item, ...], next_cursor=str | None, **extra)


READ_RESULTS = {
    "get_accounts": result_model(
        "AccountsResult",
        accounts=tuple[AccountView, ...],
        daily_outflow_cents=Cents,
        daily_outflow_limit_cents=Cents,
        remaining_daily_outflow_cents=Cents,
        balance_history=tuple[BalancePoint, ...],
    ),
    "list_payees": page("PayeesResult", PayeeView),
    "get_transactions": page(
        "TransactionsResult",
        TransactionView,
        income_cents=Cents,
        expense_cents=Cents,
        totals_scope=Literal["filtered_all"],
    ),
    "get_spending_summary": result_model(
        "SpendingResult",
        month=str,
        total_cents=Cents,
        previous_total_cents=Cents,
        mom_change_cents=Int64,
        mom_change_bp=Int64 | None,
        categories=tuple[Category, ...],
        daily=tuple[DailySpending, ...],
        transfer_out_cents=Cents,
    ),
    "detect_anomalies": result_model("AnomaliesResult", items=tuple[Anomaly, ...]),
    "get_cards": result_model("CardsResult", items=tuple[CardView, ...]),
    "list_scheduled": page("ScheduledResult", ScheduledView),
    "get_split_status": page("SplitsResult", SplitView),
    "detect_subscriptions": result_model(
        "DetectedSubscriptionsResult", items=tuple[DetectedSubscription, ...]
    ),
    "list_subscriptions": page(
        "SubscriptionsResult",
        SubscriptionView,
        total_monthly_cents=Cents,
        total_annual_cents=Cents,
        normalization_basis=Normalization,
    ),
    "upcoming_charges": result_model(
        "UpcomingResult",
        items=tuple[Charge, ...],
        scheduled_items=tuple[ScheduledCharge, ...],
        charges_total_cents=Cents,
        scheduled_transfers_total_cents=Cents,
        reserve_total_cents=Cents,
        by_account=tuple[AccountReserve, ...],
    ),
    "get_risk_questionnaire": result_model(
        "QuestionnaireResult",
        questionnaire_id=str,
        questions=tuple[Question, ...],
        validity_months=Literal[12],
    ),
    "get_risk_profile": RiskProfile,
    "list_products": page("ProductsResult", ProductView),
    "compare_products": result_model(
        "CompareResult", items=tuple[ProductView, ...], comparison_fields=tuple[str, ...]
    ),
    "get_holdings": result_model(
        "HoldingsResult", holdings=tuple[HoldingView, ...], orders=tuple[FundOrderView, ...]
    ),
    "get_product": ProductView,
    "get_profile": result_model(
        "ProfileResult",
        customer_id=Id,
        display_name=str,
        phone_masked=str,
        risk_profile=ProfileRisk,
        sim_now=Timestamp,
    ),
}
WRITE_RESULTS = {
    "transfer": result_model(
        "TransferResult",
        transfer_id=Id,
        transaction_ids=tuple[Id, ...],
        from_available_after_cents=Cents,
        to_available_after_cents=Cents | None,
    ),
    "add_payee": result_model("AddPayeeResult", payee_id=Id, target_fingerprint=str, no_op=bool),
    "schedule_transfer": result_model(
        "ScheduleResult",
        instruction_id=Id,
        next_run_at=Timestamp,
        grant_scope_hash=str,
        commitment_30d_cents=Cents,
    ),
    "cancel_scheduled": result_model(
        "CancelScheduleResult", instruction_id=Id, status=Literal["DELETED"], no_op=bool
    ),
    "create_split_request": SplitView,
    "update_card_limits": result_model(
        "LimitsResult", card=CardView, direction=Literal["TIGHTEN", "LOOSEN"]
    ),
    "update_card_controls": result_model(
        "ControlsResult", card=CardView, direction=Literal["TIGHTEN", "LOOSEN"]
    ),
    "freeze_card": result_model("FreezeResult", card_id=Id, status=Literal["FROZEN"], no_op=bool),
    "unfreeze_card": result_model(
        "UnfreezeResult", card_id=Id, status=Literal["ACTIVE"], no_op=bool
    ),
    "report_card_lost": result_model(
        "LostResult",
        old_card_id=Id,
        status=Literal["LOST"],
        new_card_id=Id | None,
        new_last4=str | None,
    ),
    "cancel_subscription": result_model(
        "CancelSubscriptionResult",
        instruction_id=Id,
        status=Literal["DELETED"],
        monthly_saving_cents=Cents,
        annual_saving_cents=Cents,
        no_op=bool,
    ),
    "submit_risk_assessment": result_model(
        "AssessmentResult",
        assessment_id=Id,
        score=StrictInt,
        level=StrictInt,
        assessed_at=Timestamp,
        valid_until=date,
    ),
    "subscribe_product": result_model(
        "SubscribeResult",
        order=FundOrderView,
        available_after_cents=Cents,
        pending_debit_after_cents=Cents,
        estimated_units=Decimal4,
    ),
    "redeem_product": result_model(
        "RedeemResult",
        order=FundOrderView,
        available_units_after=Decimal4,
        estimated_amount_cents=Cents,
    ),
}
