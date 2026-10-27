"""Conservative scalar normalization; no LLM interpretation of source text."""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation


def normalize(value: str) -> str:
    return "".join(
        c
        for c in unicodedata.normalize("NFKC", value)
        if not c.isspace() and unicodedata.category(c) != "Cf"
    )


def numbers(value: str) -> frozenset[str]:
    return frozenset(re.findall(r"[0-9]{8,}", normalize(value)))


def matches(value, text: str, param: str, state, customer_id: str | None = None) -> bool:
    raw = normalize(text)
    if param.endswith("_cents"):
        # User amounts are yuan. No float conversion or rounding is permitted.
        raw = re.sub(r"^(人民币|CNY|RMB|¥|￥)", "", raw, flags=re.I)
        raw = re.sub(r"(元|块钱|块)$", "", raw)
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", raw):
            # Fallback: reuse G3 parser's Chinese numeral support.  Colloquial
            # abbreviations (三千五, 两万五) return None and stay unknown.
            from ..utterance.parser import money_cents as parse_money

            parsed = parse_money(text)
            return parsed is not None and parsed == value
        try:
            return Decimal(raw) * 100 == value
        except InvalidOperation:
            return False
    if isinstance(value, bool):
        # Load card-control verbs from the single source of truth (intent_lexicon.yaml)
        # so provenance boolean matching stays in sync with G3 parser recognition.
        # Non-channel booleans (补卡/不补卡/包括自己/etc) retain their hard-coded entries.
        from ..utterance.parser import LEXICON

        controls = LEXICON.get("actions", {}).get("update_card_controls", [])
        true_extra = {v for v in controls if v not in ("关闭", "关掉")}
        false_extra = {v for v in controls if v in ("关闭", "关掉")}
        return raw in (
            {"true", "是", "开启", "打开", "开通", "补卡", "包括自己"} | true_extra
            if value
            else {"false", "否", "关闭", "关掉", "不补卡", "不包括自己"} | false_extra
        )
    if isinstance(value, str):
        if normalize(value) == raw:
            return True
        # Human names can resolve only to owned, unique entities; ownership and
        # exact intent matching are separate mandatory stages.
        candidates = []
        for group in (state.accounts.values(), state.payees.values(), state.products):
            for item in group:
                if hasattr(item, "customer_id") and item.customer_id != customer_id:
                    continue
                labels = [
                    getattr(item, "name", None),
                    getattr(item, "alias", None),
                    getattr(item, "display_name", None),
                ]
                if any(label is not None and normalize(label) == raw for label in labels):
                    candidates.append(item.id)
        return candidates == [value]
    return False
