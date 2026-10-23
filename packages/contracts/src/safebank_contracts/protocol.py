"""L3 execution/recovery/outbox response contracts (PRD §8.5)."""

from typing import Annotated, Literal

from pydantic import Field, StrictInt, model_validator

from .base import Contract, Timestamp
from .plan import ExecutionResult
from .state import Operation
from .tools import Id


class PrepareResult(Contract):
    status: Literal["PREPARED", "COMMITTED"]
    attempt: Annotated[StrictInt, Field(ge=1)]


class ExecutionStatus(Contract):
    status: Literal["PREPARED", "COMMITTED", "CANCELLED"]
    result: ExecutionResult | None

    @model_validator(mode="after")
    def terminal(self):
        if (self.status == "COMMITTED") != (self.result is not None):
            raise ValueError("only COMMITTED has a durable execution result")
        return self


class OutboxItem(Contract):
    event_id: Id
    payload: Operation
    delivered_at_wall: Timestamp | None
    commit_seq: Annotated[StrictInt, Field(ge=1)]


class OutboxPage(Contract):
    items: tuple[OutboxItem, ...]
    next_cursor: str | None


class AckResult(Contract):
    acknowledged: Annotated[StrictInt, Field(ge=0)]
