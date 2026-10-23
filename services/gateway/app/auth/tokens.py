"""Service-token recognition and EdDSA JWT verification interface (PRD §7.3).

W1 owns JWT issuance and login. W0 provides the verification interface plus a
signing helper for tests so that token semantics can be pinned before any
issuer exists.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Literal

import jwt
from safebank_contracts.codes import Code

from ..errors import GatewayError

ServiceKind = Literal["F", "A", "S", "D"]

ISSUER = "safebank-gateway"
APP_AUDIENCE = "gateway-app"
ASSISTANT_AUDIENCE = "safebank-assistant"

SERVICE_TOKEN_ENV = {
    "F": "FRONTEND_SERVICE_TOKEN",
    "A": "AGENT_SERVICE_TOKEN",
    "S": "SCHEDULER_SERVICE_TOKEN",
    "D": "DEMO_CONSOLE_TOKEN",
}


def service_token_matches(kind: ServiceKind, token: str | None, env: dict[str, str]) -> bool:
    expected = env.get(SERVICE_TOKEN_ENV[kind]) or ""
    if not expected or not token:
        return False
    return secrets.compare_digest(token.encode(), expected.encode())


def identify_service(token: str | None, env: dict[str, str]) -> ServiceKind | None:
    for kind in SERVICE_TOKEN_ENV:
        if service_token_matches(kind, token, env):
            return kind
    return None


@dataclass(frozen=True)
class Claims:
    sub: str
    aud: str
    scope: tuple[str, ...]
    jti: str
    exp: int
    nbf: int
    sid: str | None
    utts: tuple[str, ...]


def verify_jwt(
    token: str,
    public_key_pem: bytes,
    *,
    expected_aud: str,
    require_sid: bool = False,
) -> Claims:
    """Verify an EdDSA JWT: signature, alg, iss, aud, exp, nbf, jti, sub, sid."""
    if not token:
        raise GatewayError(Code.UNAUTHENTICATED, 401)
    try:
        payload = jwt.decode(
            token,
            public_key_pem,
            algorithms=["EdDSA"],
            issuer=ISSUER,
            audience=expected_aud,
            options={"require": ["exp", "nbf", "iss", "aud", "jti", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise GatewayError(Code.TOKEN_EXPIRED, 401) from exc
    except jwt.InvalidTokenError as exc:
        raise GatewayError(Code.UNAUTHENTICATED, 401) from exc

    sub = payload.get("sub")
    jti = payload.get("jti")
    if not sub or not jti:
        raise GatewayError(Code.UNAUTHENTICATED, 401)
    sid = payload.get("sid") or None
    if require_sid and not sid:
        raise GatewayError(Code.UNAUTHENTICATED, 401)
    scope = tuple(payload.get("scope") or ())
    utts = tuple(payload.get("utts") or ())
    return Claims(
        sub=sub,
        aud=payload["aud"],
        scope=scope,
        jti=jti,
        exp=int(payload["exp"]),
        nbf=int(payload["nbf"]),
        sid=sid,
        utts=utts,
    )


def sign_jwt(
    private_key_pem: bytes,
    *,
    sub: str,
    aud: str,
    scope: list[str],
    sid: str | None = None,
    utts: list[str] | None = None,
    jti: str | None = None,
    now: int | None = None,
    ttl: int = 600,
    nbf_skew: int = 0,
) -> str:
    """Test/issuance helper: mint an EdDSA JWT accepted by :func:`verify_jwt`."""
    now = int(time.time()) if now is None else now
    payload = {
        "iss": ISSUER,
        "sub": sub,
        "aud": aud,
        "scope": scope,
        "jti": jti or f"jti-{secrets.token_hex(8)}",
        "iat": now,
        "nbf": now - nbf_skew,
        "exp": now + ttl,
    }
    if sid is not None:
        payload["sid"] = sid
    if utts is not None:
        payload["utts"] = utts
    return jwt.encode(payload, private_key_pem, algorithm="EdDSA")
