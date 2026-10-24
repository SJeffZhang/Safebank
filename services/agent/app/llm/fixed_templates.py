"""C1e fixed respond templates (PRD §6.1.1).

Centralised template store shared by ``routing.py`` (for route-error replies) and
``respond.py`` (for structured success/failure replies with LLM-generated placeholders).

Template keys not used for ``.format()`` substitution are plain text; keys that accept
placeholders document them in their docstring.
"""

from __future__ import annotations

# ── Error / status templates (plain, no placeholders) ─────────────────────

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


# ── Templates with placeholders (LLM fills content; never format() here) ─

# The LLM generates free-form text that may include these fixed wrappers;
# the template key is used only to decide the *approach*, not the exact wording.
#
# EXECUTED / EXECUTED_FAILED / REPLAN_USER_HINTS are LLM-driven — no
# hard-coded slot text is substituted here because the LLM writes the
# full sentence with real numbers/names it extracted from state.

_PLACEHOLDER_TEMPLATES: dict[str, str] = {
    # respond port generates a short success message via LLM.
    # Prompt includes: utterance text, decision conclusion, execution step summary.
    "EXECUTED": "",
    # FAILED execution — LLM explains which step failed and suggests next action.
    "EXECUTED_FAILED": "",
    # REPLAN with user resolver — LLM explains why and lists hints.
    "REPLAN_USER_HINTS": "",
}

# A fallback text for EXECUTED when LLM is unavailable (no API key, transport error).
# Never exposed to users directly; the LLM is the primary renderer.
_EXECUTED_FALLBACK = "操作已完成。"

# A fallback for failed execution when LLM is unavailable.
_EXECUTED_FAILED_FALLBACK = "执行失败，请重试或联系客服。"


def respond_template(template_key: str, *, detail: str = "") -> str:
    """Return the fixed template text for a given key.

    This is the ONLY place where L1 selects user-facing text for routing errors,
    unknowns and template responses.  It never echoes raw server text.
    """
    base = _RESPOND_TEMPLATES.get(template_key, _MISSING_MESSAGE)
    if detail:
        return f"{base}（{detail}）"
    return base


def template_key_exists(key: str) -> bool:
    """Return True if *key* is a known plain template."""
    return key in _RESPOND_TEMPLATES


def is_llm_template(key: str) -> bool:
    """Return True if *key* requires LLM-driven content (not fixed text)."""
    return key in _PLACEHOLDER_TEMPLATES


def executed_fallback() -> str:
    return _EXECUTED_FALLBACK


def executed_failed_fallback() -> str:
    return _EXECUTED_FAILED_FALLBACK


def get_all_plain_templates() -> dict[str, str]:
    """Return a copy of all fixed (non-LLM) templates for inspection."""
    return dict(_RESPOND_TEMPLATES)


# Re-export for convenience
__all__ = [
    "respond_template",
    "template_key_exists",
    "is_llm_template",
    "executed_fallback",
    "executed_failed_fallback",
    "get_all_plain_templates",
]
