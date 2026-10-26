"""Execution acknowledgement (202 UNKNOWN, PRD §9.5 G8) and G5 plan status view."""

from __future__ import annotations

from typing import Any, Literal

from safebank_contracts.base import Contract
from safebank_contracts.tools import Id


class ExecutionAccepted(Contract):
    plan_id: Id
    status: Literal["UNKNOWN"] = "UNKNOWN"


class PlanStatusView(Contract):
    """G5 GET /plans/{plan_id} response (PRD §9.5)."""

    plan_id: Id
    status: str
    decision: dict[str, Any] | None = None
    execution: dict[str, Any] | None = None
