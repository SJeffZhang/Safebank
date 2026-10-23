"""Strict W1 request inputs; wire output models remain in frozen schemas."""

from typing import Any, Literal

from pydantic import Field, model_validator
from safebank_contracts.base import Contract
from safebank_contracts.tools import Id

from ..schemas.enums import ActionKind


class Login(Contract):
    customer_id: Id
    pin: str = Field(strict=True, min_length=1, max_length=128)


class RequestId(Contract):
    request_id: Id


class Registration(RequestId):
    kind: Literal["typed", "hint", "action"]
    text: str | None = Field(default=None, strict=True, min_length=1, max_length=8192)
    hint_id: Id | None = None
    action_id: Id | None = None
    clarification_id: Id | None = None

    @model_validator(mode="after")
    def exclusive(self):
        values = {"typed": self.text, "hint": self.hint_id, "action": self.action_id}
        if any((value is not None) != (key == self.kind) for key, value in values.items()):
            raise ValueError("kind requires exactly its own source field")
        if self.clarification_id is not None and self.kind != "typed":
            raise ValueError("clarification_id is only accepted for typed slot answers")
        return self


class ActionRequest(RequestId):
    kind: Literal["FORM", "CLARIFICATION"]
    action_kind: ActionKind | None
    fields: dict[str, Any] | list[dict[str, str]]
    parent_utterance_id: Id | None = None

    @model_validator(mode="after")
    def shape(self):
        if self.kind == "FORM":
            if (
                self.action_kind is None
                or not isinstance(self.fields, dict)
                or self.parent_utterance_id is not None
            ):
                raise ValueError("FORM requires action kind, tool args, and no parent")
        elif (
            self.action_kind is not None
            or not isinstance(self.fields, list)
            or not self.parent_utterance_id
        ):
            raise ValueError(
                "CLARIFICATION requires fields list and parent; action kind must be null"
            )
        return self
