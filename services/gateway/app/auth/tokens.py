"""Service-token recognition and EdDSA JWT verification interface (PRD §7.3).

W1 owns JWT issuance and login. W0 provides the verification interface plus a
signing helper for tests. Verification is strict: ``aud`` must be a single
string equal to the expected audience (a list aud never passes), and
``scope`` / ``utts`` must be lists of strings.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import jwt
from safebank_contracts.codes import Code
from sqlalchemy.ext.asyncio import AsyncConnection

from ..errors import GatewayError
from ..schemas.enums import ServiceKind
from ..schemas.identity import Caller

ISSUER = "safebank-gateway"
APP_AUDIENCE = "gateway-app"
ASSISTANT_AUDIENCE = "safebank-assistant"

SERVICE_TOKEN_ENV = {
    ServiceKind.F: "FRONTEND_SERVICE_TOKEN",
    ServiceKind.A: "AGENT_SERVICE_TOKEN",
    ServiceKind.S: "SCHEDULER_SERVICE_TOKEN",
    ServiceKind.D: "DEMO_CONSOLE_TOKEN",
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


def _str_list(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise GatewayError(Code.UNAUTHENTICATED, 401, message=f"{name} must be a list of strings")
    return tuple(value)


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
        # `audience` lets PyJWT validate presence/membership; the manual check
        # below rejects a list aud containing the expected value, which PyJWT
        # would otherwise accept and which would break app/assistant isolation.
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

    if any(type(payload.get(name)) is not int for name in ("exp", "nbf")):
        raise GatewayError(Code.UNAUTHENTICATED, 401)
    aud = payload.get("aud")
    if not isinstance(aud, str) or aud != expected_aud:
        raise GatewayError(Code.UNAUTHENTICATED, 401, message="aud must match exactly")
    sub = payload.get("sub")
    jti = payload.get("jti")
    if not isinstance(sub, str) or not sub or not isinstance(jti, str) or not jti:
        raise GatewayError(Code.UNAUTHENTICATED, 401)
    sid = payload.get("sid")
    if sid is not None and not isinstance(sid, str):
        raise GatewayError(Code.UNAUTHENTICATED, 401)
    if require_sid and not sid:
        raise GatewayError(Code.UNAUTHENTICATED, 401)
    scope = _str_list(payload.get("scope"), "scope")
    utts_value = payload.get("utts")
    utts = () if utts_value is None else _str_list(utts_value, "utts")
    return Claims(
        sub=sub,
        aud=aud,
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


async def issue_app(
    conn: AsyncConnection,
    private_key_pem: bytes,
    customer_id: str,
    now: datetime,
) -> tuple[str, datetime]:
    """Issue and persist a two-hour browser login; never attach a session id."""
    return await _issue(
        conn,
        private_key_pem,
        customer_id,
        now,
        aud=APP_AUDIENCE,
        scope=["app", "approve"],
        ttl=7200,
    )


async def issue_assistant(
    conn: AsyncConnection,
    private_key_pem: bytes,
    customer_id: str,
    sid: str,
    ids: list[str],
    now: datetime,
    *,
    expires_at: datetime | None = None,
) -> tuple[str, datetime]:
    """Bind only this registration's effective references; renewal has ids=[]."""
    ttl = 600
    if expires_at is not None:
        ttl = min(ttl, int((expires_at - now).total_seconds()))
    if ttl <= 0:
        raise GatewayError(Code.UTTERANCE_EXPIRED, 409)
    return await _issue(
        conn,
        private_key_pem,
        customer_id,
        now,
        aud=ASSISTANT_AUDIENCE,
        scope=["assistant"],
        ttl=ttl,
        sid=sid,
        ids=ids,
    )


async def _issue(
    conn: AsyncConnection,
    private_key_pem: bytes,
    customer_id: str,
    now: datetime,
    *,
    aud: str,
    scope: list[str],
    ttl: int,
    sid: str | None = None,
    ids: list[str] | None = None,
) -> tuple[str, datetime]:
    from .. import tables as t

    jti = f"jti-{secrets.token_hex(16)}"
    expiry = datetime.fromtimestamp(int(now.timestamp()) + ttl, tz=now.tzinfo)
    token = sign_jwt(
        private_key_pem,
        sub=customer_id,
        aud=aud,
        scope=scope,
        sid=sid,
        utts=ids,
        jti=jti,
        now=int(now.timestamp()),
        ttl=ttl,
    )
    await conn.execute(
        t.auth_sessions.insert().values(
            jti=jti,
            customer_id=customer_id,
            created_at_wall=now,
            expires_at_wall=expiry,
        )
    )
    return token, expiry


async def check_revocation(
    conn: AsyncConnection,
    caller: Caller,
    *,
    now: datetime | None = None,
) -> None:
    """Fail closed on missing/expired/revoked login or revoked/wrong-owner sid.

    Shared auth dependency must invoke this after signature verification. W1
    also calls it at its route boundary and from load_for_plan for defense in depth.
    """
    from sqlalchemy import select

    from .. import tables as t

    now = now or datetime.now(UTC)
    if caller.customer_id is None:
        return
    row = (
        (
            await conn.execute(
                select(t.auth_sessions).where(
                    t.auth_sessions.c.jti == caller.jti,
                    t.auth_sessions.c.customer_id == caller.customer_id,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row["revoked_at_wall"] is not None:
        raise GatewayError(Code.UNAUTHENTICATED, 401)
    if row["expires_at_wall"] <= now:
        raise GatewayError(Code.TOKEN_EXPIRED, 401)
    if caller.sid:
        session = (
            (
                await conn.execute(
                    select(t.sessions).where(
                        t.sessions.c.id == caller.sid,
                        t.sessions.c.customer_id == caller.customer_id,
                        t.sessions.c.revoked_at_wall.is_(None),
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if session is None:
            raise GatewayError(Code.UNAUTHENTICATED, 401)
