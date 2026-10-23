"""GatewayClient error boundary: safe categories, no credential leakage (C1c1 Task 2)."""

from __future__ import annotations

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
    decision = {
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
    transport = ScriptedGateway(
        [error_response(409, "POLICY_CHANGED", details={"decision": decision})]
    )
    with pytest.raises(GatewayResponseError) as info:
        client_with(transport).get_utterances(("utt_a",), ctx())
    assert isinstance(info.value.decision, GatewayDecision)
    assert info.value.decision.revision == 2


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
