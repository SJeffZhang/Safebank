from enum import StrEnum


class AccountType(StrEnum):
    CHECKING = "CHECKING"
    SAVINGS = "SAVINGS"
    INTERNAL = "INTERNAL"


class CardStatus(StrEnum):
    ACTIVE = "ACTIVE"
    FROZEN = "FROZEN"
    LOST = "LOST"


class Phase(StrEnum):
    POSTED = "POSTED"
    PENDING = "PENDING"
    POST_PENDING = "POST_PENDING"
    VOID_PENDING = "VOID_PENDING"


class Risk(StrEnum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"


class IntentStatus(StrEnum):
    PREPARED = "PREPARED"
    COMMITTED = "COMMITTED"
    CANCELLED = "CANCELLED"
