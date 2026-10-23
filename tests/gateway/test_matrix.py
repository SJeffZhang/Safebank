import asyncio

import httpx
import pytest
from fastapi import APIRouter
from starlette.routing import Route

from services.gateway.app.api import auth
from services.gateway.app.auth.matrix import coverage_errors, lookup
from services.gateway.app.config import from_env
from services.gateway.app.errors import GatewayError
from services.gateway.app.main import (
    _public_auth_router,
    _routes,
    build_internal_app,
    build_public_app,
)

G22 = ("GET", "/auth/demo-customers")


def _settings(overrides=None):
    base = {"DEMO_MODE": "false", "EVAL_MODE": "false", "GATEWAY_PROFILE": "B2"}
    return from_env({**base, **(overrides or {})})


def _pairs(app):
    return {(method, path) for methods, path in _routes(app) for method in methods}


def test_open_health():
    assert lookup("GET", "/health") == ()


def test_parameterized_path_matches():
    entry = lookup("POST", "/plans/pln_123/execute")
    assert entry is not None
    assert any(s.service == "A" for s in entry)
    assert any(s.service == "F" for s in entry)


def test_multi_caller_entry_for_evaluate():
    entry = lookup("POST", "/plans/evaluate")
    kinds = {s.service for s in entry}
    assert kinds == {"A", "F"}


def test_unregistered_route_is_none():
    assert lookup("GET", "/nope") is None


def test_public_app_routes_are_all_in_matrix():
    app = build_public_app(from_env({"DEMO_MODE": "false", "EVAL_MODE": "false"}))
    assert coverage_errors(_routes(app), demo_mounted=False) == []


def test_demo_routes_mount_only_in_demo_mode():
    no_demo = build_public_app(from_env({"DEMO_MODE": "false", "EVAL_MODE": "false"}))
    paths = {path for _, path in _routes(no_demo)}
    assert not any(p.startswith("/demo/") for p in paths)

    demo = build_public_app(from_env({"DEMO_MODE": "true", "EVAL_MODE": "false"}))
    demo_paths = {path for _, path in _routes(demo)}
    assert any(p.startswith("/demo/") for p in demo_paths)


def test_internal_app_routes_are_all_in_matrix():
    app = build_internal_app(from_env({"DEMO_MODE": "false", "EVAL_MODE": "false"}))
    assert coverage_errors(_routes(app), demo_mounted=False, internal=True) == []


def test_missing_matrix_entry_is_detected():
    errors = coverage_errors([({"GET"}, "/not-in-matrix")], demo_mounted=False)
    assert any("route not in matrix" in e for e in errors)


def test_g22_absent_when_demo_and_eval_off():
    app = build_public_app(_settings({"DEMO_MODE": "false", "EVAL_MODE": "false"}))
    assert G22 not in _pairs(app)


def test_g22_present_when_demo_or_eval_on():
    for overrides in (
        {"DEMO_MODE": "true", "EVAL_MODE": "false"},
        {"DEMO_MODE": "false", "EVAL_MODE": "true"},
        {"DEMO_MODE": "true", "EVAL_MODE": "true"},
    ):
        app = build_public_app(_settings(overrides))
        assert G22 in _pairs(app)


def test_g22_http_404_when_off():
    async def run():
        app = build_public_app(_settings({"DEMO_MODE": "false", "EVAL_MODE": "false"}))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            resp = await client.get("/auth/demo-customers")
            assert resp.status_code == 404
            assert resp.json()["error"]["code"] == "NOT_FOUND"

    asyncio.run(run())


def test_g22_http_requires_f_service_when_on():
    async def run():
        settings = _settings({"DEMO_MODE": "true", "EVAL_MODE": "false"})
        app = build_public_app(settings, env={"FRONTEND_SERVICE_TOKEN": "frontend-token"})
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            no_token = await client.get("/auth/demo-customers")
            assert no_token.status_code == 401
            assert no_token.json()["error"]["code"] == "UNAUTHENTICATED"
            with_token = await client.get(
                "/auth/demo-customers", headers={"X-Service-Token": "frontend-token"}
            )
            assert with_token.status_code == 503
            assert with_token.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"

    asyncio.run(run())


def test_g22_build_order_does_not_pollute_global_router():
    off = build_public_app(_settings({"DEMO_MODE": "false", "EVAL_MODE": "false"}))
    on = build_public_app(_settings({"DEMO_MODE": "true", "EVAL_MODE": "false"}))
    off_again = build_public_app(_settings({"DEMO_MODE": "false", "EVAL_MODE": "false"}))
    on_again = build_public_app(_settings({"DEMO_MODE": "true", "EVAL_MODE": "false"}))

    assert G22 not in _pairs(off)
    assert G22 in _pairs(on)
    assert G22 not in _pairs(off_again)
    assert G22 in _pairs(on_again)
    # The W1-owned singleton auth.router must stay untouched.
    assert any(getattr(r, "path", "") == G22[1] for r in auth.router.routes)


def test_g22_coverage_rejects_mount_when_off():
    errors = coverage_errors([({"GET"}, "/auth/demo-customers")], demo_mounted=False)
    assert any("mounted while DEMO/EVAL off" in e for e in errors)


def test_g22_coverage_requires_presence_when_on():
    errors = coverage_errors([], demo_mounted=True)
    assert any("matrix entry has no route: GET /auth/demo-customers" in e for e in errors)


def test_g22_coverage_ok_when_on_and_present():
    app = build_public_app(_settings({"DEMO_MODE": "true", "EVAL_MODE": "false"}))
    assert coverage_errors(_routes(app), demo_mounted=True) == []


def test_g22_filter_matches_method_and_path(monkeypatch):
    router = APIRouter()
    router.post("/auth/demo-customers")(lambda: None)
    monkeypatch.setattr(auth, "router", router)
    out = _public_auth_router(demo_mounted=False)
    pairs = {(frozenset(r.methods), r.path) for r in out.routes}
    assert (frozenset({"POST"}), "/auth/demo-customers") in pairs


def test_g22_filter_fails_closed_on_non_api_route(monkeypatch):
    router = APIRouter()
    router.routes.append(Route("/auth/demo-customers", endpoint=lambda: None, methods=["GET"]))
    monkeypatch.setattr(auth, "router", router)
    with pytest.raises(GatewayError):
        _public_auth_router(demo_mounted=False)
