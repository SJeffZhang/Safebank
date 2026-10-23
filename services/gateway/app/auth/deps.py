"""FastAPI dependency resolving the caller identity (PRD §7.1).

``resolve_caller`` looks up the auth matrix, checks the presented service token
and (when the matrix entry requires it) the bearer JWT, and returns a
:class:`Caller` — including ``sid`` and ``utts`` so G4 can enforce
``utterance_ids ⊆ token.utts``. Open matrix entries return ``None``.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import Request
from safebank_contracts.codes import Code

from ..errors import GatewayError
from ..schemas.enums import ServiceKind
from ..schemas.identity import Caller
from .matrix import CallerSpec, lookup
from .tokens import identify_service, verify_jwt


def _match_spec(
    spec: CallerSpec,
    *,
    token: str | None,
    authorization: str | None,
    env: dict[str, str],
    public_key: bytes | None,
) -> Caller:
    if spec.service is not None:
        if token is None or identify_service(token, env) is not spec.service:
            raise GatewayError(Code.UNAUTHENTICATED, 401)
        kind = spec.service
    else:
        kind = identify_service(token, env)

    claims = None
    if spec.jwt_aud is not None:
        if public_key is None or not authorization or not authorization.startswith("Bearer "):
            raise GatewayError(Code.UNAUTHENTICATED, 401)
        claims = verify_jwt(
            authorization.removeprefix("Bearer ").strip(),
            public_key,
            expected_aud=spec.jwt_aud,
            require_sid=spec.require_sid,
        )
        missing = spec.scopes - set(claims.scope)
        if missing:
            raise GatewayError(Code.FORBIDDEN, 403)

    return Caller(
        kind=kind or spec.service or ServiceKind.F,
        customer_id=claims.sub if claims else None,
        sid=claims.sid if claims else None,
        utts=claims.utts if claims else (),
        jti=claims.jti if claims else None,
        scopes=frozenset(claims.scope) if claims else frozenset(),
    )


def resolve_caller(
    request: Request,
    *,
    env: dict[str, str] | None = None,
    public_key: bytes | None = None,
) -> Caller | None:
    env = dict(os.environ if env is None else env)
    entry = lookup(request.method, request.url.path)
    if entry is None:
        raise GatewayError(Code.FORBIDDEN, 403)
    if entry == ():
        return None
    token = request.headers.get("X-Service-Token")
    authorization = request.headers.get("Authorization")
    last: GatewayError | None = None
    for spec in entry:
        try:
            return _match_spec(
                spec, token=token, authorization=authorization, env=env, public_key=public_key
            )
        except GatewayError as exc:
            last = exc
    raise last if last is not None else GatewayError(Code.UNAUTHENTICATED, 401)


def caller_dependency(env: dict[str, str] | None = None, public_key: bytes | None = None) -> Any:
    """FastAPI dependency wrapping :func:`resolve_caller` with injected config."""

    async def _dependency(request: Request) -> Caller | None:
        return resolve_caller(request, env=env, public_key=public_key)

    return _dependency
