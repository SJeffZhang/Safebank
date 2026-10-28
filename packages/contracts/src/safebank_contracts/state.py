"""Immutable serializable shadow state and typed persistence effects (PRD §7.7.1)."""

from datetime import date
from functools import reduce
from operator import or_
from typing import Annotated, Literal

from pydantic import (
    Field,
    JsonValue,
    StrictBool,
    StrictInt,
    create_model,
    model_validator,
)

from .base import Cents, Contract, FrozenMap, Timestamp
from .enums import AccountType, CardStatus, Phase
from .obligations import ObligationSnapshot
from .tools import Id
from .views import (
    ChannelFlags,
    FundOrderView,
    HoldingView,
    ProductView,
    ScheduledView,
    SplitView,
    SubscriptionView,
)


class Account(Contract):
    id: Id
    customer_id: Id | None
    type: AccountType
    display_name: str
    account_no: str
    currency: Literal["CNY"] = "CNY"
    ledger: Literal["CNY"] = "CNY"
    current_card_id: Id | None = None
    single_limit_cents: Cents = 500_000
    daily_limit_cents: Cents = 2_000_000
    flags: StrictInt = 1
    debits_posted_cents: Cents = 0
    credits_posted_cents: Cents = 0
    debits_pending_cents: Cents = 0
    credits_pending_cents: Cents = 0

    @property
    def balance_cents(self) -> int:
        return self.credits_posted_cents - self.debits_posted_cents

    @property
    def available_cents(self) -> int:
        return self.balance_cents - self.debits_pending_cents

    @model_validator(mode="after")
    def bounds(self):
        if self.customer_id is not None and (
            self.available_cents < 0 or self.type == AccountType.INTERNAL
        ):
            raise ValueError("customer account cannot overdraw or be INTERNAL")
        if not 0 < self.single_limit_cents <= self.daily_limit_cents:
            raise ValueError("invalid account limits")
        return self


class Card(Contract):
    id: Id
    account_id: Id
    last4: str
    status: CardStatus
    single_limit_cents: Cents
    daily_limit_cents: Cents
    max_single_limit_cents: Cents = 5_000_000
    max_daily_limit_cents: Cents = 10_000_000
    controls: ChannelFlags
    replaces_id: Id | None = None
    created_at: Timestamp

    @model_validator(mode="after")
    def limits(self):
        if not (
            0 < self.single_limit_cents <= self.daily_limit_cents <= self.max_daily_limit_cents
            and self.single_limit_cents <= self.max_single_limit_cents
        ):
            raise ValueError("invalid card limits")
        return self


class Customer(Contract):
    id: Id
    display_name: str
    phone: str
    risk_level: StrictInt | None = None
    risk_assessed_at: Timestamp | None = None
    risk_valid_until: str | None = None


class Payee(Contract):
    id: Id
    customer_id: Id
    name: str
    alias: str | None
    phone: str | None
    bank_name: str
    account_no: str
    target_account_id: Id | None
    target_fingerprint: str
    created_at: Timestamp


class DirectoryEntry(Contract):
    target_fingerprint: str
    target_account_id: Id | None
    customer_id: Id | None


class LedgerTransfer(Contract):
    id: Id
    debit_account_id: Id
    credit_account_id: Id
    amount_cents: Cents = Field(gt=0)
    code: str
    phase: Phase
    pending_id: Id | None
    reverses_id: Id | None = None
    plan_id: Id | None
    event_id: Id | None = None
    step_id: Id
    leg: str = "main"
    occurred_at: Timestamp
    expires_at: Timestamp | None


class Transaction(Contract):
    id: Id
    transfer_id: Id
    customer_id: Id
    account_id: Id
    direction: Literal["IN", "OUT"]
    kind: str
    phase: Phase
    status: Literal["POSTED", "PENDING", "SETTLED", "RELEASED"]
    amount_cents: Cents
    balance_after_cents: Cents
    available_after_cents: Cents
    counterparty: str
    category: str
    memo: str | None
    merchant_id: Id | None = None
    payee_id: Id | None = None
    card_id: Id | None = None
    channel: str | None = None
    occurred_at: Timestamp
    sequence: StrictInt = 0


class Operation(Contract):
    event_id: Id
    customer_id: Id
    plan_id: Id
    step_id: Id
    action_kind: str
    target_fingerprint: str | None
    amount_cents: Cents
    occurred_at: Timestamp
    commit_seq: StrictInt = 0
    payload: FrozenMap[JsonValue] = Field(default_factory=dict)


class Counters(Contract):
    daily_outflow_cents: FrozenMap[Cents] = Field(default_factory=dict)
    rolling_outflow_cents: FrozenMap[Cents] = Field(default_factory=dict)
    daily_used_cents: FrozenMap[Cents] = Field(default_factory=dict)


class RiskAssessmentRecord(Contract):
    """One completed risk questionnaire submission (PRD §5.5 / §8.3 risk_assessments)."""

    id: Id
    customer_id: Id
    questionnaire_id: str
    answers: tuple[tuple[str, str], ...]  # ((qid, oid), ...)
    score: StrictInt
    level: str  # "R1"–"R5"
    assessed_at: Timestamp
    valid_until: date
    plan_id: Id


class StateScope(Contract):
    customer_ids: tuple[Id, ...]
    directory: FrozenMap[DirectoryEntry] = Field(default_factory=dict)


class BankState(Contract):
    sim_now: Timestamp
    clock_version: StrictInt
    versions: FrozenMap[StrictInt]
    accounts: FrozenMap[Account]
    customers: FrozenMap[Customer]
    cards: FrozenMap[Card]
    payees: FrozenMap[Payee]
    transfers: FrozenMap[LedgerTransfer] = Field(default_factory=dict)
    transactions: tuple[Transaction, ...] = ()
    counters: Counters = Field(default_factory=Counters)
    directory: FrozenMap[DirectoryEntry] = Field(default_factory=dict)
    ops_7d: tuple[Operation, ...] = ()
    instructions: tuple[ScheduledView, ...] = ()
    subscriptions: tuple[SubscriptionView, ...] = ()
    obligations: ObligationSnapshot | None = None
    products: tuple[ProductView, ...] = ()
    holdings: tuple[HoldingView, ...] = ()
    orders: tuple[FundOrderView, ...] = ()
    assessments: tuple[RiskAssessmentRecord, ...] = ()
    splits: tuple[SplitView, ...] = ()
    dataset_revision: str = "p1_core_v1"


class PostTransfer(Contract):
    type: Literal["PostTransfer"] = "PostTransfer"
    transfer: LedgerTransfer


class AppendTransaction(Contract):
    type: Literal["AppendTransaction"] = "AppendTransaction"
    transaction: Transaction


class UpsertCard(Contract):
    type: Literal["UpsertCard"] = "UpsertCard"
    card: Card
    make_current: StrictBool = False


class InsertPayee(Contract):
    type: Literal["InsertPayee"] = "InsertPayee"
    payee: Payee


class OperationEvent(Contract):
    type: Literal["OperationEvent"] = "OperationEvent"
    operation: Operation


class InsertAssessment(Contract):
    """Effect: persist a completed risk assessment + update customer summary."""

    type: Literal["InsertAssessment"] = "InsertAssessment"
    record: RiskAssessmentRecord


# Typed future effects reserve the public algebra without implementing their business paths.
FUTURE_EFFECTS = {
    "InsertSplit": create_model(
        "InsertSplit",
        __base__=Contract,
        type=(Literal["InsertSplit"], "InsertSplit"),
        split=(SplitView, ...),
    ),
    "ScheduleEventJob": create_model(
        "ScheduleEventJob",
        __base__=Contract,
        type=(Literal["ScheduleEventJob"], "ScheduleEventJob"),
        event_id=(Id, ...),
        kind=(str, ...),
        due_at=(Timestamp, ...),
        payload=(dict[str, JsonValue], ...),
    ),
    "InsertInstruction": create_model(
        "InsertInstruction",
        __base__=Contract,
        type=(Literal["InsertInstruction"], "InsertInstruction"),
        instruction=(ScheduledView, ...),
    ),
    "SetInstructionStatus": create_model(
        "SetInstructionStatus",
        __base__=Contract,
        type=(Literal["SetInstructionStatus"], "SetInstructionStatus"),
        instruction_id=(Id, ...),
        status=(Literal["ACTIVE", "DISABLED", "DELETED"], ...),
    ),
    "InsertFundOrder": create_model(
        "InsertFundOrder",
        __base__=Contract,
        type=(Literal["InsertFundOrder"], "InsertFundOrder"),
        order=(FundOrderView, ...),
    ),
    "UpdateFundOrder": create_model(
        "UpdateFundOrder",
        __base__=Contract,
        type=(Literal["UpdateFundOrder"], "UpdateFundOrder"),
        order=(FundOrderView, ...),
    ),
    "UpdateHolding": create_model(
        "UpdateHolding",
        __base__=Contract,
        type=(Literal["UpdateHolding"], "UpdateHolding"),
        holding=(HoldingView, ...),
    ),
}
LedgerEffect = Annotated[
    reduce(
        or_,
        [
            PostTransfer,
            AppendTransaction,
            UpsertCard,
            InsertPayee,
            InsertAssessment,
            OperationEvent,
            *FUTURE_EFFECTS.values(),
        ],
    ),
    Field(discriminator="type"),
]
