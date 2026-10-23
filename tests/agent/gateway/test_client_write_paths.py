"""GatewayClient evaluate/execute paths (G4/G8) and status/plan binding (C1c1 Task 2)."""

from __future__ import annotations

import pytest
from safebank_contracts.plan import ExecutionResult, PlanDraft

from services.agent.app.gateway import GatewayClient
from services.agent.app.gateway.errors import (
    GatewayProtocolError,
    GatewayResponseError,
)
from services.agent.app.gateway.wire import GatewayDecision, UnknownExecution
from services.agent.app.graph.state import AgentContext
from tests.agent.gateway.transport import ScriptedGateway, error_response, json_response

SERVICE_TOKEN = "TEST_AGENT_SERVICE_TOKEN"
SECRET = "TEST_ONLY_ASSISTANT_TOKEN_SECRET"


def ctx(customer="cus_a", session="ses_a", token=SECRET) -> AgentContext:
    return AgentContext(assistant_token=token, customer_id=customer, session_id=session)


def draft(session="ses_a", request="req_a") -> PlanDraft:
    return PlanDraft(
        request_id=request,
        session_id=session,
        utterance_ids=["utt_a"],
        intents=["transfer"],
        steps=[
            {
                "step_id": "s1",
                "tool": "transfer",
                "args": {
                    "from_account_id": "acc_a",
                    "payee_id": "pye_b",
                    "to_account_id": None,
                    "amount_cents": 80000,
                    "memo": None,
                },
                "depends_on": [],
                "arg_sources": [],
            }
        ],
        rationale="test",
    )


def decision(**overrides) -> dict:
    body = {
        "plan_id": "pln_a",
        "evaluation_id": "evl_a",
        "revision": 1,
        "decision": "ALLOW",
        "risk": "GREEN",
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
        "versions": {"cus_a": 1},
        "clock_version": 1,
        "policy_version": "sha256:abc",
        "shadow_b1": None,
    }
    body.update(overrides)
    return body


def execution(plan_id="pln_a") -> dict:
    return {
        "plan_id": plan_id,
        "status": "EXECUTED",
        "steps": [],
        "versions": {},
        "committed_at": "2026-10-24T00:00:00Z",
        "error_code": None,
        "replayed": False,
    }


def test_evaluate_posts_plan_draft_without_extra_origin_auth_fields():
    transport = ScriptedGateway([json_response(200, decision(plan_id="pln_new"))])
    client = GatewayClient("http://gw", SERVICE_TOKEN, http_client=transport.client)
    result = client.evaluate(draft(), ctx())
    assert isinstance(result, GatewayDecision)
    assert result.plan_id == "pln_new"
    request = transport.requests[0]
    assert request["method"] == "POST"
    assert request["url"] == "http://gw/plans/evaluate"
    assert request["body"]["request_id"] == "req_a"
    assert request["body"]["session_id"] == "ses_a"
    assert request["body"]["utterance_ids"] == ["utt_a"]
    assert "origin" not in request["body"]
    assert "auth" not in request["body"]


def test_evaluate_session_mismatch_rejects_without_http():
    transport = ScriptedGateway([])
    client = GatewayClient("http://gw", SERVICE_TOKEN, http_client=transport.client)
    with pytest.raises(ValueError):
        client.evaluate(draft(session="ses_other"), ctx())
    assert transport.requests == []


def test_evaluate_all_four_decisions_parse():
    transport = ScriptedGateway(
        [
            json_response(200, decision(decision="ALLOW", risk="GREEN")),
            json_response(
                200, decision(decision="REQUIRE_APPROVAL", risk="YELLOW", approval_id="apv_a")
            ),
            json_response(200, decision(decision="DENY", risk="RED")),
            json_response(
                200,
                decision(
                    decision="REPLAN",
                    risk="YELLOW",
                    findings=[
                        {
                            "code": "ENTITY_UNSEEN",
                            "effect": "REPLAN",
                            "resolver": "agent",
                            "evidence_refs": ["list_payees"],
                        }
                    ],
                ),
            ),
        ]
    )
    client = GatewayClient("http://gw", SERVICE_TOKEN, http_client=transport.client)
    assert client.evaluate(draft(), ctx()).decision == "ALLOW"
    assert client.evaluate(draft(), ctx()).approval_id == "apv_a"
    assert client.evaluate(draft(), ctx()).decision == "DENY"
    assert client.evaluate(draft(), ctx()).findings[0].resolver == "agent"


def test_execute_posts_only_request_id():
    transport = ScriptedGateway([json_response(200, execution())])
    client = GatewayClient("http://gw", SERVICE_TOKEN, http_client=transport.client)
    result = client.execute("pln_a", "req_x", ctx())
    assert isinstance(result, ExecutionResult)
    request = transport.requests[0]
    assert request["method"] == "POST"
    assert request["url"] == "http://gw/plans/pln_a/execute"
    assert request["body"] == {"request_id": "req_x"}


def test_execute_result_plan_id_mismatch_is_protocol_error():
    transport = ScriptedGateway([json_response(200, execution(plan_id="pln_other"))])
    client = GatewayClient("http://gw", SERVICE_TOKEN, http_client=transport.client)
    with pytest.raises(GatewayProtocolError):
        client.execute("pln_a", "req_x", ctx())


def test_execute_202_unknown_single_http_and_plan_binding():
    transport = ScriptedGateway([json_response(202, {"plan_id": "pln_a", "status": "UNKNOWN"})])
    client = GatewayClient("http://gw", SERVICE_TOKEN, http_client=transport.client)
    result = client.execute("pln_a", "req_x", ctx())
    assert isinstance(result, UnknownExecution)
    assert len(transport.requests) == 1  # no follow-up GET/POST polling


def test_execute_202_unknown_wrong_plan_rejects():
    transport = ScriptedGateway([json_response(202, {"plan_id": "pln_other", "status": "UNKNOWN"})])
    client = GatewayClient("http://gw", SERVICE_TOKEN, http_client=transport.client)
    with pytest.raises(GatewayProtocolError):
        client.execute("pln_a", "req_x", ctx())


def test_execute_409_state_changed_preserves_decision_and_binds_plan():
    transport = ScriptedGateway(
        [
            error_response(
                409,
                "STATE_CHANGED",
                details={
                    "decision": decision(
                        plan_id="pln_a", decision="REQUIRE_APPROVAL", approval_id="apv_new"
                    )
                },
            )
        ]
    )
    client = GatewayClient("http://gw", SERVICE_TOKEN, http_client=transport.client)
    with pytest.raises(GatewayResponseError) as info:
        client.execute("pln_a", "req_x", ctx())
    error = info.value
    assert error.status == 409
    assert error.code.value == "STATE_CHANGED"
    assert isinstance(error.decision, GatewayDecision)
    assert error.decision.approval_id == "apv_new"


def test_execute_409_state_changed_wrong_plan_rejects():
    transport = ScriptedGateway(
        [error_response(409, "STATE_CHANGED", details={"decision": decision(plan_id="pln_other")})]
    )
    client = GatewayClient("http://gw", SERVICE_TOKEN, http_client=transport.client)
    with pytest.raises(GatewayProtocolError):
        client.execute("pln_a", "req_x", ctx())


@pytest.mark.parametrize(
    "status,code",
    [
        (422, "CONTRACT_INVALID"),
        (429, "RATE_LIMITED"),
        (401, "UNAUTHENTICATED"),
        (403, "FORBIDDEN"),
        (404, "NOT_FOUND"),
        (409, "APPROVAL_REJECTED"),
        (503, "DEPENDENCY_UNAVAILABLE"),
    ],
)
def test_error_status_and_code_preserved(status, code):
    transport = ScriptedGateway([error_response(status, code)])
    client = GatewayClient("http://gw", SERVICE_TOKEN, http_client=transport.client)
    with pytest.raises(GatewayResponseError) as info:
        client.evaluate(draft(), ctx())
    assert info.value.status == status
    assert info.value.code.value == code
