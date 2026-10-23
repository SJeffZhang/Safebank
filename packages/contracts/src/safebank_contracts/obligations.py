"""Internal obligation snapshot models (ADR-009; PRD §7.7/§8.3).

These are not public read/write contracts: they express the raw merchant bills,
standing instructions and instruction-run history the L3 loader must prove it
has read before L2 may project reserves. A ``null`` ``BankState.obligations``
means obligations were not loaded; a non-null container with empty collections
means the scoped customers genuinely have no obligations.
"""

import re
from datetime import time
from typing import Annotated, Literal

from pydantic import AfterValidator, BeforeValidator, Field, StrictBool, model_validator

from .base import Contract, FrozenMap, PositiveCents, Timestamp
from .tools import Id, Period

InstructionStatus = Literal["ACTIVE", "DISABLED", "DELETED"]
InstructionPriority = Literal["URGENT", "HIGH", "MEDIUM", "LOW"]
BillingPeriod = Literal["WEEKLY", "MONTHLY"]
InstructionKind = Literal["SCHEDULED_TRANSFER", "MERCHANT_MANDATE"]
RunStatus = Literal["SUCCESS", "FAILED_INSUFFICIENT", "BLOCKED", "SKIPPED_REAPPROVAL"]
TargetOwnership = Literal["OWN", "OTHER", "EXTERNAL"]


def _strict_int(value):
    if type(value) is not int:
        raise ValueError("integer required; no bool or string coercion")
    return value


AnchorDay = Annotated[int, BeforeValidator(_strict_int), Field(ge=1, le=31)]
Weekday = Annotated[int, BeforeValidator(_strict_int), Field(ge=0, le=6)]
PositiveObligationCents = PositiveCents


def _local_time_input(value):
    if isinstance(value, time):
        if value.tzinfo is not None:
            raise ValueError("local execution time cannot carry a timezone")
        return value
    if isinstance(value, str):
        return value
    raise ValueError("local execution time required")


def _local_time_no_tz(value: time) -> time:
    if value.tzinfo is not None:
        raise ValueError("local execution time cannot carry a timezone")
    return value


LocalExecutionTime = Annotated[
    time, BeforeValidator(_local_time_input), AfterValidator(_local_time_no_tz)
]


def _hex64(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("64-char lowercase hex required")
    return value


GrantScopeHash = Annotated[str, BeforeValidator(_hex64)]
NonEmpty = Annotated[str, Field(min_length=1, max_length=256)]


class Merchant(Contract):
    id: Id
    name: NonEmpty
    mcc: NonEmpty
    category: NonEmpty
    usage_expected: StrictBool


class MerchantBilling(Contract):
    id: Id
    customer_id: Id
    account_id: Id
    merchant_id: Id
    amount_cents: PositiveObligationCents
    period: BillingPeriod
    anchor_day: AnchorDay | None
    weekday: Weekday | None
    time_of_day: LocalExecutionTime
    next_due_at: Timestamp
    active: StrictBool
    created_at: Timestamp

    @model_validator(mode="after")
    def anchors(self):
        if self.period == "MONTHLY":
            if self.anchor_day is None or self.weekday is not None:
                raise ValueError("MONTHLY billing requires anchor_day and no weekday")
        elif self.period == "WEEKLY":
            if self.weekday is None or self.anchor_day is not None:
                raise ValueError("WEEKLY billing requires weekday and no anchor_day")
        return self


class ScheduledInstruction(Contract):
    id: Id
    customer_id: Id
    kind: Literal["SCHEDULED_TRANSFER"] = "SCHEDULED_TRANSFER"
    amount_type: Literal["FIXED"] = "FIXED"
    status: InstructionStatus
    from_account_id: Id
    payee_id: Id | None
    to_account_id: Id | None
    amount_cents: PositiveObligationCents
    recurrence: Literal["PERIODIC"] = "PERIODIC"
    period: Period
    anchor_day: AnchorDay | None
    weekday: Weekday | None
    time_of_day: LocalExecutionTime
    next_run_at: Timestamp | None
    end_at: Timestamp | None
    priority: InstructionPriority
    grant_scope_hash: GrantScopeHash
    source_plan_id: Id
    created_at: Timestamp

    @model_validator(mode="after")
    def scheduled(self):
        if (self.payee_id is None) == (self.to_account_id is None):
            raise ValueError("scheduled instruction requires exactly one target")
        if self.to_account_id == self.from_account_id:
            raise ValueError("scheduled instruction source equals target")
        if self.status == "ACTIVE" and self.next_run_at is None:
            raise ValueError("ACTIVE scheduled instruction requires next_run_at")
        if self.period == "ONCE":
            if self.anchor_day is not None or self.weekday is not None:
                raise ValueError("ONCE instruction cannot have recurring anchors")
        elif self.period == "WEEKLY":
            if self.weekday is None or self.anchor_day is not None:
                raise ValueError("WEEKLY instruction requires weekday and no anchor_day")
        elif self.period == "MONTHLY":
            if self.anchor_day is None or self.weekday is not None:
                raise ValueError("MONTHLY instruction requires anchor_day and no weekday")
        return self


class MerchantMandate(Contract):
    id: Id
    customer_id: Id
    kind: Literal["MERCHANT_MANDATE"] = "MERCHANT_MANDATE"
    amount_type: Literal["DUES"] = "DUES"
    status: InstructionStatus
    from_account_id: Id
    merchant_id: Id
    billing_id: Id
    max_amount_cents: PositiveObligationCents
    recurrence: Literal["AS_PER_DUES"] = "AS_PER_DUES"
    end_at: Timestamp | None
    priority: InstructionPriority
    grant_scope_hash: GrantScopeHash
    source_plan_id: Id
    created_at: Timestamp


StandingInstruction = ScheduledInstruction | MerchantMandate


class InstructionTarget(Contract):
    instruction_id: Id
    source_customer_id: Id
    ownership: TargetOwnership
    target_account_id: Id | None
    target_customer_id: Id | None

    @model_validator(mode="after")
    def target(self):
        if self.ownership == "EXTERNAL":
            if self.target_account_id is not None or self.target_customer_id is not None:
                raise ValueError("EXTERNAL target cannot carry account/customer ids")
        else:
            if self.target_account_id is None or self.target_customer_id is None:
                raise ValueError("OWN/OTHER target requires account and customer ids")
        if self.ownership == "OWN" and self.target_customer_id != self.source_customer_id:
            raise ValueError("OWN target must belong to the source customer")
        if self.ownership == "OTHER" and self.target_customer_id == self.source_customer_id:
            raise ValueError("OTHER target must belong to a different customer")
        return self


class InstructionRun(Contract):
    id: Id
    customer_id: Id
    account_id: Id
    kind: InstructionKind
    instruction_id: Id | None
    billing_id: Id | None
    due_at: Timestamp
    event_id: Id
    status: RunStatus
    code: NonEmpty | None
    amount_cents: PositiveObligationCents
    transfer_id: Id | None

    @model_validator(mode="after")
    def run(self):
        if self.kind == "SCHEDULED_TRANSFER":
            if self.instruction_id is None or self.billing_id is not None:
                raise ValueError("scheduled run requires instruction_id and no billing_id")
        elif self.billing_id is None:
            raise ValueError("merchant run requires billing_id")
        if self.status == "SUCCESS":
            if self.instruction_id is None:
                raise ValueError("SUCCESS run requires instruction_id")
            if self.transfer_id is None or self.code is not None:
                raise ValueError("SUCCESS run requires transfer_id and no code")
        elif self.transfer_id is not None:
            raise ValueError("non-success run cannot reference a transfer")
        elif not self.code:
            raise ValueError("non-success run requires a code")
        return self


class ObligationSnapshot(Contract):
    covered_customer_ids: tuple[Id, ...]
    merchants: FrozenMap[Merchant]
    billings: FrozenMap[MerchantBilling]
    standing_instructions: FrozenMap[StandingInstruction]
    runs: tuple[InstructionRun, ...]
    instruction_targets: FrozenMap[InstructionTarget]

    @model_validator(mode="after")
    def covered(self):
        ids = self.covered_customer_ids
        if not ids:
            raise ValueError("covered_customer_ids cannot be empty")
        if list(ids) != sorted(set(ids)):
            raise ValueError("covered_customer_ids must be unique and sorted")
        return self
