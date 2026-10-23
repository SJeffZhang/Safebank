from fastapi.testclient import TestClient

from scripts.health_app import create_app


def test_public_and_private_route_separation(monkeypatch):
    monkeypatch.setenv("SCHEDULER_SERVICE_TOKEN", "test-scheduler")
    public = TestClient(create_app("gateway"))
    internal = TestClient(create_app("gateway", internal=True))
    assert public.get("/health").json()["stage"] == "P0-skeleton"
    assert public.get("/internal/health").status_code == 404
    assert internal.get("/health").status_code == 404
    assert internal.get("/internal/health").status_code == 401
    assert internal.get("/internal/health", headers={"X-Service-Token": "wrong"}).status_code == 401
    assert (
        internal.get("/internal/health", headers={"X-Service-Token": "test-scheduler"}).status_code
        == 200
    )


def test_backend_fails_closed_when_token_missing(monkeypatch):
    monkeypatch.delenv("GATEWAY_SERVICE_TOKEN", raising=False)
    client = TestClient(create_app("bank-backend"))
    assert client.get("/health").status_code == 401
    monkeypatch.setenv("GATEWAY_SERVICE_TOKEN", "test-gateway")
    assert client.get("/health", headers={"X-Service-Token": "test-gateway"}).status_code == 200
    assert client.post("/internal/plans/execute", json={}).status_code == 404
