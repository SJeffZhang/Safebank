"""Resolved caller identity (PRD §7.1): service kind + optional user JWT claims."""

from __future__ import annotations

from dataclasses import dataclass, field

from .enums import ServiceKind


@dataclass(frozen=True)
class Caller:
    kind: ServiceKind
    customer_id: str | None = None
    sid: str | None = None
    utts: tuple[str, ...] = ()
    jti: str | None = None
    scopes: frozenset[str] = field(default_factory=frozenset)

    @property
    def origin(self) -> str | None:
        # §7.1: L2 derives origin from the credential, never from the body.
        if self.kind is ServiceKind.A:
            return "agent"
        if self.kind is ServiceKind.F:
            return "app_form"
        return None
