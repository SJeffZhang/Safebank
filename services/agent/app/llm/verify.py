"""C1e output verification and number masking (PRD §6.7.1, C1e Task 4).

Extracts amounts/percentages/card-last4 from reply text, compares against
facts from state (reads/decision/execution), optionally invokes LLM for a
single repair attempt, and always masks ≥8-digit numbers at the end.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum

from services.agent.app.graph.state import AgentContext, AgentState
from services.agent.app.llm.execution_receipts import execution_receipt
from services.agent.app.llm.fixed_templates import executed_failed_fallback, respond_template

# ── Extraction patterns ─────────────────────────────────────────────────────

# ¥ or ￥ followed by a numeric amount (with optional commas and decimals)
_AMOUNT_CNY_RE = re.compile(r"(¥|￥)\s*([\d,]+(?:\.\d{1,2})?)")

# Bare number + 元/块
_AMOUNT_YUAN_RE = re.compile(r"([\d,]+(?:\.\d{1,2})?)\s*(元|块)")

# Percentage
_PCT_RE = re.compile(r"(?<![\d.+\-−＋－])([+\-−＋－]?\d+(?:\.\d+)?)\s*%")

# Card tail: 尾号 followed by an optional copula (为/是) and an ASCII word
# token.  Valid tails are exactly 4 digits; anything else (card IDs, 5+
# digits, mixed alphanumerics) is extracted with last4=None so it can be
# flagged and scrubbed.
_CARD_LAST4_RE = re.compile(r"尾号(?:为|是)?\s*([A-Za-z0-9_]+)")

# Amount string that should be preserved during masking (contains ¥/元/块)
_AMOUNT_TEXT_RE = re.compile(r"(?:¥|￥)[\d,.]+|[\d,.]+\s*(?:元|块)")

# Digit run: one or more digit groups separated by one or more space/hyphen/tab chars.
# Does NOT rely on \b — CJK characters are \w in Python 3, so \b fails at
# Chinese-digit boundaries.  Total digit count is checked post-match.
_DIGIT_RUN_RE = re.compile(r"\d+(?:[ \t\-]+\d+)*")

# Chinese numeral + 元/块 (e.g. 八百元, 三千块)
_CHINESE_AMOUNT_RE = re.compile(r"([零〇一二两三四五六七八九十百千万亿]+)\s*(元|块)")

# Fullwidth digits (U+FF10-U+FF19) → ASCII
_FULLWIDTH_DIGIT_TABLE = str.maketrans(
    "０１２３４５６７８９",
    "0123456789",
)
# Zero-width characters to strip before masking
_ZERO_WIDTH_RE = re.compile(r"[​-‍⁠﻿]")

# Date pattern: YYYY-MM-DD or 8-digit YYYYMMDD (2020-2099)
_DATE_RE = re.compile(r"(?:19|20)\d{2}[-/]?(?:0[1-9]|1[0-2])[-/]?(?:0[1-9]|[12]\d|3[01])")

# ── Chinese numeral parsing ──────────────────────────────────────────────

_CN_DIGIT: dict[str, int] = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CN_UNIT: dict[str, int] = {"十": 10, "百": 100, "千": 1000}


def _chinese_to_value(text: str) -> int | None:
    """Parse a Chinese numeral (e.g. 三万五千, 一百零五) to an integer.

    Returns None if the string contains unrecognised characters.
    """
    if not text:
        return None

    def _parse_below_wan(seg: str) -> int | None:
        """Parse a segment below 万 (e.g. 三百零五, 十二)."""
        if not seg:
            return 0
        val = 0
        i = 0
        chars = list(seg)
        n = len(chars)
        while i < n:
            c = chars[i]
            if c in _CN_DIGIT:
                d = _CN_DIGIT[c]
                if i + 1 < n and chars[i + 1] in _CN_UNIT:
                    val += d * _CN_UNIT[chars[i + 1]]
                    i += 2
                else:
                    val += d
                    i += 1
            elif c in _CN_UNIT:
                # Leading unit: 十 → 10 (e.g. 十二 = 10 then +2)
                if c == "十":
                    val += 10
                    i += 1
                else:
                    return None
            elif c in ("零", "〇"):
                i += 1
            else:
                return None
        return val

    # 亿 boundary
    yi_parts = text.split("亿", 1)
    yi_val = 0
    rest = text
    if len(yi_parts) == 2:
        yi_v = _parse_below_wan(yi_parts[0])
        if yi_v is None:
            return None
        yi_val = yi_v * 100_000_000
        rest = yi_parts[1]

    # 万 boundary
    wan_parts = rest.split("万", 1)
    wan_val = 0
    after_wan = rest
    if len(wan_parts) == 2:
        wan_v = _parse_below_wan(wan_parts[0])
        if wan_v is None:
            return None
        wan_val = wan_v * 10_000
        after_wan = wan_parts[1]

    remaining = _parse_below_wan(after_wan)
    if remaining is None:
        return None

    # Abbreviated form after 万: 三万五 means 三万五千 (bare digit → 千)
    if len(after_wan) == 1 and after_wan in _CN_DIGIT and _CN_DIGIT[after_wan] in range(1, 10):
        if wan_val > 0:
            remaining *= 1000

    return yi_val + wan_val + remaining


class ExtractedKind(StrEnum):
    AMOUNT = "amount"
    PERCENTAGE = "percentage"
    CARD_LAST4 = "card_last4"


# ── Extraction ──────────────────────────────────────────────────────────────


def extract_amounts(text: str) -> list[dict]:
    """Extract amount references from *text*, converting to cents.

    Handles ¥/￥ prefix, 元/块 suffix, and Chinese numerals (八百元).

    Returns ``[{span, raw, cents, kind}, ...]`` where *span* is (start, end).
    """
    results: list[dict] = []
    for m in _AMOUNT_CNY_RE.finditer(text):
        raw = m.group(0)
        num_str = m.group(2).replace(",", "")
        cents = _yuan_str_to_cents(num_str)
        if cents is not None:
            results.append(
                {"span": m.span(), "raw": raw, "cents": cents, "kind": ExtractedKind.AMOUNT}
            )
    for m in _AMOUNT_YUAN_RE.finditer(text):
        raw = m.group(0)
        num_str = m.group(1).replace(",", "")
        cents = _yuan_str_to_cents(num_str)
        if cents is not None:
            results.append(
                {"span": m.span(), "raw": raw, "cents": cents, "kind": ExtractedKind.AMOUNT}
            )
    for m in _CHINESE_AMOUNT_RE.finditer(text):
        raw = m.group(0)
        value = _chinese_to_value(m.group(1))
        if value is not None:
            cents = value * 100
            results.append(
                {"span": m.span(), "raw": raw, "cents": cents, "kind": ExtractedKind.AMOUNT}
            )
    return results


def extract_percentages(text: str) -> list[dict]:
    """Extract percentage values from *text*.

    Returns ``[{span, raw, value, kind}, ...]``.
    """
    results: list[dict] = []
    for m in _PCT_RE.finditer(text):
        raw = m.group(0)
        try:
            number = m.group(1).replace("−", "-").replace("－", "-").replace("＋", "+")
            value = float(number)
        except ValueError:
            continue
        results.append(
            {"span": m.span(), "raw": raw, "value": value, "kind": ExtractedKind.PERCENTAGE}
        )
    return results


def extract_card_last4(text: str) -> list[dict]:
    """Extract card tail references from *text*.

    Returns ``[{span, raw, last4, kind}, ...]``.  ``last4`` is the 4-digit tail
    for valid references, or ``None`` when the token after 尾号 is not exactly
    4 digits (card ID, overlong, or mixed alphanumeric).
    """
    results: list[dict] = []
    for m in _CARD_LAST4_RE.finditer(text):
        raw = m.group(0)
        token = m.group(1)
        last4: str | None = token if (len(token) == 4 and token.isdigit()) else None
        results.append(
            {"span": m.span(), "raw": raw, "last4": last4, "kind": ExtractedKind.CARD_LAST4}
        )
    return results


def _yuan_str_to_cents(raw: str) -> int | None:
    """Parse a yuan-decimal string to cents (integer)."""
    try:
        yuan = float(raw)
        cents = int(round(yuan * 100))
        # Reject negative or unreasonable amounts
        if cents < 0:
            return None
        return cents
    except (ValueError, OverflowError):
        return None


def _replan_receipt(state: AgentState) -> str:
    """Bind suggested amounts to this decision's original transfer steps.

    Hint labels and unrelated read amounts are never financial evidence for a
    maximum. This is display-only: selecting a hint and authorizing a new plan
    remain gateway operations.
    """
    from pydantic import ValidationError
    from safebank_contracts.plan import PlanDraft

    from services.agent.app.gateway.wire import GatewayDecision
    from services.agent.app.llm.respond import _replan_user_items

    raw = state.get("decision")
    if not isinstance(raw, dict):
        if not hasattr(raw, "model_dump"):
            return respond_template("REPLAN_USER")
        raw = raw.model_dump(mode="json")
    lines = [respond_template("REPLAN_USER")]
    # Reuse only deterministic finding labels; discard every unbound hint label.
    lines.extend("- " + item for item in _replan_user_items({"findings": raw.get("findings", [])}))
    base = "\n".join(lines)
    try:
        decision = GatewayDecision.model_validate(raw)
        plan = PlanDraft.model_validate(state.get("plan_draft"))
    except (ValidationError, TypeError):
        return base
    if (
        decision.plan_id != state.get("plan_id")
        or plan.session_id != state.get("session_id")
        or any(f.effect == "DENY" for f in decision.findings)
    ):
        return base

    shrinkable = {
        "INSUFFICIENT_FUNDS",
        "CARD_SINGLE_LIMIT",
        "CARD_DAILY_LIMIT",
        "SCHEDULED_SHORTFALL",
        "HOLDING_INSUFFICIENT",
        "MIN_PURCHASE_NOT_MET",
    }
    replans = [finding for finding in decision.findings if finding.effect == "REPLAN"]
    if not replans or any(
        finding.resolver != "user" or finding.code not in shrinkable for finding in replans
    ):
        return base

    payees: dict[str, set[str]] = {}
    for read in state.get("reads") or ():
        if _obj_attr(read, "tool") != "list_payees":
            continue
        data = _obj_attr(read, "data")
        items = (data.get("items") if isinstance(data, dict) else _obj_attr(data, "items")) or ()
        if not isinstance(items, (list, tuple)):
            continue
        for item in items:
            payee_id, fingerprint = _obj_attr(item, "id"), _obj_attr(item, "target_fingerprint")
            if isinstance(payee_id, str) and isinstance(fingerprint, str):
                payees.setdefault(payee_id, set()).add(fingerprint)

    original = []
    for index, step in enumerate(plan.steps):
        if step.tool != "transfer":
            continue
        args = step.args
        if args.to_account_id:
            target, kind = "int:" + args.to_account_id, "TRANSFER_SELF"
        elif isinstance(args.payee_id, str) and len(payees.get(args.payee_id, ())) == 1:
            target, kind = next(iter(payees[args.payee_id])), "TRANSFER_EXTERNAL"
        else:
            return base
        original.append((index, kind, args.from_account_id, target, args.amount_cents))
    if not original:
        return base
    step_indices = {step.step_id: index for index, step in enumerate(plan.steps)}
    failed_indices = set()
    for finding in replans:
        index = step_indices.get(finding.step_id)
        if index is None or plan.steps[index].tool != "transfer":
            return base
        failed_indices.add(index)
    now_wall = datetime.now(UTC)
    seen = set()
    for hint in decision.replan_hints:
        try:
            expires = datetime.fromisoformat(hint.expires_at_wall)
        except ValueError:
            continue
        if expires.tzinfo is None or expires.utcoffset() is None or expires <= now_wall:
            continue
        scope = hint.scope
        if (
            set(scope.action_kinds) != {item[1] for item in original}
            or scope.period is not None
            or scope.channel is not None
            or len(scope.transfers) != len(original)
        ):
            continue
        if scope.fields:
            own_targets = {item[3][4:] for item in original if item[1] == "TRANSFER_SELF"}
            if (
                set(scope.fields) != {"to_account_id"}
                or len(own_targets) != 1
                or not isinstance(scope.fields["to_account_id"], str)
                or scope.fields["to_account_id"] not in own_targets
            ):
                continue
        remaining = list(original)
        changes = []
        for transfer in scope.transfers:
            matches = [
                item
                for item in remaining
                if item[3] == transfer.target_fingerprint
                and scope.from_account_id in (None, item[2])
                and transfer.from_account_id in (None, item[2])
                and 0 < transfer.amount_cents <= item[4]
            ]
            if len(matches) != 1:
                break
            item = matches[0]
            remaining.remove(item)
            if transfer.amount_cents < item[4]:
                changes.append((item[0], transfer.amount_cents))
        else:
            if not remaining and {index for index, _ in changes} == failed_indices:
                for index, amount in changes:
                    if (index, amount) in seen:
                        continue
                    seen.add((index, amount))
                    lines.append(
                        f"- 原计划第{index + 1}步转账建议最多{amount // 100}.{amount % 100:02d}元，"
                        "调整金额后仍需您重新确认，本次未执行。"
                    )
    return "\n".join(lines)


# ── Fact set builder ────────────────────────────────────────────────────────


def build_fact_set(state: AgentState) -> dict:
    """Build a fact set from state for output verification.

    Returns ``{kind: [...values...], raw_amounts: [...str...], last4s: [...str...]}``.
    """
    amounts: list[int] = []
    percentages: list[float] = []
    last4s: list[str] = []

    # Walk reads
    for r in state.get("reads") or ():
        data = _obj_attr(r, "data")
        if data is None:
            continue
        _collect_from_data(data, amounts, percentages, last4s)

    # Walk decision preflight balances
    decision = state.get("decision")
    if decision is not None:
        pref = _obj_attr(decision, "preflight")
        if pref is not None:
            balances = _obj_attr(pref, "balances")
            if isinstance(balances, list):
                for b in balances:
                    bc = _obj_attr(b, "balance_cents")
                    if bc is not None:
                        try:
                            amounts.append(int(bc))
                        except (TypeError, ValueError):
                            pass

    # Walk execution steps
    execution = state.get("execution")
    if execution is not None:
        steps = _obj_attr(execution, "steps")
        if steps:
            for s in steps:
                result = _obj_attr(s, "result")
                if result is not None:
                    _collect_from_data(result, amounts, percentages, last4s)

    return {
        "amounts": sorted(set(amounts)),
        "percentages": sorted(set(percentages)),
        "last4s": sorted(set(last4s)),
    }


def _collect_from_data(
    data: object, amounts: list[int], percentages: list[float], last4s: list[str]
) -> None:
    """Walk *data* (dict or object) and collect numeric facts."""
    if isinstance(data, dict):
        items = data.items()
    elif hasattr(data, "model_dump"):
        items = data.model_dump(mode="json").items()
    elif hasattr(data, "__dict__"):
        items = data.__dict__.items()
    else:
        return

    for key, value in items:
        if not isinstance(key, str):
            continue
        # Amount-related fields
        if key.endswith("_cents") and isinstance(value, (int, float)):
            try:
                amounts.append(int(value))
            except (TypeError, ValueError):
                pass
        # Percentage fields (basis points → percent)
        if key.endswith("_bp") and isinstance(value, (int, float)):
            try:
                percentages.append(round(float(value) / 100, 4))
            except (TypeError, ValueError):
                pass
        # Card last4
        if key == "last4" and isinstance(value, str) and len(value) == 4:
            last4s.append(value)
        # Walk nested lists/dicts
        if isinstance(value, (dict, list)):
            _collect_from_data(value, amounts, percentages, last4s)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, (dict, list)) or (
                    hasattr(item, "model_dump") or hasattr(item, "__dict__")
                ):
                    _collect_from_data(item, amounts, percentages, last4s)


def _obj_attr(obj: object, name: str, default: object = None) -> object:
    """Read *name* from a dict or object attribute."""
    if hasattr(obj, name):
        return getattr(obj, name, default)
    if isinstance(obj, dict):
        return obj.get(name, default)
    return default


# ── Fact checking ───────────────────────────────────────────────────────────


def check_against_facts(extracted: list[dict], fact_set: dict) -> list[dict]:
    """Compare extracted values against fact_set, return unverified items.

    Each unverified item is ``{span, raw, kind, reason}``.
    """
    unverified: list[dict] = []
    for item in extracted:
        kind = item.get("kind")
        if kind == ExtractedKind.AMOUNT:
            cents = item.get("cents")
            if not _amount_in_facts(cents, fact_set.get("amounts", [])):
                unverified.append(
                    {
                        "span": item["span"],
                        "raw": item["raw"],
                        "kind": kind,
                        "reason": "amount_not_found",
                    }
                )
        elif kind == ExtractedKind.PERCENTAGE:
            value = item.get("value")
            if not _pct_in_facts(value, fact_set.get("percentages", [])):
                unverified.append(
                    {
                        "span": item["span"],
                        "raw": item["raw"],
                        "kind": kind,
                        "reason": "percentage_not_found",
                    }
                )
        elif kind == ExtractedKind.CARD_LAST4:
            last4 = item.get("last4")
            if last4 is None:
                reason = "card_tail_malformed"
            elif last4 not in fact_set.get("last4s", []):
                reason = "card_not_found"
            else:
                continue
            unverified.append(
                {
                    "span": item["span"],
                    "raw": item["raw"],
                    "kind": kind,
                    "reason": reason,
                }
            )
    return unverified


def _amount_in_facts(cents: int, facts: list[int]) -> bool:
    """Check if *cents* matches any fact (±1 cent rounding tolerance)."""
    if not facts:
        return False
    for f in facts:
        if abs(cents - f) <= 1:
            return True
    return False


def _pct_in_facts(value: float, facts: list[float]) -> bool:
    """Check if *value* matches any fact (±0.1% tolerance)."""
    if not facts:
        return False
    for f in facts:
        if abs(value - f) <= 0.1:
            return True
    return False


# ── Number masking ──────────────────────────────────────────────────────────


def mask_long_numbers(text: str) -> str:
    """Mask ≥8-digit sequences (with optional space/hyphen/tab separators).

    - ISO dates (YYYY-MM-DD, YYYYMMDD) are preserved.
    - Amount strings with explicit currency markers (¥/￥ prefix or 元 suffix)
      are preserved.  This is a deviation from PRD §6.7 "一律" — see ADR-004.
    - All other digit runs ≥8 total digits → ``****`` + last 4 digits.
    - CJK-adjacent and punctuation-adjacent numbers are masked (no \\b dependency).
    - Fullwidth digits (U+FF10-U+FF19) are normalised to ASCII.
    - Zero-width characters (U+200B-U+200D, U+2060, U+FEFF) are stripped.
    - NFKC normalisation handles other compatibility forms.
    """
    if not text:
        return text

    # Preprocess: NFKC → strip zero-width → fullwidth→ASCII
    import unicodedata

    working = unicodedata.normalize("NFKC", text)
    working = _ZERO_WIDTH_RE.sub("", working)
    working = working.translate(_FULLWIDTH_DIGIT_TABLE)

    # Phase 1: identify spans to protect (dates and amounts)
    protected: set[tuple[int, int]] = set()

    for m in _DATE_RE.finditer(working):
        span = m.span()
        if (span[1] - span[0]) in (8, 10):
            protected.add(span)

    for m in _AMOUNT_TEXT_RE.finditer(working):
        protected.add(m.span())

    # Phase 2: find digit runs with ≥8 total digits
    spans: list[tuple[int, int]] = []
    for m in _DIGIT_RUN_RE.finditer(working):
        span = m.span()
        if any(_overlaps(span, p) for p in protected):
            continue
        digit_count = sum(1 for c in m.group() if c.isdigit())
        if digit_count >= 8:
            spans.append(span)

    if not spans:
        return working

    # Phase 3: replace spans from right to left (preserve indices)
    result = list(working)
    for start, end in reversed(spans):
        digits_only = "".join(c for c in working[start:end] if c.isdigit())
        masked = "****" + digits_only[-4:]
        result[start:end] = masked

    return "".join(result)


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return not (a[1] <= b[0] or b[1] <= a[0])


# ── Semantic checks ─────────────────────────────────────────────────────────


def check_semantic_consistency(text: str, state: AgentState) -> list[dict]:
    """Return consistency violations for the reply text.

    E.g. UNKNOWN execution + "已转账" is a violation.
    """
    violations: list[dict] = []
    execution = state.get("execution")
    if execution is None:
        return violations

    exec_status = _obj_attr(execution, "status")
    if exec_status == "UNKNOWN":
        if _contains_any(text, _UNKNOWN_FORBIDDEN):
            violations.append({"kind": "semantic", "reason": "unknown_claims_certainty"})

    if exec_status == "FAILED":
        if _contains_any(text, _FAILED_FORBIDDEN):
            violations.append({"kind": "semantic", "reason": "failed_masquerading_as_success"})

    return violations


# Forbidden phrases when execution status is UNKNOWN
_UNKNOWN_FORBIDDEN = ["已转账", "已扣款", "转账成功", "已完成", "已到账", "交易成功"]

# Forbidden phrases when execution status is FAILED (reply should reflect failure)
_FAILED_FORBIDDEN = ["转账成功", "操作完成", "已到账"]


def _contains_any(text: str, phrases: list[str]) -> bool:
    return any(p in text for p in phrases)


_TRAILING_DECKA_RE = re.compile(r"\s*的卡")


def _scrub_unverified_card_tails(text: str, fact_set: dict) -> str:
    """Replace every unverified ``尾号…`` phrase in *text* with ``该卡``.

    When the phrase is immediately followed by ``的卡``, that ``的卡`` is
    absorbed into the replacement so ``尾号 zs_1 的卡`` becomes ``该卡``
    (not ``该卡 的卡``).  Only CARD_LAST4 items that fail fact-checking are
    touched; amounts and percentages are left for the normal masking path.
    """
    extracted = extract_card_last4(text)
    unverified = check_against_facts(extracted, fact_set)
    card_items = [uv for uv in unverified if uv.get("kind") == ExtractedKind.CARD_LAST4]
    if not card_items:
        return text
    for uv in sorted(card_items, key=lambda u: u["span"][0], reverse=True):
        start, end = uv["span"]
        m = _TRAILING_DECKA_RE.match(text, end)
        if m:
            end = m.end()
        text = text[:start] + "该卡" + text[end:]
    return text


# ── OutputVerifier ──────────────────────────────────────────────────────────


class OutputVerifier:
    """Verify and mask LLM-generated reply text against state facts.

    ``llm`` is optional; when provided, a single repair attempt is made for
    unverified items before fallback.
    """

    def __init__(self, llm: object | None = None) -> None:
        self._llm = llm

    def __call__(self, state: AgentState, _context: AgentContext) -> str:
        # A free-text draft or repair cannot override committed business facts.
        status = _obj_attr(state.get("execution"), "status")
        if status == "EXECUTED":
            return mask_long_numbers(execution_receipt(state))
        if status == "UNKNOWN":
            return mask_long_numbers(respond_template("UNKNOWN"))
        if status == "FAILED":
            return mask_long_numbers(executed_failed_fallback())

        if _obj_attr(state.get("decision"), "decision") == "REPLAN":
            return mask_long_numbers(_replan_receipt(state))

        draft: str = state.get("reply_draft") or ""
        if not draft:
            return "您的请求已收到。"

        # Step 1: extract
        extracted = extract_amounts(draft) + extract_percentages(draft) + extract_card_last4(draft)

        # Step 2: fact set + comparison
        fact_set = build_fact_set(state)
        unverified = check_against_facts(extracted, fact_set)

        # Step 3: semantic consistency
        violations = check_semantic_consistency(draft, state)

        # Step 4: LLM repair if unverified items exist and LLM is available
        text = draft
        if (unverified or violations) and self._llm is not None:
            repaired = self._repair(text, unverified, violations, fact_set)
            if repaired:
                # Re-verify repaired text
                re_extracted = (
                    extract_amounts(repaired)
                    + extract_percentages(repaired)
                    + extract_card_last4(repaired)
                )
                re_unverified = check_against_facts(re_extracted, fact_set)
                if not re_unverified and not check_semantic_consistency(repaired, state):
                    text = repaired

        # Step 4.5: deterministic scrub — an unverified card tail must never
        # reach the user, even when the LLM is absent or its repair failed.
        text = _scrub_unverified_card_tails(text, fact_set)

        # Any failed/empty/invalid repair must not expose known wrong facts.
        remaining = check_against_facts(
            extract_amounts(text) + extract_percentages(text) + extract_card_last4(text), fact_set
        )
        if remaining or check_semantic_consistency(text, state):
            text = respond_template("GENERIC_ERROR")

        # Step 5: always mask long numbers
        masked = mask_long_numbers(text)

        return masked

    def _repair(
        self,
        text: str,
        unverified: list[dict],
        violations: list[dict],
        fact_set: dict,
    ) -> str | None:
        """Ask LLM to repair unverified items — one attempt only."""
        from langchain_core.messages import HumanMessage, SystemMessage

        issues: list[str] = []
        for uv in unverified:
            issues.append(f"- {uv['kind']} {uv['raw']}: {uv['reason']}")
        for v in violations:
            issues.append(f"- {v['kind']}: {v['reason']}")

        fact_lines: list[str] = []
        if fact_set.get("amounts"):
            fact_lines.append(f"金额(分): {fact_set['amounts']}")
        if fact_set.get("percentages"):
            fact_lines.append(f"百分比: {fact_set['percentages']}")
        if fact_set.get("last4s"):
            fact_lines.append(f"卡号尾号: {fact_set['last4s']}")

        system = _REPAIR_SYSTEM
        user = (
            f"原始回复:\n{text}\n\n"
            f"问题:\n{chr(10).join(issues)}\n\n"
            f"已知事实:\n{chr(10).join(fact_lines)}\n\n"
            "请修正回复，只输出修正后的文本。"
        )

        try:
            result = self._llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
            repaired: str = getattr(result, "content", "") or ""
            repaired = repaired.strip()
            return repaired if repaired else None
        except Exception as exc:
            if getattr(exc, "status_code", None) in (402, 403, 429):
                raise
            return None


_REPAIR_SYSTEM = (
    "你是一个银行AI助手的输出校验器。你收到一个包含不准确数字的回复。"
    "请根据已知事实修正回复，只修改有问题的部分，保持格式不变。"
    "只输出修正后的文本，不要加任何解释。"
)
