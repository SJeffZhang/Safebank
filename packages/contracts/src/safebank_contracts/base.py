"""PRD §0.1/§9.1: exact, JSON-safe financial scalar contracts."""

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Annotated, Any, TypeVar

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    WithJsonSchema,
)

BIGINT_MAX = 2**63 - 1


def exact_int(value: Any) -> int:
    if type(value) is not int:
        raise ValueError("integer required; no bool, float or string coercion")
    return value


Int64 = Annotated[int, BeforeValidator(exact_int), Field(ge=-BIGINT_MAX - 1, le=BIGINT_MAX)]
Cents = Annotated[Int64, Field(ge=0)]
PositiveCents = Annotated[Cents, Field(gt=0, le=100_000_000)]


def decimal4(value: Any) -> Decimal:
    if not isinstance(value, (str, Decimal)):
        raise ValueError("decimal string or Decimal required")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("invalid decimal") from exc
    if not number.is_finite() or number < 0 or number >= Decimal("100000000000000"):
        raise ValueError("outside numeric(18,4) range")
    if number != number.quantize(Decimal("0.0001")):
        raise ValueError("at most four fractional digits")
    return number.quantize(Decimal("0.0001"))


Decimal4 = Annotated[
    Decimal,
    BeforeValidator(decimal4),
    WithJsonSchema({"type": "string", "pattern": r"^\d{1,14}(\.\d{1,4})?$"}),
    PlainSerializer(lambda v: format(v, ".4f"), return_type=str),
]


def aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone offset required")
    return value


def timestamp_input(value: Any):
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value
    ):
        raise ValueError("RFC3339 timestamp with timezone required")
    return value


Timestamp = Annotated[datetime, BeforeValidator(timestamp_input), AfterValidator(aware)]


T = TypeVar("T")
FrozenMap = Annotated[
    dict[str, T], AfterValidator(MappingProxyType), PlainSerializer(dict, return_type=dict)
]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)
