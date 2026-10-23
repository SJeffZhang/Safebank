"""Static authorization matrix data format and lookup (PRD §7.1).

A ``CredentialSpec`` states what must be presented for a ``(method, path)``:
a service token kind (F/A/S/D) and, for user routes, a JWT audience and the
required scope subset. W1 populates the full table; W0 registers the two
entries it owns.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ServiceKind = Literal["F", "A", "S", "D"]


@dataclass(frozen=True)
class CredentialSpec:
    service: ServiceKind | None = None
    jwt_aud: str | None = None
    scopes: frozenset[str] = frozenset()
    require_sid: bool = False


MATRIX: dict[tuple[str, str], CredentialSpec] = {
    ("GET", "/health"): CredentialSpec(),
    ("POST", "/internal/scheduled/authorize"): CredentialSpec(service="S"),
}


def lookup(method: str, path: str) -> CredentialSpec | None:
    return MATRIX.get((method.upper(), path))
