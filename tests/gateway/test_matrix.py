from services.gateway.app.auth.matrix import coverage_errors, lookup
from services.gateway.app.config import from_env
from services.gateway.app.main import _routes, build_internal_app, build_public_app


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
