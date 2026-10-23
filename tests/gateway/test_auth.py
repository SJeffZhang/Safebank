import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from safebank_contracts.codes import Code

from services.gateway.app.auth.tokens import (
    APP_AUDIENCE,
    ASSISTANT_AUDIENCE,
    identify_service,
    sign_jwt,
    verify_jwt,
)
from services.gateway.app.errors import GatewayError


@pytest.fixture
def keys():
    private = Ed25519PrivateKey.generate()
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return private_pem, public_pem


def test_sign_verify_roundtrip(keys):
    private_pem, public_pem = keys
    token = sign_jwt(
        private_pem,
        sub="cus_zs",
        aud=ASSISTANT_AUDIENCE,
        scope=["assistant"],
        sid="ses_1",
        utts=["utt_1"],
        jti="jti-1",
        now=int(time.time()),
    )
    claims = verify_jwt(token, public_pem, expected_aud=ASSISTANT_AUDIENCE, require_sid=True)
    assert claims.sub == "cus_zs"
    assert claims.sid == "ses_1"
    assert claims.utts == ("utt_1",)
    assert claims.scope == ("assistant",)


def test_app_jwt_without_sid(keys):
    private_pem, public_pem = keys
    token = sign_jwt(
        private_pem, sub="cus_zs", aud=APP_AUDIENCE, scope=["app", "approve"], jti="jti-2"
    )
    claims = verify_jwt(token, public_pem, expected_aud=APP_AUDIENCE)
    assert claims.sid is None
    assert set(claims.scope) == {"app", "approve"}


def test_tampered_token_rejected(keys):
    private_pem, public_pem = keys
    token = sign_jwt(private_pem, sub="cus_zs", aud=APP_AUDIENCE, scope=["app"], jti="jti-3")
    tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    with pytest.raises(GatewayError) as exc:
        verify_jwt(tampered, public_pem, expected_aud=APP_AUDIENCE)
    assert exc.value.code is Code.UNAUTHENTICATED


def test_expired_token_rejected(keys):
    private_pem, public_pem = keys
    now = int(time.time())
    token = sign_jwt(
        private_pem,
        sub="cus_zs",
        aud=APP_AUDIENCE,
        scope=["app"],
        jti="jti-4",
        now=now - 1000,
        ttl=10,
    )
    with pytest.raises(GatewayError) as exc:
        verify_jwt(token, public_pem, expected_aud=APP_AUDIENCE)
    assert exc.value.code is Code.TOKEN_EXPIRED


def test_wrong_audience_rejected(keys):
    private_pem, public_pem = keys
    token = sign_jwt(private_pem, sub="cus_zs", aud=APP_AUDIENCE, scope=["app"], jti="jti-5")
    with pytest.raises(GatewayError) as exc:
        verify_jwt(token, public_pem, expected_aud=ASSISTANT_AUDIENCE)
    assert exc.value.code is Code.UNAUTHENTICATED


def test_require_sid_rejects_app_token(keys):
    private_pem, public_pem = keys
    token = sign_jwt(private_pem, sub="cus_zs", aud=APP_AUDIENCE, scope=["app"], jti="jti-6")
    with pytest.raises(GatewayError):
        verify_jwt(token, public_pem, expected_aud=APP_AUDIENCE, require_sid=True)


def test_service_token_identification():
    env = {
        "FRONTEND_SERVICE_TOKEN": "f-token",
        "AGENT_SERVICE_TOKEN": "a-token",
        "SCHEDULER_SERVICE_TOKEN": "s-token",
        "DEMO_CONSOLE_TOKEN": "d-token",
    }
    assert identify_service("f-token", env) == "F"
    assert identify_service("a-token", env) == "A"
    assert identify_service("s-token", env) == "S"
    assert identify_service("d-token", env) == "D"
    assert identify_service("unknown", env) is None
    assert identify_service(None, env) is None
