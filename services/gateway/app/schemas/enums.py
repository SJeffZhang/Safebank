"""Gateway state/status enums (PRD §7.2, §9.5).

These are the single source for the CHECK constraints in ``app/tables.py`` and
for the wire values in ``app/schemas``. ExecutionJobStatus / WorkflowEventStatus
are the values the W0 conductor decision fixed; they will be recorded into
PRD §7.2 by the conductor.
"""

from enum import StrEnum


class ServiceKind(StrEnum):
    F = "F"
    A = "A"
    S = "S"
    D = "D"


class PlanStatus(StrEnum):
    EVALUATED = "EVALUATED"
    REPLAN = "REPLAN"
    DENIED = "DENIED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    CONFIRMED = "CONFIRMED"
    EXECUTING = "EXECUTING"
    UNKNOWN = "UNKNOWN"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"
    USED = "USED"
    LOCKED = "LOCKED"


class UtteranceStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    CONSUMED = "CONSUMED"
    CANCELLED = "CANCELLED"


class ClarificationKind(StrEnum):
    SLOTS = "SLOTS"
    FORM_REQUIRED = "FORM_REQUIRED"


class ClarificationStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    CANCELLED = "CANCELLED"


class ParseStatus(StrEnum):
    SCOPED = "SCOPED"
    NO_WRITE = "NO_WRITE"
    INTENT_UNCLEAR = "INTENT_UNCLEAR"
    CLARIFYING = "CLARIFYING"


class ExecutionJobStatus(StrEnum):
    EXECUTING = "EXECUTING"
    FENCING = "FENCING"
    PAUSED = "PAUSED"
    UNKNOWN = "UNKNOWN"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class WorkflowEventStatus(StrEnum):
    PENDING = "PENDING"
    COMMITTED = "COMMITTED"
    BLOCKED = "BLOCKED"


class FindingEffect(StrEnum):
    DENY = "DENY"
    REPLAN = "REPLAN"
    RISK_YELLOW = "RISK_YELLOW"
    RISK_RED = "RISK_RED"
    INFO = "INFO"


class ActionKind(StrEnum):
    TRANSFER_EXTERNAL = "TRANSFER_EXTERNAL"
    TRANSFER_SELF = "TRANSFER_SELF"
    PAYEE_ADD = "PAYEE_ADD"
    SCHEDULE_CREATE = "SCHEDULE_CREATE"
    SCHEDULE_CANCEL = "SCHEDULE_CANCEL"
    SPLIT_CREATE = "SPLIT_CREATE"
    CARD_LIMIT_TIGHTEN = "CARD_LIMIT_TIGHTEN"
    CARD_LIMIT_LOOSEN = "CARD_LIMIT_LOOSEN"
    CARD_CONTROL_TIGHTEN = "CARD_CONTROL_TIGHTEN"
    CARD_CONTROL_LOOSEN = "CARD_CONTROL_LOOSEN"
    CARD_FREEZE = "CARD_FREEZE"
    CARD_UNFREEZE = "CARD_UNFREEZE"
    CARD_LOST = "CARD_LOST"
    MANDATE_CANCEL = "MANDATE_CANCEL"
    ASSESSMENT_SUBMIT = "ASSESSMENT_SUBMIT"
    FUND_SUBSCRIBE = "FUND_SUBSCRIBE"
    FUND_REDEEM = "FUND_REDEEM"
