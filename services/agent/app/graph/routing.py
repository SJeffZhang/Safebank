"""C1c2 pure routing functions (PRD §6.1.1).

No side effects, no HTTP, no state mutation — these are pure decision functions
that take a ``GatewayDecision`` or error metadata and return a node name plus optional
state update flags.

G4/G8 are currently NOT implemented in L2; the routing logic here handles only
what the C1c1 ``GatewayClient`` can actually return (ALLOW/REQUIRE_APPROVAL/DENY/
REPLAN decisions and error codes).  All routes that depend on G4 approval panel
and G8 execution are labelled with ``_G4_G8_PENDING`` until those services land.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from services.agent.app.gateway.wire import GatewayDecision, UnknownExecution

# ── Fixed respond templates (PRD §6.1.1) ──────────────────────────────────

_RESPOND_TEMPLATES: dict[str, str] = {
    "DENY": "该操作未被授权，无法执行。",
    "REPLAN_AGENT_EXHAUSTED": "当前请求暂时无法处理，请调整后重试。",
    "REPLAN_USER": "需要您确认以下信息：",
    "UTTERANCE_EXPIRED": "请求已过期，请重新发起。",
    "AUTHORIZATION_EXPIRED": "授权已过期，请重新发起。",
    "AUTHORIZATION_REVOKED": "请求已被撤销，请重新发起。",
    "AUTHORIZATION_INSUFFICIENT": "权限不足，无法执行此操作。",
    "RATE_LIMITED": "请求过于频繁，请稍后重试。本次未执行任何操作。",
    "DEPENDENCY_UNAVAILABLE": "服务暂不可用，未执行任何操作。",
    "UNKNOWN": "请求已提交，正在确认中…",
    "REINITIATE": "请求无法处理，请重新发起。未执行任何操作。",
    "APPROVAL_REJECTED": "审批已被拒绝。",
    "APPROVAL_EXPIRED": "审批已过期。",
    "APPROVAL_SUPERSEDED": "审批已被后续请求替代。",
    "APPROVAL_USED": "审批已被使用。",
    "OTP_LOCKED": "验证码已锁定，请稍后重试。",
    "EXECUTION_CANCELLED": "执行已被取消。",
    "EXECUTION_DEADLINE_EXPIRED": "执行已超时。",
    "EXECUTION_IN_PROGRESS": "该计划正在执行中，请稍后查看。",
    "EXECUTION_FAILED": "执行失败：",
    "GENERIC_ERROR": "请求无法处理，请重新发起。未执行任何操作。",
}

_MISSING_MESSAGE = "请求无法处理，请重新发起。未执行任何操作。"


def respond_template(template_key: str, *, detail: str = "") -> str:
    """Return the fixed template text for a given key.

    This is the ONLY place where L1 selects user-facing text for routing errors,
    unknowns and template responses.  It never echoes raw server text.
    """
    base = _RESPOND_TEMPLATES.get(template_key, _MISSING_MESSAGE)
    if detail:
        return f"{base}（{detail}）"
    return base


# ── Route result ---------------------------------------------------------


@dataclass(frozen=True)
class RouteResult:
    """Immutable routing decision for one evaluate/execute step."""

    node: str  # "execute" | "await_approval" | "respond" | "gather"
    state: dict = field(default_factory=dict)  # state updates
    # True when a produceable respond node should just render a template
    # rather than call the LLM respond port (for protocol errors, transport
    # failures, and fixed-code responses).
    use_fixed_reply: bool = False
    fixed_reply: str = ""


# ── Evaluate routing ────────────────────────────────────────────────────


def route_evaluate(
    decision: GatewayDecision | None,
    replan_count: int,
) -> RouteResult:
    """Route after a successful evaluate (200 with GatewayDecision).

    Returns a route *without* fixed reply — ``respond`` still calls the LLM
    port for template rendering.
    """
    if decision is None:
        return RouteResult("respond", {}, True, respond_template("GENERIC_ERROR"))

    value = decision.decision

    if value == "ALLOW":
        return RouteResult("execute")

    if value == "REQUIRE_APPROVAL":
        if decision.approval_id is None:
            return RouteResult("respond", {}, True, respond_template("GENERIC_ERROR"))
        return RouteResult("await_approval")

    if value == "DENY":
        return RouteResult("respond", {}, True, respond_template("DENY"))

    if value == "REPLAN":
        findings = decision.findings
        resolvers = [f.resolver for f in findings]
        if resolvers and all(r == "agent" for r in resolvers) and replan_count < 2:
            return RouteResult("gather", {"replan_count": replan_count + 1})
        # user resolver OR replan exhausted
        reply = respond_template(
            "REPLAN_USER" if any(r == "user" for r in resolvers) else "REPLAN_AGENT_EXHAUSTED"
        )
        return RouteResult("respond", {}, True, reply)

    return RouteResult("respond", {}, True, respond_template("GENERIC_ERROR"))


def route_evaluate_error(
    status: int,
    code_value: str,
    replan_count: int,
    decision: GatewayDecision | None = None,
) -> RouteResult:
    """Route after a non-200 evaluate response (PRD §6.1.1 evaluate error rows)."""

    # 422 CONTRACT_INVALID → plan (shared replan budget)
    if status == 422 and code_value == "CONTRACT_INVALID":
        if replan_count >= 2:
            return RouteResult("respond", {}, True, respond_template("REPLAN_AGENT_EXHAUSTED"))
        return RouteResult("gather", {"replan_count": replan_count + 1})

    # 401 / 403 / 404 → respond (reinitiate)
    if status in (401, 403, 404):
        return RouteResult("respond", {}, True, respond_template("REINITIATE"))

    # 409 variant routes
    if status == 409:
        tkey = _ERROR_CODE_TO_TEMPLATE.get(code_value, "REINITIATE")
        return RouteResult("respond", {}, True, respond_template(tkey))

    # 429 → respond
    if status == 429:
        return RouteResult("respond", {}, True, respond_template("RATE_LIMITED"))

    # 503 → respond
    if status == 503:
        return RouteResult("respond", {}, True, respond_template("DEPENDENCY_UNAVAILABLE"))

    # Default: respond
    return RouteResult("respond", {}, True, respond_template("GENERIC_ERROR"))


# ── Execute routing ─────────────────────────────────────────────────────


def route_execute_success(
    result_or_unknown: UnknownExecution | None = None,
) -> RouteResult:
    """Route after a 200 ExecutionResult or 202 UNKNOWN acknowledgement."""
    if result_or_unknown is not None:
        # 202 UNKNOWN
        return RouteResult(
            "respond", {"execution_status": "UNKNOWN"}, True, respond_template("UNKNOWN")
        )
    # 200 success
    return RouteResult("respond")


def route_execute_error(
    status: int,
    code_value: str,
    decision: GatewayDecision | None = None,
    evaluate_reentry_count: int = 0,
) -> RouteResult:
    """Route after a non-200 execute response (PRD §6.1.1 execute error rows).

    STATE_CHANGED / POLICY_CHANGED with a new decision is the only path that
    re-routes (at most once) instead of going directly to respond.
    """
    if decision is not None:
        # 409 with a re-evaluated decision: STATE_CHANGED or POLICY_CHANGED
        d = decision.decision
        if d == "REQUIRE_APPROVAL":
            if evaluate_reentry_count >= 1:
                return RouteResult("respond", {}, True, respond_template("REINITIATE"))
            return RouteResult(
                "await_approval", {"evaluate_reentry_count": evaluate_reentry_count + 1}
            )
        if d == "ALLOW":
            if evaluate_reentry_count >= 1:
                return RouteResult("respond", {}, True, respond_template("REINITIATE"))
            return RouteResult("execute", {"evaluate_reentry_count": evaluate_reentry_count + 1})
        # DENY / REPLAN with decision → respond
        return RouteResult(
            "respond",
            {},
            True,
            respond_template("DENY" if d == "DENY" else "REPLAN_AGENT_EXHAUSTED"),
        )

    # 202 → unknown (done in route_execute_success via execute node, not error path)
    # 503 on execute → unknown
    if status == 503:
        return RouteResult(
            "respond", {"execution_status": "UNKNOWN"}, True, respond_template("UNKNOWN")
        )

    # Transport error → unknown (outcome uncertainty)
    if status == 0:
        return RouteResult(
            "respond", {"execution_status": "UNKNOWN"}, True, respond_template("UNKNOWN")
        )

    # 401/403/404 → respond
    if status in (401, 403, 404):
        return RouteResult("respond", {}, True, respond_template("REINITIATE"))

    # 409 without decision (APPROVAL_*, EXECUTION_*, etc.)
    if status == 409:
        tkey = _ERROR_CODE_TO_TEMPLATE.get(code_value, "REINITIATE")
        return RouteResult("respond", {}, True, respond_template(tkey))

    # Default
    return RouteResult("respond", {}, True, respond_template("GENERIC_ERROR"))


def route_error(
    status: int | None,
    code_value: str,
    phase: str,
    category: str,
) -> dict:
    """Build a safe, fixed ``route_error`` dict for state storage.

    NEVER exposes httpx Exception, request details, raw message or traceback.
    The returned dict is safe to checkpoint.
    """
    return {
        "status": status,
        "code": code_value,
        "phase": phase,
        "category": category,
    }


# ── Error code → template key mapping ───────────────────────────────────

_ERROR_CODE_TO_TEMPLATE: dict[str, str] = {
    "UTTERANCE_EXPIRED": "UTTERANCE_EXPIRED",
    "AUTHORIZATION_EXPIRED": "AUTHORIZATION_EXPIRED",
    "AUTHORIZATION_REVOKED": "AUTHORIZATION_REVOKED",
    "AUTHORIZATION_INSUFFICIENT": "AUTHORIZATION_INSUFFICIENT",
    "IDEMPOTENCY_CONFLICT": "REINITIATE",
    "APPROVAL_REJECTED": "APPROVAL_REJECTED",
    "APPROVAL_EXPIRED": "APPROVAL_EXPIRED",
    "APPROVAL_SUPERSEDED": "APPROVAL_SUPERSEDED",
    "APPROVAL_USED": "APPROVAL_USED",
    "OTP_LOCKED": "OTP_LOCKED",
    "EXECUTION_CANCELLED": "EXECUTION_CANCELLED",
    "EXECUTION_DEADLINE_EXPIRED": "EXECUTION_DEADLINE_EXPIRED",
    "EXECUTION_IN_PROGRESS": "EXECUTION_IN_PROGRESS",
    "STATE_CHANGED": "REINITIATE",
    "POLICY_CHANGED": "REINITIATE",
}


# ── REPLAN read whitelist ───────────────────────────────────────────────


def replan_whitelist_tools(findings: tuple) -> frozenset[str]:
    """Extract read-tool names from REPLAN finding ``evidence_refs``.

    Only tool names that appear as read-tool names in the contracts are
    included; any other ref is silently dropped because it cannot be turned
    into a valid tool call.
    """
    from safebank_contracts.tools import READ_ARGS

    tools: set[str] = set()
    for finding in findings:
        for ref in getattr(finding, "evidence_refs", ()):
            if isinstance(ref, str) and ref in READ_ARGS:
                tools.add(ref)
    return frozenset(tools)


# ── UNKNOWN detection ───────────────────────────────────────────────────


def is_unknown(state_execution: dict | None) -> bool:
    """Check if an execution result represents an UNKNOWN (unconfirmed) outcome."""
    if state_execution is None:
        return False
    return state_execution.get("status") == "UNKNOWN"
