"""C1c2 Task 2: context-safe reads and bounded replan (red tests).

Tests the read-bridge paths (G2 through GatewayClient), REPLAN whitelist
enforcement (evidence_refs only, max 3 actual HTTP reads), shared replan
budget (REPLAN + 422 CONTRACT_INVALID share the same 2-count limit), and
that user-resolver REPLAN findings do NOT trigger auto-read.

These tests exercise the routing module directly; the gather/gateway graph
integration tests appear in test_routes.py.
"""

from __future__ import annotations

from services.agent.app.gateway.wire import (
    GatewayDecision,
    GatewayFinding,
)
from services.agent.app.graph.routing import (
    replan_whitelist_tools,
    route_evaluate,
    route_evaluate_error,
)


def _decision(value: str, plan_id: str = "pln_a", findings: tuple = (), replan_count: int = 0):
    """Helper to build a GatewayDecision-like routing call."""
    return GatewayDecision(
        plan_id=plan_id,
        evaluation_id="evl_1",
        revision=1,
        decision=value,
        risk="YELLOW",
        findings=findings,
        replan_hints=(),
        preflight={},
    )


def _finding(code: str, resolver: str | None = "agent", evidence_refs: tuple = ()):
    return GatewayFinding(
        code=code,
        effect="REPLAN",
        resolver=resolver,
        evidence_refs=evidence_refs,
    )


# ── Read tool whitelist (evidence_refs → READ_ARGS) ──────────────────────


def test_replan_whitelist_extracts_only_read_tools():
    """Only evidence_refs that are actual read tool names appear in the whitelist."""
    findings = (_finding("ENTITY_UNSEEN", evidence_refs=("list_payees", "not_a_tool", "")),)
    tools = replan_whitelist_tools(findings)
    assert "list_payees" in tools
    assert "not_a_tool" not in tools
    assert "" not in tools


def test_replan_whitelist_empty_when_no_evidence_refs():
    findings = (_finding("ENTITY_UNSEEN", evidence_refs=()),)
    tools = replan_whitelist_tools(findings)
    assert tools == frozenset()


# ── REPLAN routing ───────────────────────────────────────────────────────


def test_replan_agent_resolver_reroutes_to_gather_once():
    """First REPLAN with agent resolver → gather (replan_count becomes 1)."""
    d = _decision("REPLAN", findings=(_finding("ENTITY_UNSEEN", evidence_refs=("list_payees",)),))
    r = route_evaluate(d, replan_count=0)
    assert r.node == "gather"
    assert r.state.get("replan_count") == 1


def test_replan_agent_resolver_reroutes_to_gather_twice():
    """Second REPLAN with agent resolver → gather (replan_count becomes 2)."""
    d = _decision("REPLAN", findings=(_finding("ENTITY_UNSEEN", evidence_refs=("get_accounts",)),))
    r = route_evaluate(d, replan_count=1)
    assert r.node == "gather"
    assert r.state.get("replan_count") == 2


def test_replan_at_limit_goes_to_respond():
    """Third REPLAN attempt (replan_count >= 2) → respond."""
    d = _decision("REPLAN", findings=(_finding("ENTITY_UNSEEN", evidence_refs=("list_payees",)),))
    r = route_evaluate(d, replan_count=2)
    assert r.node == "respond"
    assert r.use_fixed_reply


def test_replan_user_resolver_goes_to_respond():
    """REPLAN with any user-resolver finding → respond, not gather."""
    d = _decision("REPLAN", findings=(_finding("UNKNOWN_SENSITIVE_ARG", resolver="user"),))
    r = route_evaluate(d, replan_count=0)
    assert r.node == "respond"


def test_replan_mixed_resolver_goes_to_respond():
    """REPLAN with both agent and user resolvers → respond (user wins)."""
    d = _decision(
        "REPLAN",
        findings=(
            _finding("ENTITY_UNSEEN", evidence_refs=("list_payees",)),
            _finding("UNKNOWN_SENSITIVE_ARG", resolver="user"),
        ),
    )
    r = route_evaluate(d, replan_count=0)
    assert r.node == "respond"


# ── 422 CONTRACT_INVALID shares replan budget ────────────────────────────


def test_422_contract_invalid_reroutes_to_gather_increments_count():
    r = route_evaluate_error(422, "CONTRACT_INVALID", replan_count=0)
    assert r.node == "gather"
    assert r.state.get("replan_count") == 1


def test_422_after_replan_stops_at_2():
    r = route_evaluate_error(422, "CONTRACT_INVALID", replan_count=1)
    assert r.node == "gather"
    assert r.state.get("replan_count") == 2


def test_422_at_limit_stops():
    r = route_evaluate_error(422, "CONTRACT_INVALID", replan_count=2)
    assert r.node == "respond"
    assert r.use_fixed_reply


def test_422_then_replan_shared_budget_exhaustion():
    """422 once + REPLAN once = replan_count 2, third attempt blocked."""
    d = _decision("REPLAN", findings=(_finding("ENTITY_UNSEEN", evidence_refs=("list_payees",)),))
    # After 422 incremented to 1, a REPLAN from same flow would see count=1.
    r = route_evaluate(d, replan_count=1)
    assert r.node == "gather"
    assert r.state.get("replan_count") == 2
    # Now at 2, another attempt is blocked.
    d2 = _decision("REPLAN", findings=(_finding("ENTITY_UNSEEN", evidence_refs=("get_accounts",)),))
    r2 = route_evaluate(d2, replan_count=2)
    assert r2.node == "respond"


# ── Other evaluate error codes ───────────────────────────────────────────


def test_409_utterance_expired_goes_to_respond():
    r = route_evaluate_error(409, "UTTERANCE_EXPIRED", 0)
    assert r.node == "respond"
    assert r.use_fixed_reply


def test_401_goes_to_respond():
    r = route_evaluate_error(401, "AUTHORIZATION_EXPIRED", 0)
    assert r.node == "respond"


def test_403_goes_to_respond():
    r = route_evaluate_error(403, "AUTHORIZATION_INSUFFICIENT", 0)
    assert r.node == "respond"


def test_404_goes_to_respond():
    r = route_evaluate_error(404, "NOT_FOUND", 0)
    assert r.node == "respond"


def test_429_goes_to_respond():
    r = route_evaluate_error(429, "RATE_LIMITED", 0)
    assert r.node == "respond"


def test_503_goes_to_respond():
    r = route_evaluate_error(503, "DEPENDENCY_UNAVAILABLE", 0)
    assert r.node == "respond"


# ── ALLOW / DENY / REQUIRE_APPROVAL routing ──────────────────────────────


def test_allow_goes_to_execute():
    r = route_evaluate(_decision("ALLOW"), 0)
    assert r.node == "execute"


def test_deny_goes_to_respond():
    r = route_evaluate(_decision("DENY"), 0)
    assert r.node == "respond"
    assert r.use_fixed_reply


def test_require_approval_goes_to_await():
    d = _decision("REQUIRE_APPROVAL").model_copy(update={"approval_id": "apv_a"})
    r = route_evaluate(d, 0)
    assert r.node == "await_approval"


def test_require_approval_without_id_goes_to_respond():
    d = _decision("REQUIRE_APPROVAL")  # approval_id is None
    r = route_evaluate(d, 0)
    assert r.node == "respond"
