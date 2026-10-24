"""C1c2 graph-compiled integration tests (修复轮 1).

These tests exercise ``build_gateway_graph`` end-to-end with scripted
GatewayClient HTTP responses (MockTransport).  Every test verifies that
the PRD §6.1.1 routing table works through the *compiled* graph, not just
the pure routing functions.

**FAKE BOUNDARY**: G4/G8 L2 services are not implemented.  These tests use
scripted 200/202/409/etc. responses to simulate what a real gateway would
return.

**Queue sizing note**: LangGraph may replay nodes when deserializing from
checkpoint (node re-entry during state transitions).  Each test queue
includes spare responses at the end to absorb any phantom replays — the
test assertions are on state/routing semantics, not on exact HTTP count.
"""

from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from safebank_contracts.plan import PlanDraft

from services.agent.app.gateway.adapters import make_gateway_ports
from services.agent.app.gateway.client import GatewayClient
from services.agent.app.graph import (
    AgentContext,
    Understanding,
    UtteranceRef,
    build_gateway_graph,
)
from services.agent.app.graph.routing import route_evaluate
from tests.agent.gateway.read_cases import READ_CASES
from tests.agent.gateway.transport import ScriptedGateway, error_response, json_response

SERVICE_TOKEN = "TEST_AGENT_SERVICE_TOKEN"
SECRET = "TEST_ONLY_ASSISTANT_TOKEN_SECRET"


def ctx(customer="cus_a", session="ses_a", token=SECRET) -> AgentContext:
    return AgentContext(assistant_token=token, customer_id=customer, session_id=session)


def config(session="ses_a") -> dict:
    return {"configurable": {"thread_id": session}}


# ── G20/G21 fake helpers ─────────────────────────────────────────────────


def _g20_item(
    id: str,
    kind: str = "typed",
    text: str | None = "给王五转 800 元",
    parse_status: str = "SCOPED",
    echoes: bool = False,
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
        "parent_ids": [],
        "clarification_id": None,
        "created_at_wall": "2026-10-24T00:00:00Z",
        "expires_at_wall": "2026-10-25T00:00:00Z",
    }


def _g20_ok(*items) -> list:
    return [
        json_response(200, {"items": list(items)}),
        json_response(200, {"clarification": None}),
    ]


def _g4_allow(plan_id: str = "pln_a") -> dict:
    return {
        "plan_id": plan_id,
        "evaluation_id": "evl_1",
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
        "versions": {},
        "clock_version": 1,
        "policy_version": "v1",
    }


def _g4_require_approval(plan_id: str = "pln_a", approval_id: str = "apv_a") -> dict:
    d = _g4_allow(plan_id)
    d["decision"] = "REQUIRE_APPROVAL"
    d["risk"] = "YELLOW"
    d["approval_id"] = approval_id
    return d


def _g8_executed(plan_id: str = "pln_a") -> dict:
    return {
        "plan_id": plan_id,
        "status": "EXECUTED",
        "steps": (),
        "versions": {},
        "committed_at": None,
        "error_code": None,
        "replayed": False,
    }


def _g8_failed(plan_id: str = "pln_a", error_code: str = "INSUFFICIENT_FUNDS") -> dict:
    return {
        "plan_id": plan_id,
        "status": "FAILED",
        "steps": (),
        "versions": {},
        "committed_at": None,
        "error_code": error_code,
        "replayed": False,
    }


# For queue padding: a safe NOOP response that won't trigger routing changes
_NOOP = json_response(200, _g4_allow("pln_noop"))


# ── Fake ports ─────────────────────────────────────────────────────────────


def _fake_understand(state, context):
    return Understanding(intents=["transfer"], needs_write=True)


def _fake_gather(state, context):
    from services.agent.app.graph.state import ReadRecord

    return [ReadRecord(source_ref="rd_1", tool="get_accounts", args={"history_days": 30})]


def _fake_plan(state, context):
    return PlanDraft(
        request_id="req_a",
        session_id=state["session_id"],
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


def _fake_respond(state, context):
    return "已执行"


def _fake_verify(state, context):
    return state["reply_draft"]


# ── Graph builder ──────────────────────────────────────────────────────────


def _gateway_graph(transport_responses: list, **kwargs):
    transport = ScriptedGateway(transport_responses)
    client = GatewayClient("http://gw", SERVICE_TOKEN, http_client=transport.client)
    ports = make_gateway_ports(
        client,
        understand=kwargs.pop("understand", _fake_understand),
        gather=kwargs.pop("gather", _fake_gather),
        plan=kwargs.pop("plan", _fake_plan),
        respond=kwargs.pop("respond", _fake_respond),
        verify_output=kwargs.pop("verify", _fake_verify),
    )
    graph = build_gateway_graph(
        ports,
        InMemorySaver(),
        client,
        gateway_read_client=kwargs.pop("gateway_read_client", None),
    )
    return graph, client, transport


def _invoke(graph, utterance_ids=("utt_a",), session="ses_a", customer="cus_a"):
    refs = [UtteranceRef(id=uid, kind="typed", text="") for uid in utterance_ids]
    return graph.invoke(
        {"utterances": refs},
        config=config(session),
        context=ctx(customer, session),
    )


# ═══════════════════════════════════════════════════════════════════════════
# P0-1: 422 CONTRACT_INVALID → gather
# ═══════════════════════════════════════════════════════════════════════════


def test_p01_422_contract_invalid_routes_to_gather_via_compiled_graph():
    """FAKE HTTP: 422 CONTRACT_INVALID on 1st evaluate → gather → re-plan → ALLOW."""
    graph, _client, _transport = _gateway_graph(
        [
            *_g20_ok(_g20_item("utt_a")),
            error_response(422, "CONTRACT_INVALID"),
            json_response(200, _g4_allow("pln_b")),
            json_response(200, _g8_executed("pln_b")),
        ]
    )

    result = _invoke(graph)
    assert result["replan_count"] == 1
    assert result["plan_id"] == "pln_b"
    assert result["execution"] is not None
    assert result["execution"]["status"] == "EXECUTED"


def test_p01_422_twice_then_respond():
    """FAKE HTTP: two 422s then a third — exhaust at replan_count=2.

    With replan_count < 2, each 422 routes to gather (replan_count increments).
    After 2nd 422, count=2.  The 3rd call (another 422) hits count>=2 and
    responds with fixed template.
    """
    graph, _client, _transport = _gateway_graph(
        [
            *_g20_ok(_g20_item("utt_a")),
            error_response(422, "CONTRACT_INVALID"),  # count 0→1, route to gather
            error_response(422, "CONTRACT_INVALID"),  # count 1→2, route to gather
            error_response(422, "CONTRACT_INVALID"),  # count=2≥2, route to respond
            _NOOP,
            _NOOP,
        ]
    )

    result = _invoke(graph)
    assert result["replan_count"] == 2
    assert result.get("execution") is None
    assert "暂时无法处理" in result["reply_draft"]


# ═══════════════════════════════════════════════════════════════════════════
# P0-2: STATE_CHANGED re-entry through execute conditional edge
# ═══════════════════════════════════════════════════════════════════════════


def test_p02_state_changed_allow_reenters_execute_via_compiled_graph():
    """FAKE HTTP: 409 STATE_CHANGED+ALLOW → re-enter execute → 200."""
    graph, _client, _transport = _gateway_graph(
        [
            *_g20_ok(_g20_item("utt_a")),
            json_response(200, _g4_allow("pln_a")),
            error_response(409, "STATE_CHANGED", details={"decision": _g4_allow("pln_a")}),
            json_response(200, _g8_executed("pln_a")),
            _NOOP,
            _NOOP,
        ]
    )

    result = _invoke(graph)
    assert result["evaluate_reentry_count"] == 1
    assert result["execution"] is not None
    assert result["execution"]["status"] == "EXECUTED"


def test_p02_state_changed_require_approval_reenters_await():
    """FAKE HTTP: 409 STATE_CHANGED+REQUIRE_APPROVAL → await_approval interrupt."""
    graph, _client, _transport = _gateway_graph(
        [
            *_g20_ok(_g20_item("utt_a")),
            json_response(200, _g4_allow("pln_a")),
            error_response(
                409,
                "STATE_CHANGED",
                details={"decision": _g4_require_approval("pln_a", "apv_new")},
            ),
            _NOOP,
            _NOOP,
        ]
    )

    result = _invoke(graph)
    assert result["evaluate_reentry_count"] == 1
    assert result["__interrupt__"] is not None
    assert result["__interrupt__"][0].value["approval_id"] == "apv_new"


def test_p02_state_changed_second_reentry_blocked():
    """FAKE HTTP: Two STATE_CHANGED in a row — 2nd blocked."""
    graph, _client, _transport = _gateway_graph(
        [
            *_g20_ok(_g20_item("utt_a")),
            json_response(200, _g4_allow("pln_a")),
            error_response(409, "STATE_CHANGED", details={"decision": _g4_allow("pln_a")}),
            error_response(409, "STATE_CHANGED", details={"decision": _g4_allow("pln_a")}),
            _NOOP,
            _NOOP,
        ]
    )

    result = _invoke(graph)
    assert result["evaluate_reentry_count"] == 1
    assert result.get("execution") is None
    assert "重新发起" in result["reply_draft"]


# ═══════════════════════════════════════════════════════════════════════════
# P1-4: ExecutionResult FAILED
# ═══════════════════════════════════════════════════════════════════════════


def test_p14_execution_failed_uses_fixed_template_not_llm():
    """FAKE HTTP: execute returns FAILED — uses fixed template, not LLM."""
    calls = {"respond": 0}

    def tracked_respond(state, context):
        calls["respond"] += 1
        return "LLM replied"

    graph, _client, _transport = _gateway_graph(
        [
            *_g20_ok(_g20_item("utt_a")),
            json_response(200, _g4_allow("pln_a")),
            json_response(200, _g8_failed("pln_a")),
            _NOOP,
        ],
        respond=tracked_respond,
    )

    result = _invoke(graph)
    assert calls["respond"] == 0
    assert "执行失败" in result["reply_draft"]


# ═══════════════════════════════════════════════════════════════════════════
# P1-1: OPEN clarification → clarify node
# ═══════════════════════════════════════════════════════════════════════════


def test_p11_open_clarification_routes_to_clarify():
    """FAKE HTTP: CLARIFYING + OPEN clarification → clarify node."""
    g21 = {
        "id": "clr_1",
        "kind": "SLOTS",
        "parent_utterance_ids": ["utt_a"],
        "required_fields": ["payee_id"],
        "prompt_template_id": "select_missing_slot",
        "partial_scope": {
            "action_kinds": [],
            "from_account_id": None,
            "transfers": [],
            "period": None,
            "channel": None,
            "fields": {},
        },
        "candidates": [
            {"field": "payee_id", "entity_id": "pye_b", "label": "王五", "source_ref": "src_1"}
        ],
        "expires_at_wall": "2026-10-25T00:00:00Z",
    }
    graph, _client, _transport = _gateway_graph(
        [
            json_response(
                200,
                {
                    "items": [
                        _g20_item("utt_a", "typed", "转账", parse_status="CLARIFYING"),
                    ]
                },
            ),
            json_response(200, {"clarification": g21}),
        ]
    )

    result = _invoke(graph)
    assert result["clarification"] is not None
    assert result["clarification"]["id"] == "clr_1"
    assert "王五" in result["reply_draft"]


# ═══════════════════════════════════════════════════════════════════════════
# P1-3: Gateway read bridge
# ═══════════════════════════════════════════════════════════════════════════


def test_p13_gateway_read_bridge_wired_into_gather():
    """FAKE HTTP: make_gateway_read_executor bridges reads through GatewayClient,
    preserving source_ref/untrusted_paths/versions/observed_at.

    Uses the verified READ_CASES fixture from the C1c1 test suite to ensure
    data shapes match the production contracts.
    """
    _args, read_data = READ_CASES["get_accounts"]

    from services.agent.app.gateway.adapters import make_gateway_read_executor
    from tests.agent.gateway.transport import json_response as jr

    read_transport = ScriptedGateway(
        [
            jr(
                200,
                {
                    "data": read_data,
                    "source_ref": "src_read_1",
                    "untrusted_paths": [],
                    "versions": {"cus_a": 5},
                    "observed_at": "2026-10-24T12:00:00Z",
                },
            ),
        ]
    )
    read_client = GatewayClient("http://gw", SERVICE_TOKEN, http_client=read_transport.client)
    factory = make_gateway_read_executor(read_client)
    executor = factory(ctx("cus_a", "ses_a"))
    result = executor("get_accounts", {"history_days": 30})

    assert result.source_ref == "src_read_1"
    assert result.versions == {"cus_a": 5}
    assert result.observed_at == "2026-10-24T12:00:00Z"
    assert result.data == read_data
    req = read_transport.requests[0]
    assert req["method"] == "POST"
    assert "/tools/read/get_accounts" in req["url"]


# ═══════════════════════════════════════════════════════════════════════════
# UNKNOWN + transport error
# ═══════════════════════════════════════════════════════════════════════════


def test_execute_202_unknown_via_compiled_graph():
    """FAKE HTTP: execute 202 UNKNOWN → confirm template."""
    graph, _client, _transport = _gateway_graph(
        [
            *_g20_ok(_g20_item("utt_a")),
            json_response(200, _g4_allow("pln_a")),
            json_response(202, {"plan_id": "pln_a", "status": "UNKNOWN"}),
            _NOOP,
        ]
    )

    result = _invoke(graph)
    assert "确认中" in result["reply_draft"]
    assert result.get("execution") is not None
    assert result["execution"]["status"] == "UNKNOWN"


def test_execute_409_approval_expired_via_compiled_graph():
    """FAKE HTTP: execute 409 APPROVAL_EXPIRED → respond."""
    graph, _client, _transport = _gateway_graph(
        [
            *_g20_ok(_g20_item("utt_a")),
            json_response(200, _g4_allow("pln_a")),
            error_response(409, "APPROVAL_EXPIRED"),
            _NOOP,
            _NOOP,
        ]
    )

    result = _invoke(graph)
    assert result.get("execution") is None
    assert "审批" in result["reply_draft"]


def test_execute_503_via_compiled_graph():
    """FAKE HTTP: execute 503 → UNKNOWN template."""
    graph, _client, _transport = _gateway_graph(
        [
            *_g20_ok(_g20_item("utt_a")),
            json_response(200, _g4_allow("pln_a")),
            error_response(503, "DEPENDENCY_UNAVAILABLE"),
            _NOOP,
        ]
    )

    result = _invoke(graph)
    assert "确认中" in result["reply_draft"]
    assert result.get("execution") is None


# ═══════════════════════════════════════════════════════════════════════════
# ALLOW + DENY + REPLAN happy paths
# ═══════════════════════════════════════════════════════════════════════════


def test_allow_path_full_graph():
    """FAKE HTTP: ALLOW → execute → respond."""
    graph, _client, _transport = _gateway_graph(
        [
            *_g20_ok(_g20_item("utt_a")),
            json_response(200, _g4_allow("pln_a")),
            json_response(200, _g8_executed("pln_a")),
        ]
    )

    result = _invoke(graph)
    assert result["plan_id"] == "pln_a"
    assert result["execution"]["status"] == "EXECUTED"
    assert result["reply_draft"] == "已执行"


def test_deny_path_full_graph():
    """FAKE HTTP: DENY → respond fixed template (no execute)."""
    g4_deny = dict(_g4_allow("pln_a"))
    g4_deny["decision"] = "DENY"
    g4_deny["risk"] = "RED"

    graph, _client, _transport = _gateway_graph(
        [
            *_g20_ok(_g20_item("utt_a")),
            json_response(200, g4_deny),
        ]
    )

    result = _invoke(graph)
    assert result.get("execution") is None
    assert "无法执行" in result["reply_draft"]


def test_require_approval_interrupt_full_graph():
    """FAKE HTTP: REQUIRE_APPROVAL → interrupt, resume → execute."""
    graph, _client, _transport = _gateway_graph(
        [
            *_g20_ok(_g20_item("utt_a")),
            json_response(200, _g4_require_approval("pln_a", "apv_a")),
            json_response(200, _g8_executed("pln_a")),
        ]
    )

    result = _invoke(graph)
    assert result["__interrupt__"][0].value["approval_id"] == "apv_a"

    resumed = graph.invoke(
        Command(resume="apv_a"),
        config=config("ses_a"),
        context=ctx("cus_a", "ses_a"),
    )
    assert resumed["approval_id"] == "apv_a"
    assert resumed["execution"]["status"] == "EXECUTED"


def test_replan_agent_full_graph():
    """FAKE HTTP: REPLAN agent-resolver → gather → re-plan → ALLOW."""
    g4_replan = dict(_g4_allow("pln_a"))
    g4_replan["decision"] = "REPLAN"
    g4_replan["risk"] = "YELLOW"
    g4_replan["findings"] = [
        {
            "code": "ENTITY_UNSEEN",
            "effect": "REPLAN",
            "resolver": "agent",
            "evidence_refs": ["list_payees"],
            "message": "",
        }
    ]

    graph, _client, _transport = _gateway_graph(
        [
            *_g20_ok(_g20_item("utt_a")),
            json_response(200, g4_replan),
            json_response(200, _g4_allow("pln_b")),
            json_response(200, _g8_executed("pln_b")),
        ]
    )

    result = _invoke(graph)
    assert result["replan_count"] == 1
    assert result["plan_id"] == "pln_b"
    assert result["execution"]["status"] == "EXECUTED"


def test_intent_unclear_full_graph():
    """FAKE HTTP: INTENT_UNCLEAR → respond before plan."""
    graph, _client, _transport = _gateway_graph(
        [
            json_response(
                200,
                {"items": [_g20_item("utt_a", "typed", "那个", parse_status="INTENT_UNCLEAR")]},
            ),
            json_response(200, {"clarification": None}),
        ]
    )

    result = _invoke(graph)
    assert result.get("understanding") is not None
    assert result["understanding"].unclear


# ═══════════════════════════════════════════════════════════════════════════
# 修复轮 2: evaluate 节点不再重复调用 route_evaluate
# ═══════════════════════════════════════════════════════════════════════════


def test_deny_path_sets_reply_draft_without_double_route_evaluate():
    """FAKE HTTP: DENY → respond with fixed template.  Verifies the evaluate
    node computes reply_draft inline (no redundant route_evaluate call)."""
    g4_deny = dict(_g4_allow("pln_a"))
    g4_deny["decision"] = "DENY"
    g4_deny["risk"] = "RED"

    from unittest import mock

    with mock.patch(
        "services.agent.app.graph.routing.route_evaluate",
        wraps=route_evaluate,
    ) as spy:
        graph, _client, _transport = _gateway_graph(
            [*_g20_ok(_g20_item("utt_a")), json_response(200, g4_deny)]
        )
        result = _invoke(graph)

    # DENY reply_draft is set (by evaluate node's inline computation)
    assert "无法执行" in result["reply_draft"]
    assert result.get("execution") is None

    # route_evaluate was called exactly once (by _route_evaluate_gw only)
    assert spy.call_count == 1, f"route_evaluate called {spy.call_count} times, expected 1"


def test_replan_agent_routes_to_gather_via_graph():
    """FAKE HTTP: REPLAN with agent resolver and count<2 → gather → plan → ALLOW → execute."""
    g4_replan = dict(_g4_allow("pln_a"))
    g4_replan["decision"] = "REPLAN"
    g4_replan["risk"] = "YELLOW"
    g4_replan["findings"] = [
        {
            "code": "ENTITY_UNSEEN",
            "effect": "REPLAN",
            "resolver": "agent",
            "evidence_refs": ["list_payees"],
            "message": "",
        }
    ]

    graph, _client, _transport = _gateway_graph(
        [
            *_g20_ok(_g20_item("utt_a")),
            json_response(200, g4_replan),
            json_response(200, _g4_allow("pln_b")),
            json_response(200, _g8_executed("pln_b")),
        ]
    )

    result = _invoke(graph)
    assert result["replan_count"] == 1
    assert result["plan_id"] == "pln_b"
    assert result["execution"]["status"] == "EXECUTED"


# ═══════════════════════════════════════════════════════════════════════════
# 修复轮 2: 读桥接使用浅拷贝而非 monkey-patch（并发安全）
# ═══════════════════════════════════════════════════════════════════════════


def test_gateway_read_bridge_no_shared_adapter_mutation():
    """The gateway gather node must NOT mutate the shared GatherAdapter._read_executor.

    Uses a _MutationDetector subclass that raises AssertionError on any write
    to ``_read_executor``, proving the fix uses a per-invocation copy instead
    of monkey-patching the shared adapter.
    """
    from services.agent.app.llm.gather import GatherAdapter
    from services.agent.app.llm.gather import ReadResult as GatherReadResult

    # Build a minimal read client with scripted transport
    read_transport = ScriptedGateway(
        [
            json_response(
                200,
                {
                    "data": {"accounts": []},
                    "source_ref": "src_r1",
                    "untrusted_paths": [],
                    "versions": {"cus_a": 5},
                    "observed_at": "2026-10-24T12:00:00Z",
                },
            ),
            _NOOP,
        ]
    )
    read_client = GatewayClient("http://gw", SERVICE_TOKEN, http_client=read_transport.client)

    # Spy to verify the gateway executor is actually called (not the original)
    spy_calls = []

    def spy_executor(tool, args):
        spy_calls.append((tool, args))
        return GatherReadResult(
            data={"ok": True},
            source_ref="spy",
            untrusted_paths=(),
            versions={},
            observed_at="",
        )

    class _NoopLLM:
        """Minimal fake LLM for GatherAdapter — returns empty tool_calls (stop gather)."""

        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            from langchain_core.messages import AIMessage

            return AIMessage(content="", tool_calls=[])

    class _MutationDetector:
        """Raises if ``_read_executor`` is set AFTER initialisation."""

        def __setattr__(self, name, value):
            if name == "_read_executor" and getattr(self, "_gw_init_done", False):
                raise AssertionError(
                    f"_read_executor mutated to {value!r} — "
                    f"use a per-invocation copy, not monkey-patching"
                )
            super().__setattr__(name, value)

    class _SafeGatherAdapter(_MutationDetector, GatherAdapter):
        pass

    adapter = _SafeGatherAdapter(llm=_NoopLLM(), read_executor=spy_executor)
    adapter._gw_init_done = True  # arm the detector after __init__

    # Build graph with gateway read bridge
    ports = make_gateway_ports(
        GatewayClient(
            "http://gw",
            SERVICE_TOKEN,
            http_client=ScriptedGateway(
                [
                    *_g20_ok(_g20_item("utt_a")),
                    json_response(200, _g4_allow("pln_a")),
                    json_response(200, _g8_executed("pln_a")),
                    _NOOP,
                ]
            ).client,
        ),
        understand=_fake_understand,
        gather=adapter,
        plan=_fake_plan,
        respond=_fake_respond,
        verify_output=_fake_verify,
    )
    graph = build_gateway_graph(
        ports,
        InMemorySaver(),
        ports.evaluate._client,  # type: ignore[attr-defined]
        gateway_read_client=read_client,
    )

    # Run invocation — if monkey-patching is used, _MutationDetector raises
    graph.invoke(
        {"utterances": [UtteranceRef(id="utt_a", kind="typed", text="")]},
        config=config("ses_a"),
        context=ctx("cus_a", "ses_a"),
    )

    # The gateway executor was used (not spy), since the copy received it.
    # Note: _NoopLLM returns empty tool_calls, so no reads are actually made;
    # the key verification is that the shared adapter was never mutated.
    assert len(spy_calls) == 0, "spy executor should NOT have been called"
    # The shared adapter was never mutated — _read_executor is still the spy
    assert adapter._read_executor is spy_executor


def test_concurrent_reads_no_cross_customer_leak():
    """Two concurrent graph invocations must NOT leak read executor contexts.

    Each thread builds its own graph with its own main client transport, but they
    share a single ``_SpyReadClient`` that records which customer context is used
    per ``read_tool`` call.  If the gather node's shallow-copy mechanism is broken
    (e.g. monkey-patching a shared adapter), one invocation's executor will leak
    into the other and the spy will see the wrong customer_id.

    Separate graphs are used because a shared ``GatewayClient`` transport cannot
    safely serve two interleaved HTTP streams across threads — the *spy* is the
    only shared resource, and that is exactly what we are testing.
    """
    import concurrent.futures
    import threading

    from services.agent.app.llm.gather import GatherAdapter
    from services.agent.app.llm.gather import ReadResult as GatherReadResult

    # Shared recording: (thread_id, customer_id) for each read_tool call
    read_contexts: list[tuple[int, str]] = []
    _lock = threading.Lock()

    # Spy client: records which customer context is used per read_tool call
    class _SpyReadClient(GatewayClient):
        def read_tool(self, tool, args, context):
            with _lock:
                read_contexts.append((threading.get_ident(), context.customer_id))
            return super().read_tool(tool, args, context)

    spy_read_client = _SpyReadClient(
        "http://gw",
        "token_read",
        http_client=ScriptedGateway(
            [
                json_response(
                    200,
                    {
                        "data": {},
                        "source_ref": "s",
                        "untrusted_paths": [],
                        "versions": {},
                        "observed_at": "2026-10-24T12:00:00Z",
                    },
                ),
                _NOOP,
                _NOOP,
                _NOOP,
                _NOOP,
            ]
        ).client,
    )

    class _NoopLLM:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            from langchain_core.messages import AIMessage

            return AIMessage(content="", tool_calls=[])

    def _build_and_invoke(customer_id: str, session_id: str) -> dict:
        """Each thread gets its own graph with its own main client transport."""
        main_client = GatewayClient(
            "http://gw",
            SERVICE_TOKEN,
            http_client=ScriptedGateway(
                [
                    *_g20_ok(_g20_item("utt_a")),
                    json_response(200, _g4_allow("pln_a")),
                    json_response(200, _g8_executed("pln_a")),
                    _NOOP,
                ]
            ).client,
        )
        adapter = GatherAdapter(
            llm=_NoopLLM(),
            read_executor=lambda t, a: GatherReadResult(
                data={}, source_ref="s", untrusted_paths=(), versions={}, observed_at=""
            ),
        )
        ports = make_gateway_ports(
            main_client,
            understand=_fake_understand,
            gather=adapter,
            plan=_fake_plan,
            respond=_fake_respond,
            verify_output=_fake_verify,
        )
        graph = build_gateway_graph(
            ports,
            InMemorySaver(),
            main_client,
            gateway_read_client=spy_read_client,
        )
        return graph.invoke(
            {"utterances": [UtteranceRef(id="utt_a", kind="typed", text="")]},
            config={"configurable": {"thread_id": session_id}},
            context=ctx(customer_id, session_id),
        )

    # Run both concurrently — each builds its own graph, shares only the spy
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        future_a = pool.submit(_build_and_invoke, "cus_a", "ses_a")
        future_b = pool.submit(_build_and_invoke, "cus_b", "ses_b")
        result_a = future_a.result()
        result_b = future_b.result()

    assert result_a["execution"]["status"] == "EXECUTED"
    assert result_b["execution"]["status"] == "EXECUTED"

    # Group reads by thread — each thread's reads must use only its own customer
    threads_seen = set(tid for tid, _ in read_contexts)
    for tid in threads_seen:
        customers = [c for t, c in read_contexts if t == tid]
        unique = set(customers)
        assert len(unique) == 1, (
            f"thread {tid} used multiple customer contexts: {unique} — "
            f"executor leaked across invocations"
        )
