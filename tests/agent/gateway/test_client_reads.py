"""GatewayClient read paths (G20/G21/G2) against a scripted transport (C1c1 Task 1)."""

from __future__ import annotations

import pytest

from services.agent.app.gateway import GatewayClient
from services.agent.app.gateway.errors import GatewayProtocolError
from services.agent.app.gateway.wire import GatewayClarification, GatewayReadEnvelope
from services.agent.app.graph.state import AgentContext
from tests.agent.gateway.transport import ScriptedGateway, json_response

SERVICE_TOKEN = "TEST_AGENT_SERVICE_TOKEN"
SECRET = "TEST_ONLY_ASSISTANT_TOKEN_SECRET"


def ctx(customer="cus_a", session="ses_a", token=SECRET) -> AgentContext:
    return AgentContext(assistant_token=token, customer_id=customer, session_id=session)


def utterance(id: str, kind: str = "typed", text: str | None = "给王五转 800") -> dict:
    return {
        "id": id,
        "kind": kind,
        "text": text,
        "scope": {
            "action_kinds": [],
            "from_account_id": None,
            "transfers": [],
            "period": None,
            "channel": None,
            "fields": {},
        },
        "parse_status": "SCOPED",
        "echoes_untrusted": False,
        "parent_ids": [],
        "clarification_id": None,
        "created_at_wall": "2026-10-24T00:00:00Z",
        "expires_at_wall": "2026-10-24T00:10:00Z",
    }


def test_get_utterances_sends_correct_headers_path_and_params():
    transport = ScriptedGateway(
        [json_response(200, {"items": [utterance("utt_a"), utterance("utt_b", "hint", None)]})]
    )
    client = GatewayClient("http://gw", SERVICE_TOKEN, http_client=transport.client)
    result = client.get_utterances(("utt_a", "utt_b"), ctx())
    assert [u.id for u in result] == ["utt_a", "utt_b"]
    assert result[1].text is None and result[1].kind == "hint"
    request = transport.requests[0]
    assert request["method"] == "GET"
    assert request["url"].split("?", 1)[0] == "http://gw/sessions/ses_a/utterances"
    query = request["url"].split("?", 1)[1]
    assert "ids=" in query and "utt_a" in query and "utt_b" in query
    assert request["headers"]["x-service-token"] == SERVICE_TOKEN
    assert request["headers"]["authorization"] == f"Bearer {SECRET}"


def test_two_contexts_on_same_client_do_not_mix_jwt():
    transport = ScriptedGateway(
        [
            json_response(200, {"items": [utterance("utt_a")]}),
            json_response(200, {"items": [utterance("utt_a")]}),
        ]
    )
    client = GatewayClient("http://gw", SERVICE_TOKEN, http_client=transport.client)
    client.get_utterances(("utt_a",), ctx(token="TOKEN_ONE"))
    client.get_utterances(("utt_a",), ctx(token="TOKEN_TWO"))
    assert transport.requests[0]["headers"]["authorization"] == "Bearer TOKEN_ONE"
    assert transport.requests[1]["headers"]["authorization"] == "Bearer TOKEN_TWO"


def test_missing_credential_or_session_rejects_without_http():
    transport = ScriptedGateway([])
    client = GatewayClient("http://gw", SERVICE_TOKEN, http_client=transport.client)
    with pytest.raises(ValueError):
        client.get_utterances(("utt_a",), ctx(token=""))
    with pytest.raises(ValueError):
        client.read_tool("get_accounts", {"history_days": 30}, ctx(session=""))
    assert transport.requests == []


def test_unknown_or_write_tool_rejects_without_http():
    transport = ScriptedGateway([])
    client = GatewayClient("http://gw", SERVICE_TOKEN, http_client=transport.client)
    with pytest.raises(ValueError):
        client.read_tool("not_a_tool", {}, ctx())
    with pytest.raises(ValueError):
        client.read_tool("transfer", {}, ctx())
    assert transport.requests == []


def test_get_utterances_empty_duplicate_or_too_many_ids_reject():
    transport = ScriptedGateway([])
    client = GatewayClient("http://gw", SERVICE_TOKEN, http_client=transport.client)
    with pytest.raises(ValueError):
        client.get_utterances((), ctx())
    with pytest.raises(ValueError):
        client.get_utterances(("utt_a", "utt_a"), ctx())
    with pytest.raises(ValueError):
        client.get_utterances(tuple(f"utt_{i}" for i in range(101)), ctx())
    assert transport.requests == []


def test_get_utterances_response_id_mismatch_is_protocol_error():
    transport = ScriptedGateway([json_response(200, {"items": [utterance("utt_other")]})])
    client = GatewayClient("http://gw", SERVICE_TOKEN, http_client=transport.client)
    with pytest.raises(GatewayProtocolError):
        client.get_utterances(("utt_a",), ctx())


def test_get_utterances_duplicate_response_id_is_protocol_error():
    transport = ScriptedGateway(
        [json_response(200, {"items": [utterance("utt_a"), utterance("utt_a")]})]
    )
    client = GatewayClient("http://gw", SERVICE_TOKEN, http_client=transport.client)
    with pytest.raises(GatewayProtocolError):
        client.get_utterances(("utt_a",), ctx())


def test_get_clarification_null_and_full():
    full = {
        "id": "clr_1",
        "kind": "SLOTS",
        "parent_utterance_ids": ["utt_a"],
        "required_fields": ["payee_id"],
        "prompt_template_id": "select_missing_slot",
        "partial_scope": {},
        "candidates": [
            {"field": "payee_id", "entity_id": "pye_b", "label": "王五", "source_ref": "src_1"}
        ],
        "expires_at_wall": "2026-10-24T00:10:00Z",
    }
    transport = ScriptedGateway(
        [json_response(200, {"clarification": None}), json_response(200, {"clarification": full})]
    )
    client = GatewayClient("http://gw", SERVICE_TOKEN, http_client=transport.client)
    assert client.get_clarification(ctx()) is None
    clarification = client.get_clarification(ctx())
    assert isinstance(clarification, GatewayClarification)
    assert clarification.id == "clr_1"


def test_read_tool_requires_contract_fields_without_guessing():
    transport = ScriptedGateway([])
    client = GatewayClient("http://gw", SERVICE_TOKEN, http_client=transport.client)
    with pytest.raises(ValueError):
        client.read_tool("get_accounts", {}, ctx())  # history_days must not be invented
    with pytest.raises(ValueError):
        client.read_tool("get_accounts", {"history_days": 1.5}, ctx())  # non-integer amount
    assert transport.requests == []


def test_read_tool_roundtrip_and_result_validation():
    data = {
        "accounts": [],
        "daily_outflow_cents": 0,
        "daily_outflow_limit_cents": 100000,
        "remaining_daily_outflow_cents": 100000,
        "balance_history": [],
    }
    transport = ScriptedGateway(
        [
            json_response(
                200,
                {
                    "data": data,
                    "source_ref": "src_1",
                    "untrusted_paths": [],
                    "versions": {"cus_a": 1},
                    "observed_at": "2026-10-24T00:00:00Z",
                },
            )
        ]
    )
    client = GatewayClient("http://gw", SERVICE_TOKEN, http_client=transport.client)
    envelope = client.read_tool("get_accounts", {"history_days": 30}, ctx())
    assert isinstance(envelope, GatewayReadEnvelope)
    assert envelope.data == data
    assert envelope.source_ref == "src_1"
    request = transport.requests[0]
    assert request["method"] == "POST"
    assert request["url"] == "http://gw/tools/read/get_accounts"
    assert request["body"] == {"session_id": "ses_a", "args": {"history_days": 30}}


def test_read_tool_malformed_result_data_is_protocol_error():
    transport = ScriptedGateway(
        [
            json_response(
                200,
                {
                    "data": {"accounts": []},  # missing daily_outflow_cents etc.
                    "source_ref": "src_1",
                    "versions": {"cus_a": 1},
                    "observed_at": "2026-10-24T00:00:00Z",
                },
            )
        ]
    )
    client = GatewayClient("http://gw", SERVICE_TOKEN, http_client=transport.client)
    with pytest.raises(GatewayProtocolError):
        client.read_tool("get_accounts", {"history_days": 30}, ctx())
