"""GatewayClient error boundary: safe categories, no credential leakage (C1c1 Task 2)."""

from __future__ import annotations

import math
import traceback

import httpx
import pytest

from services.agent.app.gateway import GatewayClient
from services.agent.app.gateway.errors import (
    GatewayProtocolError,
    GatewayResponseError,
    GatewayTransportError,
)
from services.agent.app.gateway.wire import GatewayDecision
from services.agent.app.graph.state import AgentContext
from tests.agent.gateway.transport import ScriptedGateway, error_response, json_response

SERVICE_TOKEN = "TEST_AGENT_SERVICE_TOKEN"
SECRET = "TEST_ONLY_ASSISTANT_TOKEN_SECRET"


def ctx() -> AgentContext:
    return AgentContext(assistant_token=SECRET, customer_id="cus_a", session_id="ses_a")


def client_with(transport) -> GatewayClient:
    return GatewayClient("http://gw", SERVICE_TOKEN, http_client=transport.client)


def full_decision(**overrides) -> dict:
    body = {
        "plan_id": "pln_a",
        "evaluation_id": "evl_a",
        "revision": 2,
        "decision": "DENY",
        "risk": "RED",
        "approval_id": None,
        "findings": [],
        "replan_hints": [],
        "preflight": {
            "executable": True,
            "steps": [],
            "balances": [],
            "holdings": [],
            "scheduled_reserve_cents": 0,
        },
        "versions": {"cus_a": 2},
        "clock_version": 2,
        "policy_version": "sha256:abc",
        "shadow_b1": None,
    }
    body.update(overrides)
    return body


def test_non_json_body_is_protocol_error_without_leak():
    def garbage(request):
        return httpx.Response(500, text=f"boom {SECRET} is not json", request=request)

    transport = ScriptedGateway([garbage])
    with pytest.raises(GatewayProtocolError) as info:
        client_with(transport).get_utterances(("utt_a",), ctx())
    assert SECRET not in str(info.value)
    assert info.value.status == 500


def test_redirect_is_not_followed_and_is_protocol_error():
    transport = ScriptedGateway([httpx.Response(302, headers={"location": f"/{SECRET}"})])
    with pytest.raises(GatewayProtocolError) as info:
        client_with(transport).get_utterances(("utt_a",), ctx())
    assert info.value.status == 302


def test_timeout_is_transport_error():
    def timeout(request):
        raise httpx.ReadTimeout("timed out", request=request)

    transport = ScriptedGateway([timeout])
    with pytest.raises(GatewayTransportError) as info:
        client_with(transport).get_utterances(("utt_a",), ctx())
    assert SECRET not in str(info.value)


def test_connection_error_is_transport_error():
    def connect(request):
        raise httpx.ConnectError(f"cannot reach {SECRET}", request=request)

    transport = ScriptedGateway([connect])
    with pytest.raises(GatewayTransportError):
        client_with(transport).get_utterances(("utt_a",), ctx())


def test_server_message_containing_secret_is_not_echoed():
    transport = ScriptedGateway(
        [error_response(500, "DEPENDENCY_UNAVAILABLE", message=f"secret {SECRET} leaked")]
    )
    with pytest.raises(GatewayResponseError) as info:
        client_with(transport).get_utterances(("utt_a",), ctx())
    assert SECRET not in str(info.value)
    assert SECRET not in repr(info.value)
    assert info.value.status == 500
    assert info.value.code.value == "DEPENDENCY_UNAVAILABLE"


def test_unknown_error_code_is_protocol_error():
    transport = ScriptedGateway([error_response(500, "NOT_A_REAL_CODE")])
    with pytest.raises(GatewayProtocolError):
        client_with(transport).get_utterances(("utt_a",), ctx())


def test_malformed_decision_in_details_is_protocol_error():
    transport = ScriptedGateway(
        [error_response(409, "STATE_CHANGED", details={"decision": {"plan_id": "pln_a"}})]
    )
    with pytest.raises(GatewayProtocolError):
        client_with(transport).get_utterances(("utt_a",), ctx())


def test_well_formed_decision_in_details_is_preserved():
    transport = ScriptedGateway(
        [error_response(409, "POLICY_CHANGED", details={"decision": full_decision()})]
    )
    with pytest.raises(GatewayResponseError) as info:
        client_with(transport).get_utterances(("utt_a",), ctx())
    assert isinstance(info.value.decision, GatewayDecision)
    assert info.value.decision.revision == 2


def test_error_details_only_keep_model_dumped_decision():
    transport = ScriptedGateway(
        [
            error_response(
                409, "STATE_CHANGED", details={"decision": full_decision(), "extra": SECRET}
            )
        ]
    )
    with pytest.raises(GatewayResponseError) as info:
        client_with(transport).get_utterances(("utt_a",), ctx())
    error = info.value
    assert set(error.details.keys()) == {"decision"}
    assert SECRET not in str(error.details)
    assert error.decision.plan_id == "pln_a"


def test_credentials_in_error_message_details_and_trace_are_dropped():
    transport = ScriptedGateway(
        [
            httpx.Response(
                500,
                json={
                    "error": {
                        "code": "DEPENDENCY_UNAVAILABLE",
                        "message": f"boom {SERVICE_TOKEN} {SECRET}",
                        "details": {"extra": f"{SERVICE_TOKEN} {SECRET}"},
                        "trace_id": f"{SERVICE_TOKEN} {SECRET}",
                    }
                },
            )
        ]
    )
    with pytest.raises(GatewayResponseError) as info:
        client_with(transport).get_utterances(("utt_a",), ctx())
    error = info.value
    assert SERVICE_TOKEN not in str(error)
    assert SECRET not in str(error)
    assert SERVICE_TOKEN not in repr(error)
    assert error.details == {}
    assert error.trace_id == ""


def test_trace_id_unsafe_shape_is_omitted_but_safe_shape_preserved():
    unsafe = ScriptedGateway(
        [error_response(500, "DEPENDENCY_UNAVAILABLE", trace_id="trc <script>")]
    )
    with pytest.raises(GatewayResponseError) as info:
        client_with(unsafe).get_utterances(("utt_a",), ctx())
    assert info.value.trace_id == ""

    safe = ScriptedGateway([error_response(500, "DEPENDENCY_UNAVAILABLE", trace_id="trc_01JABC")])
    with pytest.raises(GatewayResponseError) as info:
        client_with(safe).get_utterances(("utt_a",), ctx())
    assert info.value.trace_id == "trc_01JABC"


def test_trace_id_equal_to_service_token_is_omitted():
    transport = ScriptedGateway([error_response(429, "RATE_LIMITED", trace_id=SERVICE_TOKEN)])
    with pytest.raises(GatewayResponseError) as info:
        client_with(transport).get_utterances(("utt_a",), ctx())
    assert info.value.trace_id == ""
    assert SERVICE_TOKEN not in str(info.value)
    assert SERVICE_TOKEN not in repr(info.value)


def test_trace_id_embedding_credentials_is_omitted():
    embedded_service = f"pre{SERVICE_TOKEN}post"
    embedded_jwt = f"xx{SECRET}yy"
    transport = ScriptedGateway(
        [
            error_response(429, "RATE_LIMITED", trace_id=embedded_service),
            error_response(429, "RATE_LIMITED", trace_id=embedded_jwt),
        ]
    )
    client = client_with(transport)
    with pytest.raises(GatewayResponseError) as info:
        client.get_utterances(("utt_a",), ctx())
    assert info.value.trace_id == ""
    with pytest.raises(GatewayResponseError) as info:
        client.get_utterances(("utt_a",), ctx())
    assert info.value.trace_id == ""


def test_invalid_args_do_not_leak_raw_input_in_output():
    transport = ScriptedGateway([])
    client = client_with(transport)
    with pytest.raises(ValueError) as info:
        client.read_tool("get_accounts", {"history_days": 1.5, "memo_secret": "TOP_SECRET"}, ctx())
    error = info.value
    assert "TOP_SECRET" not in str(error)
    assert "TOP_SECRET" not in repr(error)
    # ``raise ... from None`` sets the cause to None so the raw Pydantic error is not
    # rendered in the chained traceback.  (``__context__`` still references the original
    # error internally — we do not assert on it; the security boundary is the rendered
    # output and the cause, not interpreter-internal attributes.)
    assert error.__cause__ is None
    rendered = "".join(traceback.format_exception_only(type(error), error))
    assert "TOP_SECRET" not in rendered
    assert transport.requests == []


def test_unknown_tool_does_not_echo_raw_input():
    transport = ScriptedGateway([])
    client = client_with(transport)
    with pytest.raises(ValueError) as info:
        client.read_tool("TOP_SECRET_TOOL", {}, ctx())
    assert "TOP_SECRET_TOOL" not in str(info.value)
    assert "TOP_SECRET_TOOL" not in repr(info.value)
    assert transport.requests == []


def test_injected_client_cross_host_redirect_is_not_followed():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://evil.example/x"})

    injected = httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=True, timeout=None
    )
    client = GatewayClient("http://gw", SERVICE_TOKEN, http_client=injected, timeout=5.0)
    with pytest.raises(GatewayProtocolError):
        client.get_utterances(("utt_a",), ctx())
    assert seen == ["http://gw/sessions/ses_a/utterances?ids=utt_a"]


def test_injected_client_timeout_is_enforced_via_request_extensions():
    data = {
        "accounts": [],
        "daily_outflow_cents": 0,
        "daily_outflow_limit_cents": 0,
        "remaining_daily_outflow_cents": 0,
        "balance_history": [],
    }
    transport = ScriptedGateway(
        [
            json_response(
                200,
                {
                    "data": data,
                    "source_ref": "src_1",
                    "versions": {},
                    "observed_at": "2026-10-24T00:00:00Z",
                },
            )
        ]
    )
    injected = httpx.Client(
        transport=httpx.MockTransport(transport), follow_redirects=True, timeout=None
    )
    client = GatewayClient("http://gw", SERVICE_TOKEN, http_client=injected, timeout=5.0)
    client.read_tool("get_accounts", {"history_days": 30}, ctx())
    timeout = transport.requests[0]["timeout"]
    # httpcore reads this extension with ``.get("pool")`` — it must be a dict of four
    # finite numbers, not a ``httpx.Timeout`` object.
    assert isinstance(timeout, dict)
    assert set(timeout.keys()) == {"connect", "read", "write", "pool"}
    for value in timeout.values():
        assert isinstance(value, float)
        assert math.isfinite(value)
        assert value == 5.0


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf"), float("-inf"), "not-a-number"])
def test_constructor_rejects_non_positive_or_non_finite_timeout(bad):
    with pytest.raises(ValueError):
        GatewayClient("http://gw", SERVICE_TOKEN, timeout=bad)


def test_error_category_hierarchy_for_routing():
    # C1c2 routes on these fixed categories; transport errors keep outcome-uncertainty
    # distinct from well-formed L2 errors and clean protocol failures.
    transport = ScriptedGateway([error_response(503, "DEPENDENCY_UNAVAILABLE")])
    with pytest.raises(GatewayResponseError) as info:
        client_with(transport).get_utterances(("utt_a",), ctx())
    assert info.value.category.value == "RESPONSE"


def test_unexpected_success_shape_is_protocol_error():
    transport = ScriptedGateway([json_response(200, {"unexpected": True})])
    with pytest.raises(GatewayProtocolError):
        client_with(transport).get_utterances(("utt_a",), ctx())


def test_error_response_is_logged_with_status_and_code_only(caplog):
    """Gateway errors are visible in the agent log without bodies or credentials."""
    body_text = "USER_TEXT_MUST_NOT_BE_LOGGED"
    transport = ScriptedGateway([error_response(422, "CONTRACT_INVALID", message=body_text)])
    with caplog.at_level("WARNING", logger="safebank.agent.gateway"):
        with pytest.raises(GatewayResponseError):
            client_with(transport).get_utterances(("utt_a",), ctx())
    logged = caplog.text
    assert "422" in logged and "CONTRACT_INVALID" in logged
    assert body_text not in logged
    assert SECRET not in logged and SERVICE_TOKEN not in logged
