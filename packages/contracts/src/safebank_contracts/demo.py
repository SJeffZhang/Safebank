"""Strict, shared D4 synthetic event payload; never an L1 write tool."""

from typing import Literal

from pydantic import Field, model_validator

from .base import Cents, Contract


class DemoEventPayload(Contract):
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    kind: Literal["EXTERNAL_CREDIT", "EXTERNAL_DEBIT", "SPLIT_PAYMENT"]
    customer_id: str = Field(min_length=1, max_length=128)
    account_id: str = Field(min_length=1, max_length=128)
    amount_cents: Cents = Field(gt=0)
    card_id: str | None = None
    channel: Literal["ONLINE", "OVERSEAS", "ATM", "QUICKPASS", "POS"] | None = None
    merchant_id: str | None = None
    counterparty: str | None = None
    memo: str | None = None
    split_item_id: str | None = None

    @model_validator(mode="after")
    def fields_for_kind(self):
        if (self.kind == "SPLIT_PAYMENT") != (self.split_item_id is not None):
            raise ValueError("split payment requires its item only")
        if self.kind != "EXTERNAL_DEBIT" and any(
            x is not None for x in (self.card_id, self.channel, self.merchant_id)
        ):
            raise ValueError("card fields require external debit")
        if self.channel is not None and self.card_id is None:
            raise ValueError("channel requires a card")
        return self
