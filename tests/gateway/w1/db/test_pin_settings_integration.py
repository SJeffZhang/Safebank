"""Real S1c runtime + W1 loader + PostgreSQL login, with hostile ambient config."""

import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from safebank_contracts.codes import Code
from sqlalchemy import select

from services.gateway.app import tables as t
from services.gateway.app.auth.pin import HASHER, configured_hashes
from services.gateway.app.auth.tokens import APP_AUDIENCE, verify_jwt
from services.gateway.app.config import from_env
from services.gateway.app.errors import GatewayError
from services.gateway.app.l3.client import L3Client
from services.gateway.app.l3.fake import FakeL3
from services.gateway.app.main import build_public_app
from services.gateway.app.runtime import gateway_runtime
from services.gateway.app.utterance.service import new_id
from tests.gateway.w1.db.support import ENV, headers


@pytest.fixture
def pin_settings(gateway_db, tmp_path):
    key = Ed25519PrivateKey.generate()
    private_path, public_path = tmp_path / "private.pem", tmp_path / "public.pem"
    private_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    private_path.chmod(0o600)
    public_path.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    return from_env(
        {
            **ENV,
            "JWT_PRIVATE_KEY_PATH": str(private_path),
            "JWT_PUBLIC_KEY_PATH": str(public_path),
            "GATEWAY_DATABASE_URL": gateway_db.runtime_url,
            "GATEWAY_SERVICE_TOKEN": "l3-token",
            "DEMO_PINS_FILE": str(tmp_path / "selected.json"),
        }
    )


def l3_factory(url, token):
    # No L3 write is involved in login; only the external L3 transport is fake.
    return L3Client(url, token, transport=FakeL3(token=token).transport())


async def assert_login_snapshot(settings, selected_pin, rejected_pin, remove_file):
    async with gateway_runtime(settings, l3_factory=l3_factory) as runtime:
        app = build_public_app(settings, env=ENV, runtime=runtime)
        if remove_file:
            from pathlib import Path

            Path(settings.demo_pins_file).unlink()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app, client=(new_id("ip"), 1234)),
            base_url="http://gateway",
        ) as client:
            rejected = await client.post(
                "/auth/login",
                headers=headers(),
                json={
                    "customer_id": "cus_zs",
                    "pin": rejected_pin,
                },
            )
            assert rejected.status_code == 401, rejected.text
            assert rejected.json()["error"]["code"] == "UNAUTHENTICATED"
            accepted = await client.post(
                "/auth/login",
                headers=headers(),
                json={
                    "customer_id": "cus_zs",
                    "pin": selected_pin,
                },
            )
            assert accepted.status_code == 200, accepted.text
            claims = verify_jwt(
                accepted.json()["app_token"], runtime.jwt_public_key, expected_aud=APP_AUDIENCE
            )
            async with runtime.db.connect() as conn:
                assert (
                    await conn.scalar(
                        select(t.auth_sessions.c.customer_id).where(
                            t.auth_sessions.c.jti == claims.jti
                        )
                    )
                    == "cus_zs"
                )


def test_two_settings_pin_files_override_opposite_environment_and_old_startup_cache(
    pin_settings, monkeypatch, tmp_path
):
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    first.write_text(json.dumps({"cus_zs": "123456"}))
    second.write_text(json.dumps({"cus_zs": "654321"}))
    first.chmod(0o600)
    second.chmod(0o600)
    settings_a = replace(pin_settings, demo_pins_file=str(first))
    settings_b = replace(pin_settings, demo_pins_file=str(second))
    configured_hashes.cache_clear()
    monkeypatch.setenv("DEMO_PINS_FILE", str(second))
    monkeypatch.setenv("DEMO_PIN_HASHES", json.dumps({"cus_zs": HASHER.hash("654321")}))
    configured_hashes()  # Deliberately poison the legacy zero-argument cache.

    async def run():
        await assert_login_snapshot(settings_a, "123456", "654321", remove_file=True)
        # Keep the old legacy cache; both ambient sources now oppose Settings B.
        monkeypatch.setenv("DEMO_PINS_FILE", str(first))
        monkeypatch.setenv("DEMO_PIN_HASHES", json.dumps({"cus_zs": HASHER.hash("123456")}))
        await assert_login_snapshot(settings_b, "654321", "123456", remove_file=True)
        # A later startup at a reused path must load replacement contents.
        first.write_text(json.dumps({"cus_zs": "777777"}))
        monkeypatch.setenv("DEMO_PINS_FILE", str(second))  # Now a missing ambient file.
        monkeypatch.setenv("DEMO_PIN_HASHES", "invalid ambient JSON")
        await assert_login_snapshot(settings_a, "777777", "123456", remove_file=True)

    try:
        asyncio.run(run())
    finally:
        configured_hashes.cache_clear()


@pytest.mark.parametrize("payload", [None, "not-json", '{"cus_zs":true}'])
def test_invalid_settings_pin_file_refuses_real_runtime_despite_valid_environment(
    pin_settings, monkeypatch, payload
):
    from pathlib import Path

    if payload is not None:
        Path(pin_settings.demo_pins_file).write_text(payload)
    monkeypatch.setenv("DEMO_PIN_HASHES", json.dumps({"cus_zs": HASHER.hash("123456")}))

    async def run():
        with pytest.raises(GatewayError) as exc:
            async with gateway_runtime(pin_settings, l3_factory=l3_factory):
                pytest.fail("bad explicit PIN file must refuse startup")
        assert exc.value.status == 503 and exc.value.code is Code.DEPENDENCY_UNAVAILABLE

    asyncio.run(run())
