"""W1 consumes installed S1 resources and never reloads request-time defaults."""

import asyncio
from datetime import datetime

import httpx
import pytest
from safebank_contracts.codes import Code
from starlette.requests import Request

from services.gateway.app.config import from_env
from services.gateway.app.errors import GatewayError
from services.gateway.app.main import build_public_app
from services.gateway.app.utterance.runtime import private_key, wall_now


def request_for(app):
    return Request({"type": "http", "app": app})


def test_signing_key_is_not_reloaded_when_central_resource_is_missing(tmp_path, monkeypatch):
    key = tmp_path / "request-key.pem"
    key.write_bytes(b"request-file-is-not-a-central-resource")
    monkeypatch.setenv("JWT_PRIVATE_KEY_PATH", str(key))
    app = build_public_app(from_env({}), env={})
    with pytest.raises(GatewayError) as exc:
        private_key(request_for(app))
    assert exc.value.code is Code.DEPENDENCY_UNAVAILABLE


@pytest.mark.parametrize("clock", ["missing", None, lambda: datetime(2026, 10, 23)])
def test_w1_clock_must_be_installed_and_aware(clock):
    app = build_public_app(from_env({}), env={})
    if clock != "missing":
        app.state.wall_now = clock
    with pytest.raises(GatewayError) as exc:
        wall_now(request_for(app))
    assert exc.value.code is Code.DEPENDENCY_UNAVAILABLE


def test_customer_labels_do_not_fall_back_to_process_environment(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    app = build_public_app(from_env({}), env={"FRONTEND_SERVICE_TOKEN": "f"})

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://gateway"
        ) as client:
            response = await client.get("/auth/demo-customers", headers={"X-Service-Token": "f"})
            # from_env({}) ignores os.environ, so settings.demo_mode is False and
            # S1f mounts no G22 route; process DEMO_MODE must not revive it.
            assert response.status_code == 404, response.text
            assert response.json()["error"]["code"] == "NOT_FOUND"

    asyncio.run(run())
