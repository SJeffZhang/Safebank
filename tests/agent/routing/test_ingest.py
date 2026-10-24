"""C1c2 Task 1: authoritative ingest and state (red tests).

These tests verify the G20/G21-based ingest in ``build_gateway_graph``.
They use a scripted GatewayClient (MockTransport) to simulate L2 responses;
no real HTTP, no LLM, no DB.  G4/G8 are not implemented in L2 — the fake
boundary is labelled in test names where it applies.
"""

from __future__ import annotations

import httpx

from services.agent.app.gateway.adapters import ingest_via_gateway
from services.agent.app.gateway.client import GatewayClient
from services.agent.app.graph import (
    AgentContext,
)
from tests.agent.gateway.transport import ScriptedGateway, json_response

SERVICE_TOKEN = "TEST_AGENT_SERVICE_TOKEN"
SECRET = "TEST_ONLY_ASSISTANT_TOKEN_SECRET"


def ctx(customer="cus_a", session="ses_a", token=SECRET) -> AgentContext:
    return AgentContext(assistant_token=token, customer_id=customer, session_id=session)


def config(session="ses_a") -> dict:
    return {"configurable": {"thread_id": session}}


# ── G20 helpers ──────────────────────────────────────────────────────────


def _g20_item(
    id: str,
    kind: str = "typed",
    text: str | None = "给王五转 800 元",
    parse_status: str = "SCOPED",
    echoes: bool = False,
    parent_ids: tuple = (),
    clarification_id: str | None = None,
) -> dict:
    return {
        "id": id,
        "kind": kind,
        "text": text,
        "scope": {
            "action_kinds": ["transfer"],
            "from_account_id": None,
            "transfers": [],
            "period": None,
            "channel": None,
            "fields": {},
        },
        "parse_status": parse_status,
        "echoes_untrusted": echoes,
        "parent_ids": list(parent_ids),
        "clarification_id": clarification_id,
        "created_at_wall": "2026-10-24T00:00:00Z",
        "expires_at_wall": "2026-10-25T00:00:00Z",
    }


def _g20_response(*items: dict) -> httpx.Response:
    return json_response(200, {"items": list(items)})


def _g21_response(clarification: dict | None) -> httpx.Response:
    return json_response(200, {"clarification": clarification})


def _fake_allow_evaluate(state, context):
    from services.agent.app.graph.state import Decision, DecisionValue

    return Decision(plan_id="pln_a", decision=DecisionValue.ALLOW)


def _fake_executed(state, context):
    from safebank_contracts.plan import ExecutionResult

    return ExecutionResult(
        plan_id=state.get("plan_id") or "pln_a",
        status="EXECUTED",
        steps=(),
        versions={},
        committed_at=None,
        error_code=None,
        replayed=False,
    )


def _fake_respond(state, context):
    return "已执行"


def _fake_verify(state, context):
    return state["reply_draft"]


def _fake_understand(state, context):
    from services.agent.app.graph.state import Understanding

    return Understanding(intents=["transfer"], needs_write=True)


# ── Task 1.1: G20 authoritative data replaces fake text ─────────────────


def test_ingest_uses_g20_not_input_text():
    """G20 must be the ONLY source of utterance text/kind/parse_status.

    The caller passes utterance IDs; the graph's ingest calls G20 with those
    IDs and uses the returned authoritative data.  The fake input path
    (invoke_new_input with UtteranceRef text) must NOT be used for the
    production gateway graph.
    """
    transport = ScriptedGateway(
        [
            _g20_response(_g20_item("utt_a", "typed", "给王五转 800")),
            _g21_response(None),
        ]
    )
    client = GatewayClient("http://gw", SERVICE_TOKEN, http_client=transport.client)

    # Ingest via gateway: IDs only, no text.
    utts, clarification, warnings = ingest_via_gateway(
        client, ("utt_a",), context=ctx(), session_id="ses_a"
    )

    assert len(utts) == 1
    assert utts[0].id == "utt_a"
    assert utts[0].text == "给王五转 800"
    assert utts[0].kind == "typed"
    assert utts[0].parse_status == "SCOPED"
    assert clarification is None
    assert warnings == []

    # Verify G20 was called with the correct IDs.
    requests = transport.requests
    assert len(requests) == 2  # G20 then G21
    assert "/sessions/ses_a/utterances" in requests[0]["url"]


# ── Task 1.2: null hint/action text ──────────────────────────────────────


def test_null_hint_keeps_kind_scope():
    """Hint and action utterances with text=null must be preserved with
    their kind and scope, not dropped or turned into typed text.

    The LLM adapters (understand/plan) must see null text as "" for prompt
    construction but the routing logic must NOT turn null into typed.
    """
    transport = ScriptedGateway(
        [
            _g20_response(
                _g20_item("utt_a", "typed", "用推荐的卡转账"),
                _g20_item("utt_b", "hint", None, echoes=True),
            ),
            _g21_response(None),
        ]
    )
    client = GatewayClient("http://gw", SERVICE_TOKEN, http_client=transport.client)
    utts, clarification, warnings = ingest_via_gateway(
        client, ("utt_a", "utt_b"), context=ctx(), session_id="ses_a"
    )

    assert len(utts) == 2
    # hint with null text → UtteranceRef with text=""
    hint = utts[1]
    assert hint.kind == "hint"
    assert hint.text is None  # GatewayUtterance keeps null
    assert hint.id == "utt_b"

    # After conversion to UtteranceRef, text defaults to ""
    from services.agent.app.gateway.adapters import gateway_utterance_to_ref

    ref = gateway_utterance_to_ref(hint)
    assert ref.text == ""
    assert ref.id == "utt_b"


# ── Task 1.3: clarification parent matching ──────────────────────────────


def test_clarification_requires_current_parent():
    """An OPEN clarification whose parent is NOT among the current G20 IDs
    must be discarded with a warning, not applied.

    The clarification must only apply when all its parent_utterance_ids are
    in the current request's utterance IDs.
    """
    # Clarification whose parent is "utt_old" — not in current IDs.
    g21 = {
        "id": "clr_1",
        "kind": "SLOTS",
        "parent_utterance_ids": ["utt_old"],
        "required_fields": ["payee_id"],
        "prompt_template_id": "select_missing_slot",
        "partial_scope": {"action_kinds": []},
        "candidates": [],
        "expires_at_wall": "2026-10-25T00:00:00Z",
    }
    transport = ScriptedGateway(
        [
            _g20_response(_g20_item("utt_a", "typed", "转账")),
            _g21_response(g21),
        ]
    )
    client = GatewayClient("http://gw", SERVICE_TOKEN, http_client=transport.client)
    utts, clarification, warnings = ingest_via_gateway(
        client, ("utt_a",), context=ctx(), session_id="ses_a"
    )

    assert len(utts) == 1
    assert clarification is None  # discarded
    assert len(warnings) == 1
    assert "parent" in warnings[0]


def test_clarification_matching_parent_accepted():
    """A clarification whose parent IS in the current IDs is accepted."""
    g21 = {
        "id": "clr_1",
        "kind": "SLOTS",
        "parent_utterance_ids": ["utt_a"],
        "required_fields": ["payee_id"],
        "prompt_template_id": "select_missing_slot",
        "partial_scope": {"action_kinds": []},
        "candidates": [],
        "expires_at_wall": "2026-10-25T00:00:00Z",
    }
    transport = ScriptedGateway(
        [
            _g20_response(_g20_item("utt_a", "typed", "转账")),
            _g21_response(g21),
        ]
    )
    client = GatewayClient("http://gw", SERVICE_TOKEN, http_client=transport.client)
    utts, clarification, warnings = ingest_via_gateway(
        client, ("utt_a",), context=ctx(), session_id="ses_a"
    )

    assert clarification is not None
    assert clarification.id == "clr_1"
    assert warnings == []


# ── Task 1.4: INTENT_UNCLEAR blocks plan ──────────────────────────────────


def test_gateway_parse_unclear_blocks_plan():
    """When G20 returns parse_status=INTENT_UNCLEAR and needs_write is
    detected, the LLM should NOT enter plan — it goes to respond instead.

    This is tested at the routing level: the G20 parse_status must be
    checked *before* the route_understand returns "gather", and INTENT_UNCLEAR
    with needs_write=True must route to respond.
    """
    # The understanding port returns both intents and needs_write.
    # The route_understand function in the graph checks understanding.unclear
    # AND understanding.needs_write.
    # When G20 provides parse_status=INTENT_UNCLEAR, the understand node
    # should incorporate that into the understanding result.
    transport = ScriptedGateway(
        [
            _g20_response(_g20_item("utt_a", "typed", "那个", parse_status="INTENT_UNCLEAR")),
            _g21_response(None),
        ]
    )
    client = GatewayClient("http://gw", SERVICE_TOKEN, http_client=transport.client)
    utts, clarification, warnings = ingest_via_gateway(
        client, ("utt_a",), context=ctx(), session_id="ses_a"
    )

    assert len(utts) == 1
    assert utts[0].parse_status == "INTENT_UNCLEAR"
    # This parse_status should propagate and eventually block plan.
    # The exact routing is tested in the graph-level test with a
    # build_gateway_graph when that function is implemented in Task 1
    # implementation phase.


# ── Task 1.5: two contexts, no token leak ────────────────────────────────


def test_ingest_two_contexts_no_token_leak():
    """Two different AgentContext values using the same GatewayClient
    must NOT mix JWTs between calls.

    This exercises the per-call token injection in GatewayClient.
    """
    transport = ScriptedGateway(
        [
            _g20_response(_g20_item("utt_a", "typed", "第一句")),
            _g21_response(None),
            _g20_response(_g20_item("utt_a", "typed", "第二句")),
            _g21_response(None),
        ]
    )
    client = GatewayClient("http://gw", SERVICE_TOKEN, http_client=transport.client)

    ingest_via_gateway(client, ("utt_a",), context=ctx(token="TOKEN_ONE"), session_id="ses_a")
    ingest_via_gateway(client, ("utt_a",), context=ctx(token="TOKEN_TWO"), session_id="ses_b")

    req0 = transport.requests[0]
    req2 = transport.requests[2]
    assert req0["headers"]["authorization"] == "Bearer TOKEN_ONE"
    assert req2["headers"]["authorization"] == "Bearer TOKEN_TWO"


# ── Task 1.6: G20 response shapes ────────────────────────────────────────


def test_g20_utterance_fields_are_preserved():
    """G20-response fields (echoes_untrusted, parent_ids, clarification_id)
    must be carried into GatewayUtterance models and accessible to routing.
    """
    transport = ScriptedGateway(
        [
            _g20_response(
                _g20_item(
                    "utt_a",
                    "typed",
                    "给王五转",
                    echoes=False,
                    parent_ids=("utt_p",),
                    clarification_id="clr_1",
                )
            ),
            _g21_response(None),
        ]
    )
    client = GatewayClient("http://gw", SERVICE_TOKEN, http_client=transport.client)
    utts, clarification, warnings = ingest_via_gateway(
        client, ("utt_a",), context=ctx(), session_id="ses_a"
    )

    u = utts[0]
    assert u.echoes_untrusted is False
    assert u.parent_ids == ("utt_p",)
    assert u.clarification_id == "clr_1"
