"""Read tool envelope (PRD §9.1, §9.3)."""

from __future__ import annotations

from typing import Any

from safebank_contracts.base import Contract
from safebank_contracts.tools import Id


class ReadEnvelope(Contract):
    data: Any
    source_ref: Id
    untrusted_paths: tuple[str, ...] = ()
    versions: dict[str, int] = {}
    observed_at: str
