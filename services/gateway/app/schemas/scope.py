"""Authorization scope shapes shared by W1 (producer), W2/W3 (consumers).

``NormalizedScope`` is the server-side deterministic parse result (PRD §7.4);
``AuthorizationInputs`` is what the orchestrator places into ``EvalContext``.
"""

from __future__ import annotations

from typing import Any

from safebank_contracts.base import Contract
from safebank_contracts.tools import Id

from .enums import ParseStatus, UtteranceStatus


class ScopeTransfer(Contract):
    target_fingerprint: str
    amount_cents: int
    from_account_id: Id | None = None


class NormalizedScope(Contract):
    action_kinds: tuple[str, ...]
    from_account_id: Id | None = None
    transfers: tuple[ScopeTransfer, ...] = ()
    period: str | None = None
    channel: str | None = None
    fields: dict[str, Any] = {}


class UtteranceRef(Contract):
    id: Id
    kind: str
    text: str | None
    scope: NormalizedScope
    parse_status: ParseStatus
    echoes_untrusted: bool = False
    parent_ids: tuple[Id, ...] = ()
    clarification_id: Id | None = None
    created_at_wall: str
    expires_at_wall: str


class AuthorizationInputs(Contract):
    """W1 output placed into ``ctx.authorization`` before evaluation.

    ``utterance_ids`` must be a subset of the assistant token's ``utts``
    (checked by the G4 route before the pipeline runs).
    """

    sid: Id
    customer_id: Id
    utterance_ids: tuple[Id, ...]
    utterances: dict[Id, UtteranceRef] = {}
    scope_ids: tuple[Id, ...] = ()
    authorization_epoch: int = 0
    statuses: dict[Id, UtteranceStatus] = {}
