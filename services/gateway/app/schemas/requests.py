"""Gateway request bodies not covered by the frozen contracts (PRD §9.5)."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field
from safebank_contracts.base import Contract
from safebank_contracts.tools import Id


class AppFormPlanRequest(Contract):
    request_id: Id
    session_id: Id
    form_action_ids: Annotated[tuple[Id, ...], Field(min_length=1, max_length=5)]
