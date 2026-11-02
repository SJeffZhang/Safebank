"""Real PG checkpoint+SSE: exhausted model structures never expose internals."""

import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from services.agent.app.main import _install_test_ports
from tests.agent.test_nov1_understanding_errors import TEXT, invalid_error
from tests.agent.test_service import (
    _auth_headers,
    _make_test_ports,
    _parse_sse_events,
    _setup_test_app,
    make_token,
)
from tests.agent.test_service import ed25519_keys as _ed25519_keys

pytestmark = pytest.mark.db
ed25519_keys = _ed25519_keys


@pytest.mark.parametrize("kind", ["structure", "unexpected"])
def test_actual_sse_error_is_safe_and_has_zero_downstream_calls(agent_pg, ed25519_keys, kind):
    app = _setup_test_app(agent_pg, ed25519_keys)
    calls = []

    def fail(state, context):
        calls.append("understand")
        if kind == "structure":
            raise invalid_error()
        raise RuntimeError("CANARY_PRIVATE_input_value stacktrace")

    def downstream(*args):
        calls.append("downstream")
        raise AssertionError("must not execute downstream")

    _install_test_ports(
        app,
        replace(
            _make_test_ports(),
            understand=fail,
            gather=downstream,
            evaluate=downstream,
            execute=downstream,
        ),
    )
    private, _ = ed25519_keys
    sid = "ses_safe_error_" + kind
    token = make_token(private, customer="cus_a", session=sid)
    with TestClient(app) as client:
        headers = _auth_headers(token)
        created = client.post("/threads", json={"thread_id": sid}, headers=headers)
        assert created.status_code in (200, 201)
        response = client.post(
            f"/threads/{sid}/runs/stream",
            json={"input": {"utterances": [{"id": "utt_error", "kind": "typed", "text": TEXT}]}},
            headers=headers,
        )
        assert response.status_code == 200
        errors = [
            json.loads(data) for event, data in _parse_sse_events(response.text) if event == "error"
        ]
        assert len(errors) == 1
        assert errors[0]["code"] == (
            "MODEL_OUTPUT_INVALID" if kind == "structure" else "AGENT_REQUEST_FAILED"
        )
        assert all(
            s not in response.text
            for s in [
                "CANARY_PRIVATE",
                "ValidationError",
                "input_value",
                "errors.pydantic.dev",
                "stacktrace",
            ]
        )
        assert calls == ["understand"]
