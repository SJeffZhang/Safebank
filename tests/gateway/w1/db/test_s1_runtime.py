"""S1 integration: real auth dependency, revocation persistence and installed resources.

The harness uses production W4a customer locks. Only the external L3 transport
is fake; no auth checker or clarification transition is replaced.
"""

import asyncio
import json

import pytest
from sqlalchemy import func, select

from services.gateway.app import tables as t
from services.gateway.app.auth.pin import configured_hashes
from services.gateway.app.utterance.service import new_id
from tests.gateway.w1.db.support import create, harness, headers, login, utter


@pytest.mark.parametrize("missing", ["pin_hashes", "jwt_private_key"])
def test_login_missing_central_secret_cannot_use_request_environment(
    gateway_db, monkeypatch, tmp_path, missing
):
    async def run():
        async with harness(gateway_db, monkeypatch) as (app, client, db, private, _, _, _):
            key_path = tmp_path / "fallback.pem"
            key_path.write_bytes(private)
            key_path.chmod(0o600)
            monkeypatch.setenv("JWT_PRIVATE_KEY_PATH", str(key_path))
            monkeypatch.setenv("DEMO_PIN_HASHES", json.dumps(app.state.pin_hashes))
            configured_hashes.cache_clear()
            delattr(app.state, missing)
            async with db.connect() as conn:
                before = await conn.scalar(select(func.count()).select_from(t.auth_sessions))
            response = await client.post(
                "/auth/login", headers=headers(), json={"customer_id": "cus_zs", "pin": "123456"}
            )
            assert response.status_code == 503, response.text
            assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
            async with db.connect() as conn:
                assert (
                    await conn.scalar(select(func.count()).select_from(t.auth_sessions)) == before
                )

    try:
        asyncio.run(run())
    finally:
        configured_hashes.cache_clear()


def test_logout_revokes_before_unrelated_customer_handlers(gateway_db, monkeypatch):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, _, _, _, _, _):
            app_token = await login(client)
            sid = await create(client, app_token, new_id("req"))
            registered = (await utter(client, app_token, sid, "给王五800")).json()
            assistant = registered["assistant_token"]

            # These handlers are still W5 stubs. Their 503 message proves
            # authentication reached that boundary, never business success.
            probes = [
                ("GET", "/audit", app_token, "f-test"),
            ]
            for method, path, token, service in probes:
                response = await client.request(method, path, headers=headers(token, service))
                assert response.status_code == 503, response.text
                assert "lands in" in response.json()["error"]["message"]

            # /plans/evaluate is now implemented (W3b). Before logout, prove
            # auth passed (not 401/403) — even 422 from the handler confirms
            # the middleware let the request through.
            evaluate_resp = await client.post(
                "/plans/evaluate",
                headers=headers(assistant, "a-test"),
                json={"request_id": new_id("req"), "plan_draft": {}},
            )
            assert evaluate_resp.status_code not in (401, 403), (
                f"auth should pass before logout, got {evaluate_resp.status_code}"
            )

            result = await client.post(
                "/auth/logout", headers=headers(app_token), json={"request_id": new_id("req")}
            )
            assert result.status_code == 200, result.text

            # After logout: every endpoint (stub or real) must return 401.
            for method, path, token, service in probes:
                response = await client.request(method, path, headers=headers(token, service))
                assert response.status_code == 401, response.text
                assert response.json()["error"]["code"] == "UNAUTHENTICATED"

            evaluate_after = await client.post(
                "/plans/evaluate",
                headers=headers(assistant, "a-test"),
                json={"request_id": new_id("req"), "plan_draft": {}},
            )
            assert evaluate_after.status_code == 401, evaluate_after.text
            assert evaluate_after.json()["error"]["code"] == "UNAUTHENTICATED"

    asyncio.run(run())


def test_real_s1_runtime_loads_secrets_once_and_enforces_persistent_jti(
    gateway_db, monkeypatch, tmp_path
):
    from datetime import UTC, datetime, timedelta

    import httpx
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from services.gateway.app.auth.tokens import APP_AUDIENCE, issue_assistant, sign_jwt, verify_jwt
    from services.gateway.app.config import from_env
    from services.gateway.app.l3.client import L3Client
    from services.gateway.app.l3.fake import FakeL3
    from services.gateway.app.main import build_internal_app, build_public_app
    from services.gateway.app.runtime import gateway_runtime
    from tests.gateway.w1.db.support import ENV
    from tests.gateway.w1.fixtures import bank_state

    key = Ed25519PrivateKey.generate()
    private_path, public_path, pins_path = (
        tmp_path / name for name in ("private.pem", "public.pem", "pins")
    )
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
    pins_path.write_text(json.dumps({"cus_zs": "123456"}))
    pins_path.chmod(0o600)
    monkeypatch.delenv("DEMO_PIN_HASHES", raising=False)
    monkeypatch.setenv("DEMO_PINS_FILE", str(pins_path))
    configured_hashes.cache_clear()
    settings = from_env(
        {
            **ENV,
            "JWT_PRIVATE_KEY_PATH": str(private_path),
            "JWT_PUBLIC_KEY_PATH": str(public_path),
            "DEMO_PINS_FILE": str(pins_path),
            "GATEWAY_DATABASE_URL": gateway_db.runtime_url,
            "GATEWAY_SERVICE_TOKEN": "l3-token",
        }
    )
    fake = FakeL3(token="l3-token")
    fake.set_snapshot(bank_state())

    def client_factory(url, token):
        return L3Client(url, token, transport=fake.transport())

    async def run():
        # No customer-lock substitute is installed here. Login, shared middleware,
        # DB revocation and G20 reads require no submitted job or L3 fund write.
        async with gateway_runtime(settings, l3_factory=client_factory) as runtime:
            public = build_public_app(settings, env=ENV, runtime=runtime)
            internal = build_internal_app(settings, env=ENV, runtime=runtime)
            assert public.state.db is internal.state.db is runtime.db
            assert public.state.l3 is internal.state.l3 is runtime.l3
            snapshot = await runtime.l3.snapshot(
                {"customer_id": "cus_zs", "steps": [], "event_id": None}
            )
            assert snapshot["accounts"]["acc_zs"]["customer_id"] == "cus_zs"
            private_path.unlink()
            pins_path.unlink()
            monkeypatch.setenv("DEMO_PIN_HASHES", "invalid request-time environment")
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(public), base_url="http://gateway"
            ) as client:
                token = await login(client)
                claims = verify_jwt(token, runtime.jwt_public_key, expected_aud=APP_AUDIENCE)
                now = datetime.now(UTC)
                sid = new_id("ses")
                async with runtime.db.begin() as conn:
                    await conn.execute(
                        t.sessions.insert().values(
                            id=sid, customer_id="cus_zs", created_at_wall=now
                        )
                    )
                    assistant, _ = await issue_assistant(
                        conn, runtime.jwt_private_key, "cus_zs", sid, [], now
                    )
                response = await client.get(
                    f"/sessions/{sid}/utterances", headers=headers(assistant, "a-test")
                )
                assert response.status_code == 200 and response.json() == {"items": []}
                # Valid signature alone cannot bypass the shared persistent checker.
                unregistered = sign_jwt(
                    runtime.jwt_private_key,
                    sub="cus_zs",
                    aud=APP_AUDIENCE,
                    scope=["app", "approve"],
                    jti=new_id("jti"),
                )
                response = await client.get("/audit", headers=headers(unregistered))
                assert response.status_code == 401
                async with runtime.db.begin() as conn:
                    await conn.execute(
                        t.auth_sessions.update()
                        .where(t.auth_sessions.c.jti == claims.jti)
                        .values(expires_at_wall=now - timedelta(seconds=1))
                    )
                response = await client.get("/audit", headers=headers(token))
                assert (
                    response.status_code == 401
                    and response.json()["error"]["code"] == "TOKEN_EXPIRED"
                )
                async with runtime.db.begin() as conn:
                    await conn.execute(
                        t.sessions.update()
                        .where(t.sessions.c.id == sid)
                        .values(revoked_at_wall=now)
                    )
                response = await client.get(
                    f"/sessions/{sid}/utterances", headers=headers(assistant, "a-test")
                )
                assert response.status_code == 401
        with pytest.raises(RuntimeError, match="closed"):
            await runtime.l3.snapshot({"customer_id": "cus_zs", "steps": [], "event_id": None})

    try:
        asyncio.run(run())
    finally:
        configured_hashes.cache_clear()
