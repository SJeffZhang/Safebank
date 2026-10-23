"""Gateway request bodies not covered by the frozen contracts (PRD §9.5)."""

from __future__ import annotations

from safebank_contracts.base import Contract
from safebank_contracts.tools import Id


class AppFormPlanRequest(Contract):
    request_id: Id
    session_id: Id
    form_action_ids: tuple[Id, ...]
