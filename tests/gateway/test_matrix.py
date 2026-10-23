from services.gateway.app.auth.matrix import lookup


def test_health_is_open():
    spec = lookup("GET", "/health")
    assert spec is not None
    assert spec.service is None
    assert spec.jwt_aud is None


def test_scheduled_authorize_requires_scheduler_token():
    spec = lookup("POST", "/internal/scheduled/authorize")
    assert spec is not None
    assert spec.service == "S"
    assert spec.jwt_aud is None


def test_unregistered_route_lookup_is_none():
    assert lookup("POST", "/plans/evaluate") is None
    assert lookup("GET", "/unknown") is None
