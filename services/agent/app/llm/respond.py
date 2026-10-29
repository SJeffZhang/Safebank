"""C1e LLM-driven respond adapter (PRD §6.1, C1e Task 3).

Replaces the old pass-through ``_make_production_respond`` with a state-aware
adapter that:
- Returns pre-set ``reply_draft`` for fixed-template paths (DENY, UNKNOWN, …)
- Calls the LLM for EXECUTED successes, REPLAN_USER hints, and analysis replies
"""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from services.agent.app.graph.state import AgentContext, AgentState
from services.agent.app.llm.fixed_templates import (
    executed_failed_fallback,
    executed_fallback,
    respond_template,
)
from services.agent.app.llm.verify import mask_long_numbers

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

# Deterministic plain-Chinese explanations for REPLAN-user finding codes.
# Never print internal ids/tokens; unknown codes fall back to the generic line.
_REPLAN_USER_CODE_LABELS: dict[str, str] = {
    "INTENT_SCOPE_EXCEEDED": "计划中的付款账户、金额或收款人与您的原话不一致",
    "ENTITY_UNSEEN": "计划引用了尚未读取到的账户、卡或收款人",
    "UNKNOWN_SENSITIVE_ARG": "计划中出现了来源不明的敏感信息",
    "TAINTED_SENSITIVE_ARG": "计划中的敏感信息来源不可信",
    "INTENT_UNCLEAR": "未能明确您的操作意图",
    "PAYEE_NOT_FOUND": "未找到您指定的收款人",
}


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
        """Deterministic finding list (no LLM, no internal ids)."""
        items = _replan_user_items(decision)
        body = "\n".join(f"- {item}" for item in items)
        return respond_template("REPLAN_USER") + "\n" + body

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


def _finding_codes(decision: dict[str, object]) -> list[str]:
    """Extract finding codes from a decision (dict or model findings)."""
    findings = decision.get("findings", ()) or ()
    codes: list[str] = []
    for f in findings:
        code = f.get("code") if isinstance(f, dict) else getattr(f, "code", None)
        if code:
            codes.append(str(code))
    return codes


def _replan_user_items(decision: dict[str, object]) -> list[str]:
    """Deterministic, de-duplicated explanation items for a REPLAN-user decision."""
    items: list[str] = []
    for code in _finding_codes(decision):
        label = _REPLAN_USER_CODE_LABELS.get(code)
        if label:
            items.append(label)
    items.extend(_replan_hint_labels(decision))
    if not items:
        items.append("请补充必要信息后重新发起")
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


# ── Reads summary (C1e fix: compact per-tool summaries, never drop data) ─────

_SHANGHAI = ZoneInfo("Asia/Shanghai")

# Key args shown alongside the tool name so the model can attribute each result
# to the account / date window / product the read was actually about.
_READ_ARG_KEYS = (
    "account_id",
    "product_id",
    "product_ids",
    "month",
    "from_at",
    "to_at",
    "lookback_days",
    "days",
    "status",
    "query",
    "request_id",
)

# Overall budget for the whole reads section. Non-empty blocks are always kept
# ahead of empty ones, so an empty read can never crowd out real data.
_MAX_READS_SUMMARY_CHARS = 6000


def _cents_yuan(value: object) -> str:
    """Format integer cents as a yuan string (``123456`` → ``1234.56元``)."""
    if value is None or value == "":
        return ""
    try:
        cents = int(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{cents // 100}.{cents % 100:02d}元"


def _bp_percent(value: object) -> str:
    """Format basis points as a percentage (``4000`` → ``40.00%``)."""
    if value is None or value == "":
        return ""
    try:
        bp = int(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{bp / 100:.2f}%"


def _fmt_ts(value: object) -> str:
    """Format an ISO timestamp as Asia/Shanghai ``YYYY-MM-DD HH:MM`` (best effort)."""
    if value is None or value == "":
        return ""
    text = str(value)
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_SHANGHAI)
        return dt.astimezone(_SHANGHAI).strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return text


def _item_value(obj: object, key: str, default: object = None) -> object:
    """Read *key* from a dict or object, returning *default* when absent."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    if hasattr(obj, key):
        return getattr(obj, key, default)
    return default


def _as_list(value: object) -> list:
    """Normalise a value that may be a list, tuple or None into a list."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _first_present(obj: object, *keys: str) -> object:
    """First value among *keys* that is present (not None and not '')."""
    for key in keys:
        value = _item_value(obj, key)
        if value is not None and value != "":
            return value
    return None


def _args_label(args: object) -> str:
    """Render the key arguments of a read, e.g. ``(account_id=acc_zs_chk; from_at=2026-09-01 00:00)``."""
    if args is None:
        return ""
    parts: list[str] = []
    for key in _READ_ARG_KEYS:
        value = _item_value(args, key)
        if value is None or value == "":
            continue
        if key in ("from_at", "to_at"):
            parts.append(f"{key}={_fmt_ts(value)}")
        else:
            parts.append(f"{key}={value}")
    if not parts:
        return ""
    return "(" + "; ".join(parts) + ")"


def _summarise_anomalies(data: object) -> tuple[str, bool]:
    """detect_anomalies → count + per item (date, counterparty, amount, codes)."""
    items = _as_list(_item_value(data, "items"))
    if not items:
        return "无异常交易", False
    lines = [f"共 {len(items)} 笔异常"]
    for i, item in enumerate(items, 1):
        tx = _item_value(item, "transaction") or {}
        codes = _item_value(item, "codes") or []
        amount = _cents_yuan(_item_value(tx, "amount_cents"))
        occurred = _fmt_ts(_item_value(tx, "occurred_at"))
        counterparty = _item_value(tx, "counterparty") or ""
        category = _item_value(tx, "category") or ""
        code_text = " ".join(str(c) for c in codes) if codes else ""
        line = f"{i}. {occurred} {amount}"
        if counterparty:
            line += f" 「{counterparty}」"
        if category:
            line += f" [{category}]"
        if code_text:
            line += f" {code_text}"
        lines.append(line)
    return "\n".join(lines), True


def _summarise_spending(data: object) -> tuple[str, bool]:
    """get_spending_summary → totals, month-over-month and category shares."""
    month = _item_value(data, "month") or ""
    total = _item_value(data, "total_cents")
    prev = _item_value(data, "previous_total_cents")
    mom_bp = _item_value(data, "mom_change_bp")
    lines: list[str] = []
    if month:
        lines.append(f"{month} 支出 {_cents_yuan(total)}")
    else:
        lines.append(f"支出 {_cents_yuan(total)}")
    prev_text = _cents_yuan(prev)
    if prev_text:
        if mom_bp is not None and mom_bp != "":
            lines.append(f"上月 {prev_text}，环比 {_bp_percent(mom_bp)}")
        else:
            lines.append(f"上月 {prev_text}")
    categories = _as_list(_item_value(data, "categories"))
    if categories:
        cat_lines = []
        for cat in categories:
            name = _item_value(cat, "category") or "?"
            amount = _cents_yuan(_item_value(cat, "amount_cents"))
            share = _bp_percent(_item_value(cat, "share_bp"))
            cat_lines.append(f"{name} {amount}（{share}）")
        lines.append("分类：" + "；".join(cat_lines))
    return "\n".join(lines), True


def _summarise_transactions(data: object) -> tuple[str, bool]:
    """get_transactions → totals, count and top items."""
    items = _as_list(_item_value(data, "items"))
    income = _item_value(data, "income_cents")
    expense = _item_value(data, "expense_cents")
    lines: list[str] = []
    if income is not None and expense is not None:
        lines.append(f"收入 {_cents_yuan(income)}，支出 {_cents_yuan(expense)}")
    if not items:
        if not lines:
            return "无交易记录", False
        return "\n".join(lines), False
    lines.append(f"共 {len(items)} 笔")
    for i, tx in enumerate(items, 1):
        direction = _item_value(tx, "direction") or ""
        amount = _cents_yuan(_item_value(tx, "amount_cents"))
        occurred = _fmt_ts(_item_value(tx, "occurred_at"))
        counterparty = _item_value(tx, "counterparty") or ""
        memo = _item_value(tx, "memo") or ""
        category = _item_value(tx, "category") or ""
        line = f"{i}. {occurred} {direction} {amount}"
        if counterparty:
            line += f" 「{counterparty}」"
        if category:
            line += f" [{category}]"
        if memo:
            line += f" 备注「{memo}」"
        lines.append(line)
    return "\n".join(lines), True


def _summarise_subscriptions(data: object) -> tuple[str, bool]:
    """list/detect_subscriptions, upcoming_charges → merchant, amount, period, idle flag."""
    items = _as_list(_item_value(data, "items"))
    if not items:
        total_monthly = _item_value(data, "total_monthly_cents")
        total_annual = _item_value(data, "total_annual_cents")
        monthly_text = _cents_yuan(total_monthly)
        annual_text = _cents_yuan(total_annual)
        if monthly_text or annual_text:
            parts = []
            if monthly_text:
                parts.append(f"月费 {monthly_text}")
            if annual_text:
                parts.append(f"年费 {annual_text}")
            return "；".join(parts), True
        return "无订阅", False
    lines = []
    for i, sub in enumerate(items, 1):
        merchant = _first_present(sub, "merchant_name", "name", "merchant_id") or "?"
        amount_value = _first_present(sub, "amount_cents", "typical_amount_cents")
        amount = _cents_yuan(amount_value)
        period = _item_value(sub, "period") or ""
        next_due = _fmt_ts(_first_present(sub, "next_due_at", "due_at", "last_charged_at"))
        is_idle = _item_value(sub, "is_idle")
        line = f"{i}. {merchant} {amount}"
        if period:
            line += f" {period}"
        if next_due:
            line += f" 下次 {next_due}"
        if is_idle is True:
            line += " [疑似闲置]"
        lines.append(line)
    return "\n".join(lines), True


def _summarise_accounts(data: object) -> tuple[str, bool]:
    """get_accounts → label, masked number and balance per account."""
    accounts = _as_list(_item_value(data, "accounts"))
    lines: list[str] = []
    for i, acc in enumerate(accounts, 1):
        name = _first_present(acc, "display_name", "type") or ""
        masked = _item_value(acc, "account_no_masked") or ""
        balance = _item_value(acc, "balance_cents")
        line = f"{i}. {name}" if name else f"{i}."
        if masked:
            line += f" ({masked})"
        if balance is not None:
            line += f" 余额 {_cents_yuan(balance)}"
        lines.append(line)
    if not lines:
        return "无账户", False
    outflow = _item_value(data, "daily_outflow_cents")
    if outflow is not None:
        lines.append(f"当日已转出 {_cents_yuan(outflow)}")
    return "\n".join(lines), True


def _summarise_cards(data: object) -> tuple[str, bool]:
    """get_cards → last4, status and limits per card."""
    items = _as_list(_item_value(data, "items"))
    if not items:
        return "无卡片", False
    lines: list[str] = []
    for i, card in enumerate(items, 1):
        last4 = _item_value(card, "last4") or ""
        status = _item_value(card, "status") or ""
        single = _item_value(card, "single_limit_cents")
        daily = _item_value(card, "daily_limit_cents")
        line = f"{i}. 尾号{last4}" if last4 else f"{i}."
        if status:
            line += f" {status}"
        if single is not None:
            line += f" 单笔限额 {_cents_yuan(single)}"
        if daily is not None:
            line += f" 日限额 {_cents_yuan(daily)}"
        lines.append(line)
    return "\n".join(lines), True


def _summarise_other(data: object) -> tuple[str, bool]:
    """Fallback: raw JSON with a generous cap."""
    text = json.dumps(data, ensure_ascii=False, default=str)
    return _safe_truncate(text, 2000), True


def _summarise_tool(tool: str, data: object) -> tuple[str, bool]:
    """Dispatch a read result to its compact summariser (``(text, has_data)``)."""
    if data is None:
        return "(无数据)", False
    if tool == "detect_anomalies":
        return _summarise_anomalies(data)
    if tool == "get_spending_summary":
        return _summarise_spending(data)
    if tool == "get_transactions":
        return _summarise_transactions(data)
    if tool in ("list_subscriptions", "detect_subscriptions", "upcoming_charges"):
        return _summarise_subscriptions(data)
    if tool == "get_accounts":
        return _summarise_accounts(data)
    if tool == "get_cards":
        return _summarise_cards(data)
    return _summarise_other(data)


def _join_capped(blocks: list[tuple[str, bool]]) -> str:
    """Join blocks under the size cap, keeping non-empty blocks ahead of empty ones."""
    non_empty = [block for block, has_data in blocks if has_data]
    empty = [block for block, has_data in blocks if not has_data]
    parts: list[str] = []
    used = 0
    for block in non_empty + empty:
        size = len(block)
        if used + size > _MAX_READS_SUMMARY_CHARS:
            remaining = _MAX_READS_SUMMARY_CHARS - used
            if not parts and remaining > 20:
                parts.append(_safe_truncate(block, remaining))
            break
        parts.append(block)
        used += size
    return "\n\n".join(parts)


def _reads_summary(state: AgentState) -> str:
    """Build a compact, per-tool summary of reads data for the analysis prompt.

    Unlike the old raw-truncated-JSON version, this keeps the tool's key
    arguments (account, date window, product) and summarises each known tool's
    payload so anomaly items, category shares and subscription rows survive.
    """
    reads = state.get("reads") or []
    if not reads:
        return "（无读取数据）"
    blocks: list[tuple[str, bool]] = []
    for r in reads:
        tool = getattr(r, "tool", "") or ""
        data = getattr(r, "data", None)
        args = getattr(r, "args", None)
        text, has_data = _summarise_tool(tool, data)
        label = _args_label(args)
        header = f"{tool}{label}" if label else tool
        blocks.append((f"{header}:\n{text}", has_data))
    return mask_long_numbers(_join_capped(blocks))


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
