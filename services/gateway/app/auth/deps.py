"""FastAPI dependencies translating the auth matrix into enforceable checks."""

from __future__ import annotations

import os
from collections.abc import Callable

from fastapi import Header, Request
from safebank_contracts.codes import Code

from ..errors import GatewayError
from .matrix import CredentialSpec, ServiceKind, lookup
from .tokens import identify_service, verify_jwt


def service_token_dependency(kind: ServiceKind, env: dict[str, str] | None = None) -> Callable:
    env = env if env is not None else os.environ

    def _require(x_service_token: str | None = Header(default=None)) -> str:
        if x_service_token is None or not identify_service(x_service_token, env) == kind:
            raise GatewayError(Code.UNAUTHENTICATED, 401)
        return kind

    return _require


def jwt_dependency(
    expected_aud: str,
    scopes: frozenset[str] | None = None,
    *,
    require_sid: bool = False,
    public_key: bytes | None = None,
) -> Callable:
    scopes = scopes or frozenset()

    def _require(authorization: str | None = Header(default=None)) -> dict:
        if public_key is None:
            raise GatewayError(Code.UNAUTHENTICATED, 401)
        if not authorization or not authorization.startswith("Bearer "):
            raise GatewayError(Code.UNAUTHENTICATED, 401)
        claims = verify_jwt(
            authorization.removeprefix("Bearer ").strip(),
            public_key,
            expected_aud=expected_aud,
            require_sid=require_sid,
        )
        missing = scopes - set(claims.scope)
        if missing:
            raise GatewayError(Code.FORBIDDEN, 403)
        return {"sub": claims.sub, "sid": claims.sid, "scope": claims.scope, "jti": claims.jti}

    return _require


def matrix_guard(request: Request, public_key: bytes | None = None) -> CredentialSpec | None:
    """Resolve and enforce the matrix entry for the current request.

    Open entries (no service, no JWT) return ``None``. Service-only entries
    check ``X-Service-Token``. Service+JWT entries additionally verify the
    bearer token scope. W1 fills in JWT issuance; W0 keeps the dependency
    honest for the entries it registers.
    """
    spec = lookup(request.method, request.url.path)
    if spec is None:
        # Unregistered routes are refused at the routing layer, not here.
        raise GatewayError(Code.FORBIDDEN, 403)
    if spec.service is None and spec.jwt_aud is None:
        return None
    token = request.headers.get("X-Service-Token")
    if spec.service is not None and not (
        token and identify_service(token, os.environ) == spec.service
    ):
        raise GatewayError(Code.UNAUTHENTICATED, 401)
    if spec.jwt_aud is not None:
        authorization = request.headers.get("Authorization", "")
        if not authorization.startswith("Bearer ") or public_key is None:
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
    return spec
