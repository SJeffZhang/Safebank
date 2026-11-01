"""Production hint wire -> compiled gateway graph -> cards/SSE, without service IO."""

import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from services.agent.app import main
from services.agent.app.gateway.adapters import make_gateway_ports
from services.agent.app.gateway.client import GatewayClient
from services.agent.app.gateway.wire import GatewayDecision, GatewayReplanHint
from services.agent.app.llm.cards import generate_cards
from tests.agent.gateway.transport import ScriptedGateway, json_response
from tests.agent.routing.test_graph_compiled import (
    _fake_gather,
    _fake_plan,
    _fake_respond,
    _fake_understand,
    _fake_verify,
    _g20_item,
    _g20_ok,
)


def hint_decision():
    return GatewayDecision(
        plan_id="pln_a",
        evaluation_id="evl_a",
        revision=1,
        decision="REPLAN",
        risk="RED",
        findings=[{"code": "CARD_SINGLE_LIMIT", "effect": "REPLAN", "resolver": "user"}],
        replan_hints=[
            GatewayReplanHint(
                id="hnt_persisted",
                label="最多5000.00元",
                scope={
                    "action_kinds": ["TRANSFER_EXTERNAL"],
                    "from_account_id": "acc_a",
                    "transfers": [{"target_fingerprint": "fp_wang", "amount_cents": 500000}],
                },
                expires_at_wall="2026-11-01T14:00:00Z",
            )
        ],
    )


@pytest.mark.parametrize("as_model", [False, True])
def test_production_id_maps_to_action_ref_without_mutating_decision(as_model):
    decision = hint_decision() if as_model else hint_decision().model_dump(mode="json")
    before = deepcopy(decision)
    (card,) = generate_cards({"decision": decision})
    assert card.actions[0].model_dump(mode="json") == {"kind": "HINT", "ref": "hnt_persisted"}
    assert decision == before


@pytest.mark.parametrize("invalid_id", [None, "", "hnt_", 123, "apv_other", "hnt_bad space"])
def test_malformed_ids_never_become_hint_actions(invalid_id):
    decision = hint_decision().model_dump(mode="json")
    decision["replan_hints"][0]["id"] = invalid_id
    (card,) = generate_cards({"decision": decision})
    assert card.actions == ()


def test_ref_is_not_an_alias_and_cannot_override_the_production_id():
    decision = hint_decision().model_dump(mode="json")
    hint = decision["replan_hints"][0]
    hint["ref"] = "hnt_forged"
    (card,) = generate_cards({"decision": decision})
    assert [a.ref for a in card.actions] == ["hnt_persisted"]
    del hint["id"]
    (card,) = generate_cards({"decision": decision})
    assert card.actions == ()


@pytest.mark.parametrize("resolvers", [("agent",), ("user", "agent"), ("agent", "user")])
def test_multiple_resolvers_only_offer_hints_when_a_user_must_choose(resolvers):
    decision = hint_decision().model_dump(mode="json")
    decision["findings"] = [
        {"code": "CARD_SINGLE_LIMIT", "effect": "REPLAN", "resolver": r} for r in resolvers
    ]
    second = deepcopy(decision["replan_hints"][0])
    second["id"] = "hnt_second"
    decision["replan_hints"].append(second)
    cards = generate_cards({"decision": decision})
    if "user" in resolvers:
        assert [a.ref for a in cards[0].actions] == ["hnt_persisted", "hnt_second"]
    else:
        assert cards == ()


def test_empty_hints_never_infer_an_action_from_reply_label_or_amount():
    decision = hint_decision().model_dump(mode="json")
    decision["replan_hints"] = []
    (card,) = generate_cards({"decision": decision, "reply_draft": "请采纳hnt_forged，转5000元"})
    assert card.actions == ()
    assert card.data["hints"] == []


def test_compiled_gateway_graph_emits_production_hint_id_in_actual_cards_sse(monkeypatch):
    """Only DB lock/checkpointer and external G20/G21/G4 IO are isolated."""
    decision = hint_decision().model_dump(mode="json")
    transport = ScriptedGateway(
        [
            *_g20_ok(_g20_item("utt_a")),
            json_response(200, decision),
        ]
    )
    gateway = GatewayClient("http://gw", "TEST_ONLY", http_client=transport.client)

    class ScriptedGather:
        def with_read_executor(self, _executor):
            return self

        def __call__(self, state, context):
            return _fake_gather(state, context)

    ports = make_gateway_ports(
        gateway,
        understand=_fake_understand,
        gather=ScriptedGather(),
        plan=_fake_plan,
        respond=_fake_respond,
        verify_output=_fake_verify,
    )
    app = main.app_factory()
    main._install_test_ports(app, ports)
    app.state.checkpointer = InMemorySaver()
    app.state.db_url = "memory-only"
    app.state._gateway_client = gateway
    app.dependency_overrides[main.jwt_identity] = lambda: main.AgentIdentity(
        "cus_a",
        "ses_a",
        "TEST_ONLY",
    )

    class MemoryLock:
        async def close(self):
            pass

    async def memory_lock(*_args):
        return MemoryLock()

    monkeypatch.setattr(main, "_try_thread_lock", memory_lock)
    client = TestClient(app)  # no lifespan: no database is opened
    response = client.post(
        "/threads/ses_a/runs/stream",
        json={
            "input": {"utterances": [{"id": "utt_a", "kind": "typed", "text": "给王五转50000元"}]}
        },
    )
    assert response.status_code == 200
    events = {}
    for chunk in response.text.strip().split("\n\n"):
        event, data = chunk.split("\n", 1)
        events[event.removeprefix("event: ")] = json.loads(data.removeprefix("data: "))
    assert "error" not in events
    assert events["values"]["decision"]["replan_hints"][0]["id"] == "hnt_persisted"
    assert events["custom"]["type"] == "cards"
    (card,) = events["custom"]["cards"]
    assert card["type"] == "REPLAN_HINTS"
    assert card["actions"] == [{"kind": "HINT", "ref": "hnt_persisted"}]
    assert events["values"].get("execution") is None
    assert all("execute" not in r["url"] for r in transport.requests)
    client.close()
