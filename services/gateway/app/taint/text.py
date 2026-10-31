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


def _classify_verbs(verbs: list[str]) -> tuple[frozenset[str], frozenset[str]]:
    """Pure: classify card-control verbs into (true_set, false_set).

    Verbs containing 关/停 go to false; verbs containing 开/启 go to true.
    A verb matching both classes or neither raises ValueError (fail-closed).
    """
    true_set: set[str] = set()
    false_set: set[str] = set()
    for v in verbs:
        is_false = any(c in v for c in ("关", "停"))
        is_true = any(c in v for c in ("开", "启"))
        if is_true and is_false:
            raise ValueError(
                f"Ambiguous card-control verb '{v}': "
                f"matches both true (开/启) and false (关/停) classes"
            )
        if is_true:
            true_set.add(v)
        elif is_false:
            false_set.add(v)
        else:
            raise ValueError(
                f"Unclassifiable card-control verb '{v}': contains neither 开/启 nor 关/停"
            )
    return frozenset(true_set), frozenset(false_set)


def _classify_bool_verbs():
    """Load card-control verbs from intent_lexicon.yaml and classify them."""
    from ..utterance.parser import LEXICON

    return _classify_verbs(LEXICON.get("actions", {}).get("update_card_controls", []))


_BOOL_TRUE_EXTRA, _BOOL_FALSE_EXTRA = _classify_bool_verbs()


def matches(value, text: str, param: str, state, customer_id: str | None = None) -> bool:
    raw = normalize(text)
    if param == "units":
        if not isinstance(value, (str, Decimal)) or not re.fullmatch(
            r"[0-9]{1,14}(?:\.[0-9]{1,4})?(?:份)?", raw
        ):
            return False
        try:
            requested = Decimal(raw.removesuffix("份"))
            actual = Decimal(value)
            return requested > 0 and actual.is_finite() and requested == actual
        except InvalidOperation:
            return False
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
    if isinstance(value, (list, tuple)):
        # A list of entity ids (AA participants) is authorized only when the span
        # names each one exactly once; every name must resolve to one owned entity.
        names = [part for part in re.split(r"[、,，;；/\s]+|和|与|及|跟", raw) if part]
        if (
            not value
            or not all(isinstance(v, str) for v in value)
            or len(names) != len(value)
            or len(set(value)) != len(value)
        ):
            return False
        remaining = list(value)
        for name in names:
            hit = next(
                (eid for eid in remaining if matches(eid, name, param, state, customer_id)), None
            )
            if hit is None:
                return False
            remaining.remove(hit)
        return not remaining
    if isinstance(value, bool):
        # Card-control verbs are classified at module-load time from the single
        # source of truth (intent_lexicon.yaml).  Non-channel booleans (补卡/
        # 不补卡/包括自己/etc) retain their hard-coded entries.
        return raw in (
            {"true", "是", "开启", "打开", "开通", "补卡", "包括自己"} | _BOOL_TRUE_EXTRA
            if value
            else {"false", "否", "关闭", "关掉", "不补卡", "不包括自己"} | _BOOL_FALSE_EXTRA
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
