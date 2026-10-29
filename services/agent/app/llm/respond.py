"""C1e LLM-driven respond adapter (PRD §6.1, C1e Task 3).

Replaces the old pass-through ``_make_production_respond`` with a state-aware
adapter that:
- Returns pre-set ``reply_draft`` for fixed-template paths (DENY, UNKNOWN, …)
- Calls the LLM for EXECUTED successes, REPLAN_USER hints, and analysis replies
"""

from __future__ import annotations

import json

from services.agent.app.graph.state import AgentContext, AgentState
from services.agent.app.llm.fixed_templates import (
    executed_failed_fallback,
    executed_fallback,
    respond_template,
)

# ── Prompt templates (kept minimal — LLM fills real numbers from state) ─────


_EXECUTED_SYSTEM = (
    "你是一个银行AI助手，负责向用户简短总结刚完成的操作。"
    "用中文回复，不超过三句话。"
    "只能引用下面提供的数字和名称，**禁止捏造**金额、账户或状态。"
    "提到卡片时只能用下面提供的卡号尾号（如“尾号1234”）；"
    "**禁止**打印卡ID，**禁止**编造尾号；如果不知道尾号，就说“该卡”。"
)


_REPLAN_USER_SYSTEM = (
    "你是一个银行AI助手，负责向用户解释为什么刚才的请求需要用户补充信息。"
    "用中文回复，不超过三句话。"
    "只能引用下面提供的提示标签，**禁止捏造**金额、账户或人名。"
)


_ANALYSIS_SYSTEM = (
    "你是一个银行AI助手，负责向用户提供账户信息和分析结果。"
    "用中文回复。"
    "只能引用下面提供的真实数据，**禁止捏造**数字、账户名或产品名。"
)

# ── Maximum response length for safety ──────────────────────────────────────

_MAX_RESPONSE_CHARS = 600


# ── RespondAdapter ──────────────────────────────────────────────────────────


class RespondAdapter:
    """State-aware respond port that routes to LLM or fixed templates.

    ``llm`` is the raw ``ChatDeepSeek`` instance (temperature=0, thinking disabled).
    """

    def __init__(self, llm: object) -> None:
        self._llm = llm

    def __call__(self, state: AgentState, _context: AgentContext) -> str:
        # ── 1. Fixed-template shortcut ──────────────────────────────────
        # When evaluate/execute already set a reply_draft alongside a
        # route_error (or other fixed-reply indicator), return it directly.
        reply_draft: str = state.get("reply_draft", "") or ""
        route_error: dict | None = state.get("route_error")
        if route_error and reply_draft:
            return reply_draft

        # ── 2. UNKNOWN execution → fixed template ──────────────────────
        execution = state.get("execution")
        if _exec_status(execution) == "UNKNOWN":
            return respond_template("UNKNOWN")

        # ── 3. DENY decision → fixed template ──────────────────────────
        decision = _extract_decision(state)
        if decision.get("decision") == "DENY":
            return respond_template("DENY")

        # ── 4. LLM-driven paths ────────────────────────────────────────
        if _exec_status(execution) == "EXECUTED":
            return self._respond_executed(state, execution)

        if _exec_status(execution) == "FAILED":
            return self._respond_executed_failed(state, execution)

        if decision.get("decision") == "REPLAN" and _has_user_resolver(decision):
            return self._respond_replan_user(state, decision)

        # ── 5. Default: analysis / intent-unclear / non-write ──────────
        return self._respond_analysis(state)

    # ── LLM helpers ────────────────────────────────────────────────────

    def _call_llm(self, system: str, user: str) -> str:
        """Invoke the LLM with a system+user pair, return sanitised text."""
        from langchain_core.messages import HumanMessage, SystemMessage

        try:
            result = self._llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
            text: str = getattr(result, "content", "") or ""
            text = text.strip()
            if len(text) > _MAX_RESPONSE_CHARS:
                text = text[:_MAX_RESPONSE_CHARS] + "…"
            return text
        except Exception:
            return ""

    def _respond_executed(self, state: AgentState, execution: object) -> str:
        """LLM success response: utterance + step summary."""
        utterance_text = _utterance_text(state)
        steps_text = _execution_steps_summary(execution)

        user_prompt = _build_executed_user_prompt(state, execution)

        text = self._call_llm(_EXECUTED_SYSTEM, user_prompt)
        return text or _build_fallback(
            "EXECUTED",
            utterance_text=utterance_text,
            steps=steps_text.split("\n"),
        )

    def _respond_executed_failed(self, state: AgentState, execution: object) -> str:
        """LLM failure explanation: which step failed, what to suggest."""
        utterance_text = _utterance_text(state)
        steps_text = _execution_steps_summary(execution)

        user_prompt = _build_prompt(
            _EXECUTED_SYSTEM,
            utterance=utterance_text,
            execution_summary=f"执行失败:\n{steps_text}",
        )
        text = self._call_llm(_EXECUTED_SYSTEM, user_prompt)
        return text or executed_failed_fallback()

    def _respond_replan_user(self, state: AgentState, decision: dict[str, object]) -> str:
        """LLM explanation with replan_hints labels."""
        utterance_text = _utterance_text(state)
        hints = _replan_hint_labels(decision)
        hints_block = "\n".join(f"- {h}" for h in hints) if hints else "（无具体提示）"

        user_prompt = _build_prompt(
            _REPLAN_USER_SYSTEM,
            utterance=utterance_text,
            replan_hints=hints_block,
        )
        text = self._call_llm(_REPLAN_USER_SYSTEM, user_prompt)
        if not text:
            return respond_template("REPLAN_USER")
        return text

    def _respond_analysis(self, state: AgentState) -> str:
        """LLM analysis response: utterance + reads data summary."""
        utterance_text = _utterance_text(state)
        reads_text = _reads_summary(state)

        user_prompt = _build_prompt(
            _ANALYSIS_SYSTEM,
            utterance=utterance_text,
            reads_data=reads_text,
        )
        text = self._call_llm(_ANALYSIS_SYSTEM, user_prompt)
        return text or respond_template("GENERIC_ERROR")


# ── State-extraction helpers (pure functions, testable in isolation) ────────


def _exec_status(execution: object | None) -> str | None:
    """Extract ``status`` from an ExecutionResult object or dict."""
    if execution is None:
        return None
    if hasattr(execution, "status"):
        return getattr(execution, "status", None)
    if isinstance(execution, dict):
        return execution.get("status")
    return None


def _attr_or_get(obj: object, key: str, default: object = None) -> object:
    """Read *key* from an object (via attr) or dict (via get)."""
    if hasattr(obj, key):
        return getattr(obj, key, default)
    if isinstance(obj, dict):
        return obj.get(key, default)
    return default


def _extract_decision(state: AgentState) -> dict[str, object]:
    """Normalise the decision field to a plain dict (handles Contract/dict)."""
    raw = state.get("decision")
    if raw is None:
        return {}
    if hasattr(raw, "model_dump"):
        return raw.model_dump(mode="json")
    if isinstance(raw, dict):
        return raw
    return {}


def _has_user_resolver(decision: dict[str, object]) -> bool:
    """Return True if any finding in *decision* has ``resolver=user``."""
    findings = decision.get("findings", ()) or ()
    for f in findings:
        resolver = f.get("resolver") if isinstance(f, dict) else getattr(f, "resolver", None)
        if resolver == "user":
            return True
    return False


def _utterance_text(state: AgentState) -> str:
    """Extract utterance text from state (first utterance, or from plan_draft)."""
    utterances = state.get("utterances") or []
    if utterances:
        return " ".join(getattr(u, "text", "") or "" for u in utterances)
    draft = state.get("plan_draft")
    if draft is not None:
        text = getattr(draft, "utterance_text", None) or ""
        if text:
            return text
    return ""


def _execution_steps_summary(execution: object) -> str:
    """Build a compact step summary from ExecutionResult (object or dict)."""
    steps_raw = _attr_or_get(execution, "steps", ()) or ()
    steps: list[str] = []
    for s in steps_raw:
        step_id = _attr_or_get(s, "step_id", "") or ""
        tool = _attr_or_get(s, "tool", "") or ""
        status = _attr_or_get(s, "status", "") or ""
        created = _attr_or_get(s, "created_ids", ()) or ()
        result = _attr_or_get(s, "result", None)
        parts = [f"{step_id} ({tool}): {status}"]
        if created:
            parts.append(f"  新建: {', '.join(str(c) for c in created)}")
        if result is not None:
            truncated = _safe_truncate(json.dumps(result, ensure_ascii=False, default=str), 120)
            parts.append(f"  结果: {truncated}")
        steps.append("\n".join(parts))
    return "\n".join(steps) or "（无步骤信息）"


def _build_executed_user_prompt(state: AgentState, execution: object) -> str:
    """Build the user prompt for the EXECUTED success response."""
    utterance_text = _utterance_text(state)
    steps_text = _execution_steps_summary(execution)
    card_facts_text = _card_facts_block(state)

    return _build_prompt(
        _EXECUTED_SYSTEM,
        utterance=utterance_text,
        card_facts=card_facts_text,
        execution_summary=steps_text,
    )


def _card_facts(state: AgentState) -> dict[str, str]:
    """Build ``{card_id: last4}`` from card data already present in reads.

    get_cards results carry a ``last4`` field alongside the card id (``id`` or
    ``card_id``); this lets the respond prompt give the model the real tail
    instead of leaving it to guess from the card ID.
    """
    facts: dict[str, str] = {}
    for r in state.get("reads") or []:
        data = _attr_or_get(r, "data", None)
        if data is None:
            continue
        _collect_card_facts(data, facts)
    return facts


def _collect_card_facts(data: object, facts: dict[str, str]) -> None:
    """Walk *data* (dict/object) recursively, recording id → last4 pairs."""
    if isinstance(data, list):
        for item in data:
            if (
                isinstance(item, (dict, list))
                or hasattr(item, "model_dump")
                or hasattr(item, "__dict__")
            ):
                _collect_card_facts(item, facts)
        return

    if isinstance(data, dict):
        items = data.items()
    elif hasattr(data, "model_dump"):
        items = data.model_dump(mode="json").items()
    elif hasattr(data, "__dict__"):
        items = data.__dict__.items()
    else:
        return

    last4: str | None = None
    card_id: str | None = None
    for key, value in items:
        if key == "last4" and isinstance(value, str) and len(value) == 4:
            last4 = value
        elif key in ("id", "card_id") and isinstance(value, str):
            card_id = value
    if last4 is not None and card_id is not None:
        facts[card_id] = last4

    for _key, value in items:
        if (
            isinstance(value, (dict, list))
            or hasattr(value, "model_dump")
            or hasattr(value, "__dict__")
        ):
            _collect_card_facts(value, facts)


def _card_facts_block(state: AgentState) -> str:
    """Format card facts as a prompt section (empty string if none).

    Emits only the last4 values (sorted, de-duplicated) — never the card ID,
    which the LLM must not be given a reason to quote as a tail.
    """
    facts = _card_facts(state)
    if not facts:
        return ""
    last4s = sorted(set(facts.values()))
    return "\n".join(f"- 尾号{last4}" for last4 in last4s)


def _replan_hint_labels(decision: dict[str, object]) -> list[str]:
    """Extract human-readable labels from replan_hints."""
    hints = decision.get("replan_hints", ()) or ()
    labels: list[str] = []
    for h in hints:
        if isinstance(h, dict):
            label = h.get("label") or h.get("hint") or ""
        elif hasattr(h, "label"):
            label = getattr(h, "label", "") or ""
        else:
            label = str(h)
        if label:
            labels.append(label)
    return labels


def _reads_summary(state: AgentState) -> str:
    """Build a compact summary of reads data for the analysis prompt."""
    reads = state.get("reads") or []
    if not reads:
        return "（无读取数据）"
    parts: list[str] = []
    for r in reads:
        tool = getattr(r, "tool", "") or ""
        data = getattr(r, "data", None)
        if data is not None:
            text = json.dumps(data, ensure_ascii=False, default=str)
            parts.append(f"{tool}: {_safe_truncate(text, 200)}")
        else:
            parts.append(f"{tool}: (无数据)")
    return "\n".join(parts)


def _safe_truncate(text: str, max_len: int) -> str:
    """Truncate *text* to *max_len* chars, adding '…' if truncated."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "…"


def _build_prompt(
    system_prompt: str,
    **sections: str,
) -> str:
    """Build a user prompt from named *sections* (utterance, execution_summary, …)."""
    lines: list[str] = []
    for key, value in sections.items():
        if not value:
            continue
        lines.append(f"【{key}】\n{value}")
    return "\n\n".join(lines)


def _build_fallback(
    template_key: str,
    **sections: str,
) -> str:
    """Build a deterministic fallback when the LLM fails."""
    if template_key == "EXECUTED":
        parts: list[str] = [executed_fallback()]
        steps = sections.get("steps", [])
        if steps and len(steps[0]) > 5:
            parts.append(f"（{steps[0]}）")
        return "".join(parts)
    return respond_template(template_key)
