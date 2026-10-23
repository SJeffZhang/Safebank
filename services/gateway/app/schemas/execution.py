"""Execution acknowledgement (202 UNKNOWN, PRD §9.5 G8)."""

from __future__ import annotations

from safebank_contracts.base import Contract
from safebank_contracts.tools import Id


class ExecutionAccepted(Contract):
    plan_id: Id
    status: str = "UNKNOWN"
