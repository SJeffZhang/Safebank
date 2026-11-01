"""Narrow read prerequisites for scoped cross-scene tasks; never authorization."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from services.agent.app.graph.state import AgentContext, AgentState, ReadRecord, UtteranceKind

_MONTH = (
    r"(?:(?:(?P<year>20\d{2})年)?(?P<month>0?[1-9]|1[0-2])月|"
    r"(?P<relative>上个月|上月))"
)
_X1 = re.compile(
    r"分析(?:我(?:的)?)?" + _MONTH + r"(?:的)?(?:消费|账单)[,，、;；]?"
    r"(?:找出|查找|找)异常(?:交易)?[,，、;；]?"
    r"(?:并|再|然后)(?:把|将)?(?:我的)?(?:银行卡|卡)(?:的)?单笔限额"
    r"(?:设成|设为|设置为|降低到|调低到|降到)\d+(?:\.\d{1,2})?(?:元)?"
)

_X4 = re.compile(
    r"(?:把(?:这笔|那笔)可疑交易对应的卡冻结|冻结可疑卡)[,，、;；]?"
    r"再从(?:我的)?储蓄(?:账户)?转\d+(?:\.\d{1,2})?(?:元)?到(?:我的)?活期(?:账户)?应急"
)


def scoped_typed_text(state: AgentState) -> str | None:
    """Read selection only: an actual unresolved clarification always wins."""
    understanding = state.get("understanding")
    utterances = state.get("utterances") or []
    if (
        state.get("parse_status") != "SCOPED"
        or state.get("clarification_id")
        or state.get("clarification")
        or state.get("echoes_untrusted")
        or understanding is None
        or understanding.needs_write is not True
        or len(utterances) != 1
        or utterances[0].kind != UtteranceKind.TYPED
    ):
        return None
    text = unicodedata.normalize("NFKC", utterances[0].text).strip(" 。.!！")
    return re.sub(r"^(?:请帮我|帮我|替我|请)\s*", "", text, count=1)


def x1_month_match(state: AgentState) -> re.Match | None:
    text = scoped_typed_text(state)
    return _X1.fullmatch(text) if text is not None else None


def x4_requested(state: AgentState) -> bool:
    text = scoped_typed_text(state)
    return text is not None and _X4.fullmatch(text) is not None


def suspicious_card_period(sim_now: str) -> tuple[str, str]:
    """Match G3 predicate_values: previous natural month start through bank now."""
    now = datetime.fromisoformat(sim_now)
    if now.tzinfo is None:
        raise ValueError("business time must include timezone")
    now = now.astimezone(ZoneInfo("Asia/Shanghai"))
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    previous_start = (month_start - timedelta(days=1)).replace(day=1)
    return previous_start.isoformat(), now.isoformat()


def natural_month_period(match: re.Match, sim_now: str) -> tuple[str, str, str]:
    """Use the freshly read bank clock, including Shanghai year/month boundaries."""
    now = datetime.fromisoformat(sim_now)
    if now.tzinfo is None:
        raise ValueError("business time must include timezone")
    now = now.astimezone(ZoneInfo("Asia/Shanghai"))
    if match["relative"]:
        year, month = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
    else:
        year, month = int(match["year"] or now.year), int(match["month"])
    start = datetime(year, month, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
    end = start.replace(year=year + 1, month=1) if month == 12 else start.replace(month=month + 1)
    return start.strftime("%Y-%m"), start.isoformat(), end.isoformat()


def x1_analysis_records(state: AgentState) -> tuple[ReadRecord, ...]:
    """Expose actual X1 analysis only when the complete same-month evidence exists.

    Missing/ambiguous profile, wrong summary month, truncated anomaly interval, or
    out-of-range anomaly transactions suppress the analysis cards rather than
    inventing totals or claiming that no anomalies were found.
    """
    match = x1_month_match(state)
    if match is None:
        return ()
    reads = state.get("reads") or []
    profiles = [r for r in reads if r.tool == "get_profile"]
    if len(profiles) != 1:
        return ()
    try:
        month, start, end = natural_month_period(match, profiles[0].data["sim_now"])
        summaries = [
            r
            for r in reads
            if r.tool == "get_spending_summary" and r.args == {"month": month, "account_id": None}
        ]
        anomalies = [
            r
            for r in reads
            if r.tool == "detect_anomalies"
            and r.args == {"from_at": start, "to_at": end, "account_id": None}
        ]
        if len(summaries) != 1 or len(anomalies) != 1:
            return ()
        summary = summaries[0].data
        items = anomalies[0].data.get("items")
        if (
            summary.get("month") != month
            or type(summary.get("total_cents")) is not int
            or not isinstance(summary.get("categories"), list)
            or not isinstance(items, list)
        ):
            return ()
        for item in items:
            at = datetime.fromisoformat(item["transaction"]["occurred_at"])
            if at.tzinfo is None or not datetime.fromisoformat(
                start
            ) <= at < datetime.fromisoformat(end):
                return ()
        return summaries[0], anomalies[0]
    except (KeyError, TypeError, ValueError):
        return ()


def current_x3_selection(state: AgentState, context: AgentContext) -> dict | None:
    """Whitelist context from one current G20 action, never an authorization source.

    G20 already validates the runtime customer/session/JWT and live utterance. The
    graph preserves its action scope in hints and its parent chain in parent_ids.
    Arbitrary hint scopes, previous refs and financial amount fields are excluded.
    """
    utterances = state.get("utterances") or []
    parents = state.get("parent_ids") or ()
    hints = state.get("hints") or []
    if (
        state.get("session_id") != context.session_id
        or state.get("customer_id") != context.customer_id
        or state.get("parse_status") != "SCOPED"
        or state.get("clarification")
        or state.get("clarification_id")
        or state.get("echoes_untrusted")
        or len(utterances) != 1
        or utterances[0].kind != UtteranceKind.ACTION
        or utterances[0].text != ""
        or not parents
        or len(hints) != 1
    ):
        return None
    hint = hints[0]
    if hint.get("id") != utterances[0].id or hint.get("kind") != "action":
        return None
    scope = hint.get("scope")
    if not isinstance(scope, dict) or scope.get("action_kinds") != ["FUND_SUBSCRIBE"]:
        return None
    fields = scope.get("fields")
    if (
        not isinstance(fields, dict)
        or fields.get("derived_template") != "available_less_7d_charges"
    ):
        return None
    product = fields.get("product_id")
    if not isinstance(product, str) or re.fullmatch(r"prd_[A-Za-z0-9_-]+", product) is None:
        return None
    selected_fields = {"product_id": product}
    account = fields.get("account_id")
    if account is not None:
        if not isinstance(account, str) or re.fullmatch(r"acc_[A-Za-z0-9_-]+", account) is None:
            return None
        selected_fields["account_id"] = account
    selected_fields["derived_template"] = "available_less_7d_charges"
    return {
        "utterance_id": utterances[0].id,
        "parent_utterance_ids": sorted(parents),
        "action_kinds": ["FUND_SUBSCRIBE"],
        "fields": selected_fields,
    }
