"""Narrow read-only query selectors, never write scopes or entity authorization."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from services.agent.app.graph.state import AgentState, UtteranceKind

SHANGHAI = ZoneInfo("Asia/Shanghai")
MAX_PAGES = 16


def data_text(value: object) -> str:
    text = str(value or "")
    return re.sub(r'<untrusted_data source="[^"<>]*">', "", text).replace("</untrusted_data>", "")


def query(state: AgentState) -> tuple[str, tuple[str, ...]] | None:
    understanding = state.get("understanding")
    utterances = state.get("utterances") or []
    if (
        understanding is None
        or understanding.needs_write
        or len(utterances) != 1
        or utterances[0].kind != UtteranceKind.TYPED
        or state.get("clarification_id")
        or state.get("parse_status") not in ("NO_WRITE", "INTENT_UNCLEAR")
    ):
        return None
    text = unicodedata.normalize("NFKC", utterances[0].text).strip(" 。.!！?？")
    text = re.sub(r"^(?:请帮我|帮我|请)", "", text)
    if re.fullmatch(r"我的卡今天还能刷多少", text):
        return "card_capacity", ()
    if re.fullmatch(r"(?:我|我的)?持仓(?:和|及)在途订单(?:有哪些|情况)", text):
        return "holdings", ()
    if re.fullmatch(r"(?:我有)?哪些定时转账", text):
        return "scheduled", ()
    upcoming = re.fullmatch(r"未来([1-9]|[12]\d|30)天有哪些扣费", text)
    if upcoming:
        return "upcoming", (upcoming[1],)
    comparison = re.fullmatch(
        r"(?:比较|对比)(?:一下)?([^，,。；;]{1,30}?)(?:和|与)([^，,。；;]{1,30})", text
    )
    if comparison:
        return "compare", tuple(part.strip() for part in comparison.groups())
    merchant = re.fullmatch(r"我在([^，,。；;]{1,30})花了多少钱", text)
    if merchant:
        return "merchant", (merchant[1],)
    month = re.fullmatch(
        r"(?:查一下|查询|查看)(?:我|我的)?(?:(20\d{2})年)?(0?[1-9]|1[0-2])月的?消费流水", text
    )
    if month:
        return "transactions", (month[1] or "", month[2])
    return None


def transaction_args(sim_now: str, *, year: str = "", month: str = "") -> dict:
    now = datetime.fromisoformat(sim_now).astimezone(SHANGHAI)
    start = now - timedelta(days=180)
    end = now
    if month:
        y, m = int(year or now.year), int(month)
        start = datetime(y, m, 1, tzinfo=SHANGHAI)
        next_month = datetime(y + (m == 12), 1 if m == 12 else m + 1, 1, tzinfo=SHANGHAI)
        end = min(now, next_month)
        if not 0 < (end - start).total_seconds() <= 180 * 86400:
            raise ValueError("requested transaction period is outside the supported read window")
    return {
        "account_id": None,
        "from_at": start.isoformat(),
        "to_at": end.isoformat(),
        "category": None,
        "merchant_id": None,
        "kind": None,
        "cursor": None,
        "limit": 100,
    }
