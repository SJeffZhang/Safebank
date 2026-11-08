"""C1c2 Task 3: decision/execute routes and uncertainty (red tests).

Tests the full PRD §6.1.1 routing table for execute paths (200, 202 UNKNOWN,
409 STATE_CHANGED/POLICY_CHANGED, transport errors, APPROVAL_* codes) and
the re-entry limit for STATE_CHANGED.

G4/G8 are not yet fully implemented in L2; the routing functions handle
what the C1c1 client can return, labelled *_G4_G8_PENDING below.
"""

from __future__ import annotations

import pytest

from services.agent.app.gateway.wire import GatewayDecision
from services.agent.app.graph.routing import (
    respond_template,
    route_evaluate,
    route_execute_error,
    route_execute_success,
)


def _decision(value: str, plan_id: str = "pln_a", approval_id: str | None = None):
    return GatewayDecision(
        plan_id=plan_id,
        evaluation_id="evl_1",
        revision=1,
        decision=value,
        risk="YELLOW",
        findings=(),
        replan_hints=(),
        preflight={},
        approval_id=approval_id,
    )


# ── 200 success ──────────────────────────────────────────────────────────


def test_execute_success_routes_to_respond():
    r = route_execute_success()
    assert r.node == "respond"
    assert not r.use_fixed_reply


# ── 202 UNKNOWN ──────────────────────────────────────────────────────────


def test_execute_202_unknown_routes_to_respond_fixed():
    r = route_execute_success("some_unknown_obj")
    assert r.node == "respond"
    assert r.use_fixed_reply
    assert r.state.get("execution_status") == "UNKNOWN"
    assert "确认中" in r.fixed_reply


# ── 503 on execute → UNKNOWN (not retry) ────────────────────────────────


def test_execute_503_routes_to_respond_unknown():
    r = route_execute_error(503, "DEPENDENCY_UNAVAILABLE")
    assert r.node == "respond"
    assert r.use_fixed_reply
    assert r.state.get("execution_status") == "UNKNOWN"


# ── Transport error on execute → UNKNOWN ─────────────────────────────────


def test_execute_transport_error_routes_to_respond_unknown():
    r = route_execute_error(0, "TRANSPORT")
    assert r.node == "respond"
    assert r.use_fixed_reply
    assert r.state.get("execution_status") == "UNKNOWN"


# ── 409 STATE_CHANGED with new decision ──────────────────────────────────


def test_state_changed_allow_reenters_execute():
    """STATE_CHANGED with ALLOW decision → execute (first re-entry)."""
    d = _decision("ALLOW")
    r = route_execute_error(409, "STATE_CHANGED", decision=d, evaluate_reentry_count=0)
    assert r.node == "execute"
    assert r.state.get("evaluate_reentry_count") == 1


def test_state_changed_require_approval_reenters_await():
    """STATE_CHANGED with REQUIRE_APPROVAL → await_approval."""
    d = _decision("REQUIRE_APPROVAL", approval_id="apv_new")
    r = route_execute_error(409, "STATE_CHANGED", decision=d, evaluate_reentry_count=0)
    assert r.node == "await_approval"
    assert r.state.get("evaluate_reentry_count") == 1


def test_state_changed_deny_goes_to_respond():
    """STATE_CHANGED with DENY → respond."""
    d = _decision("DENY")
    r = route_execute_error(409, "STATE_CHANGED", decision=d, evaluate_reentry_count=0)
    assert r.node == "respond"


def test_state_changed_replan_goes_to_respond():
    """STATE_CHANGED with REPLAN → respond (do not re-gather)."""
    d = _decision("REPLAN")
    r = route_execute_error(409, "STATE_CHANGED", decision=d, evaluate_reentry_count=0)
    assert r.node == "respond"


def test_state_changed_second_reentry_blocked():
    """STATE_CHANGED re-entry at count 1 → respond (max 1 re-entry)."""
    d = _decision("ALLOW")
    r = route_execute_error(409, "STATE_CHANGED", decision=d, evaluate_reentry_count=1)
    assert r.node == "respond"


def test_policy_changed_same_routing_as_state_changed():
    """POLICY_CHANGED follows the same rule as STATE_CHANGED."""
    d = _decision("ALLOW")
    r = route_execute_error(409, "POLICY_CHANGED", decision=d, evaluate_reentry_count=0)
    assert r.node == "execute"
    assert r.state.get("evaluate_reentry_count") == 1


# ── 409 without decision (APPROVAL_* / EXECUTION_*) ─────────────────────


@pytest.mark.parametrize(
    "code",
    [
        "APPROVAL_REJECTED",
        "APPROVAL_EXPIRED",
        "APPROVAL_SUPERSEDED",
        "APPROVAL_USED",
        "OTP_LOCKED",
        "EXECUTION_CANCELLED",
        "EXECUTION_DEADLINE_EXPIRED",
        "EXECUTION_IN_PROGRESS",
    ],
)
def test_409_no_decision_goes_to_respond(code):
    r = route_execute_error(409, code)
    assert r.node == "respond"
    assert r.use_fixed_reply


# ── 401/403/404 on execute ───────────────────────────────────────────────


@pytest.mark.parametrize("status", [401, 403, 404])
def test_auth_errors_on_execute_go_to_respond(status):
    r = route_execute_error(status, "AUTHORIZATION_EXPIRED")
    assert r.node == "respond"
    assert r.use_fixed_reply


# ── Template rendering ───────────────────────────────────────────────────


def test_fixed_templates_are_non_empty():
    templates = [
        "DENY",
        "UNKNOWN",
        "RATE_LIMITED",
        "DEPENDENCY_UNAVAILABLE",
        "REINITIATE",
        "APPROVAL_EXPIRED",
        "EXECUTION_IN_PROGRESS",
        "EXECUTION_FAILED",
    ]
    for key in templates:
        text = respond_template(key)
        assert isinstance(text, str)
        assert len(text) > 0, f"template {key} is empty"


def test_template_with_detail_appends():
    text = respond_template("EXECUTION_FAILED", detail="INSUFFICIENT_FUNDS")
    assert "EXECUTION_FAILED" in text or "执行失败" in text
    assert "INSUFFICIENT_FUNDS" in text


def test_unknown_template_does_not_poll_or_retry():
    """UNKNOWN template says 'confirming' — it must NOT instruct the user to retry."""
    text = respond_template("UNKNOWN")
    assert "确认" in text
    assert "重试" not in text


# ── GatewayDecision edge cases ───────────────────────────────────────────


def test_none_decision_routes_to_respond():
    r = route_evaluate(None, replan_count=0)
    assert r.node == "respond"
    assert r.use_fixed_reply


def test_decision_without_preflight_still_routes():
    """GatewayDecision with empty preflight should still route."""
    d = _decision("ALLOW")
    r = route_evaluate(d, 0)
    assert r.node == "execute"


# ── 5xx on execute → UNKNOWN: L3 may already have committed ─────────────


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (500, "INTERNAL_ERROR"),
        (500, "PROTOCOL"),  # non-JSON or unparseable 500 body
        (502, "PROTOCOL"),
        (504, "PROTOCOL"),
    ],
)
def test_execute_5xx_routes_to_respond_unknown(status, code):
    r = route_execute_error(status, code)
    assert r.node == "respond"
    assert r.use_fixed_reply
    assert r.state.get("execution_status") == "UNKNOWN"
    assert "未执行" not in r.fixed_reply


def test_evaluate_internal_error_has_own_template():
    from services.agent.app.graph.routing import route_evaluate_error

    r = route_evaluate_error(500, "INTERNAL_ERROR", 0)
    assert r.node == "respond"
    assert r.fixed_reply == respond_template("INTERNAL_ERROR")
    assert r.fixed_reply != respond_template("GENERIC_ERROR")
    assert "内部错误" in r.fixed_reply
