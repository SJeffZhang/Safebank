"""PRD §9.3/9.4 tool inputs. Validation never supplies authorization."""

from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from .base import Cents, Contract, Decimal4, PositiveCents, Timestamp

Id = Annotated[str, Field(strict=True, min_length=1, max_length=256)]
Period = Literal["ONCE", "WEEKLY", "MONTHLY"]
ProductType = Literal["MMF", "BOND", "MIXED", "EQUITY"]


class EntityRef(Contract):
    ref: Id


class Transfer(Contract):
    from_account_id: Id
    payee_id: Id | EntityRef | None
    to_account_id: Id | None
    amount_cents: PositiveCents
    memo: Annotated[str, Field(max_length=200)] | None

    @model_validator(mode="after")
    def target(self):
        if (self.payee_id is None) == (self.to_account_id is None):
            raise ValueError("exactly one payee or own-account target required")
        if self.to_account_id == self.from_account_id:
            raise ValueError("source and target must differ")
        return self


class AddPayee(Contract):
    name: Id
    account_no: Annotated[str, Field(strict=True, pattern=r"^\d{12,32}$")]
    bank_name: Id
    phone: str | None
    alias: str | None


class Schedule(Contract):
    from_account_id: Id
    payee_id: Id | EntityRef | None
    to_account_id: Id | None
    amount_cents: PositiveCents
    period: Period
    first_run_at: Timestamp
    end_at: Timestamp | None

    @model_validator(mode="after")
    def schedule(self):
        Transfer(
            from_account_id=self.from_account_id,
            payee_id=self.payee_id,
            to_account_id=self.to_account_id,
            amount_cents=self.amount_cents,
            memo=None,
        )
        if self.end_at is not None and self.end_at < self.first_run_at:
            raise ValueError("end precedes first execution")
        return self


class Instruction(Contract):
    instruction_id: Id


class CreateSplit(Contract):
    account_id: Id
    total_cents: PositiveCents
    participant_payee_ids: Annotated[tuple[Id, ...], Field(min_length=1, max_length=20)]
    include_self: StrictBool
    memo: Annotated[str, Field(max_length=200)] | None

    @model_validator(mode="after")
    def participants(self):
        ids = self.participant_payee_ids
        if len(set(ids)) != len(ids) or self.total_cents < len(ids) + int(self.include_self):
            raise ValueError("participants must be unique and each share positive")
        return self


class Limits(Contract):
    card_id: Id
    single_limit_cents: Cents | None
    daily_limit_cents: Cents | None

    @model_validator(mode="after")
    def change(self):
        if self.single_limit_cents is None and self.daily_limit_cents is None:
            raise ValueError("at least one limit required")
        if any(x == 0 for x in (self.single_limit_cents, self.daily_limit_cents) if x is not None):
            raise ValueError("limits must be positive")
        return self


class Controls(Contract):
    card_id: Id
    online: StrictBool | None
    overseas: StrictBool | None
    atm: StrictBool | None
    quickpass: StrictBool | None

    @model_validator(mode="after")
    def change(self):
        if all(getattr(self, name) is None for name in ("online", "overseas", "atm", "quickpass")):
            raise ValueError("at least one channel required")
        return self


class CardId(Contract):
    card_id: Id


class Lost(CardId):
    replace: StrictBool


class Answer(Contract):
    question_id: Id
    option_id: Literal["a", "b", "c", "d"]


class Assessment(Contract):
    questionnaire_id: Literal["rq_v1"]
    answers: Annotated[tuple[Answer, ...], Field(min_length=6, max_length=6)]

    @model_validator(mode="after")
    def unique(self):
        if len({a.question_id for a in self.answers}) != 6:
            raise ValueError("six different questions required")
        return self


class Subscribe(Contract):
    account_id: Id
    product_id: Id
    amount_cents: PositiveCents


class Redeem(Contract):
    account_id: Id
    product_id: Id
    units: Decimal4

    @model_validator(mode="after")
    def positive(self):
        if self.units <= 0:
            raise ValueError("positive units required")
        return self


WRITE_ARGS = {
    "transfer": Transfer,
    "add_payee": AddPayee,
    "schedule_transfer": Schedule,
    "cancel_scheduled": Instruction,
    "create_split_request": CreateSplit,
    "update_card_limits": Limits,
    "update_card_controls": Controls,
    "freeze_card": CardId,
    "unfreeze_card": CardId,
    "report_card_lost": Lost,
    "cancel_subscription": Instruction,
    "submit_risk_assessment": Assessment,
    "subscribe_product": Subscribe,
    "redeem_product": Redeem,
}


class Empty(Contract):
    pass


class Page(Contract):
    cursor: str | None = None
    limit: Annotated[StrictInt, Field(ge=1, le=100)] = 50


class AccountsQuery(Contract):
    history_days: Annotated[StrictInt, Field(ge=0, le=180)]


class PayeesQuery(Page):
    query: str | None


class Range(Contract):
    from_at: Timestamp
    to_at: Timestamp
    account_id: Id | None

    @model_validator(mode="after")
    def window(self):
        days = (self.to_at - self.from_at).total_seconds() / 86400
        if not 0 < days <= 180:
            raise ValueError("range must be positive and at most 180 days")
        return self


class TransactionsQuery(Range, Page):
    category: str | None
    merchant_id: Id | None
    kind: str | None


class SpendingQuery(Contract):
    month: Annotated[str, Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]
    account_id: Id | None


class CardsQuery(Contract):
    account_id: Id | None


class StatusQuery(Page):
    status: Literal["ACTIVE", "DELETED", "ALL"]


class SplitsQuery(Page):
    request_id: Id | None


class DetectSubscriptions(Contract):
    lookback_days: Annotated[StrictInt, Field(ge=90, le=180)]


class Upcoming(Contract):
    days: Annotated[StrictInt, Field(ge=1, le=30)]
    account_id: Id | None


class ProductsQuery(Page):
    max_risk_level: Annotated[StrictInt, Field(ge=1, le=5)] | None
    product_type: ProductType | None


class Compare(Contract):
    product_ids: Annotated[tuple[Id, ...], Field(min_length=2, max_length=4)]

    @model_validator(mode="after")
    def unique(self):
        if len(set(self.product_ids)) != len(self.product_ids):
            raise ValueError("duplicate products")
        return self


class HoldingsQuery(Contract):
    account_id: Id | None
    product_id: Id | None


class ProductQuery(Contract):
    product_id: Id


READ_ARGS = {
    "get_accounts": AccountsQuery,
    "list_payees": PayeesQuery,
    "get_transactions": TransactionsQuery,
    "get_spending_summary": SpendingQuery,
    "detect_anomalies": Range,
    "get_cards": CardsQuery,
    "list_scheduled": StatusQuery,
    "get_split_status": SplitsQuery,
    "detect_subscriptions": DetectSubscriptions,
    "list_subscriptions": StatusQuery,
    "upcoming_charges": Upcoming,
    "get_risk_questionnaire": Empty,
    "get_risk_profile": Empty,
    "list_products": ProductsQuery,
    "compare_products": Compare,
    "get_holdings": HoldingsQuery,
    "get_product": ProductQuery,
    "get_profile": Empty,
}
