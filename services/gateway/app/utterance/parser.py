"""Conservative deterministic parsing: no model output supplies authorization."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from pypinyin import lazy_pinyin
from safebank_contracts.state import BankState

from ..policy.registry import action_kind
from ..schemas.enums import ParseStatus
from ..schemas.scope import NormalizedScope, ScopeTransfer

LEXICON = yaml.safe_load(
    (Path(__file__).resolve().parents[2] / "policies/intent_lexicon.yaml").read_text()
)
QUOTES = re.compile(r'“[^”]*”|「[^」]*」|『[^』]*』|《[^》]*》|"[^"]*"|\'[^\']*\'')
NUMBER = r"(?<![\d.])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?(?![\d.])"


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).strip()
    return re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", value)


def contains(text: str, words: list[str]) -> bool:
    return any(word in text for word in words)


def numbers(text: str) -> set[str]:
    return set(re.findall(r"(?<!\d)\d{8,}(?!\d)", re.sub(r"[\s-]", "", normalize(text))))


def echoes_untrusted(
    text: str, state: BankState, customer_id: str, free_text: tuple[str, ...] = ()
) -> bool:
    from datetime import timedelta

    trusted_accounts = {p.account_no for p in state.payees.values() if p.customer_id == customer_id}
    incoming = numbers(text) - trusted_accounts
    if not incoming:
        return False
    texts = list(free_text)
    for tx in state.transactions:
        if (
            tx.customer_id == customer_id
            and state.sim_now - timedelta(days=90) <= tx.occurred_at <= state.sim_now
        ):
            texts.extend([tx.memo or "", tx.counterparty])
    texts.extend(i.target_display for i in state.instructions)
    return bool(incoming & set().union(*(numbers(value) for value in texts)))


@dataclass
class Parsed:
    scope: NormalizedScope = field(default_factory=lambda: NormalizedScope(action_kinds=()))
    status: ParseStatus = ParseStatus.INTENT_UNCLEAR
    cancel: bool = False
    required_fields: list[str] = field(default_factory=list)
    candidates: list[dict[str, str]] = field(default_factory=list)


def near_name(left: str, right: str) -> bool:
    left, right = normalize(left), normalize(right)
    table = str.maketrans(LEXICON["phonetic"]["confusables"])
    return left.translate(table) == right.translate(table) or bool(
        LEXICON["phonetic"]["pinyin"] and lazy_pinyin(left) == lazy_pinyin(right)
    )


def _candidate(field_name: str, entity_id: str, label: str) -> dict[str, str]:
    return {"field": field_name, "entity_id": entity_id, "label": label}


def _amount(text: str) -> int | None:
    found = list(re.finditer(NUMBER, text))
    # Account numbers and date markers are not monetary slots.
    found = [
        m
        for m in found
        if len(m.group().replace(",", "")) < 8
        and not re.match(r"\s*(?:号|日|天|月|年)", text[m.end() :])
    ]
    if len(found) != 1:
        return None
    match = found[0]
    if (match.start() and text[match.start() - 1] in "-−") or re.match(
        r"[万千百亿]", text[match.end() :]
    ):
        return None
    value = Decimal(match.group().replace(",", "")) * 100
    return int(value) if value > 0 and value <= 2**63 - 1 else None


def parse(
    text: str, state: BankState, customer_id: str, predicates: dict[str, list[str]] | None = None
) -> Parsed:
    """Scope only fully recognized commands; missing entity slots get L2 candidates."""
    text = normalize(text)
    clean = QUOTES.sub("", text)
    if re.search(r"(?:美元|港币|美金|USD|HKD)", clean, re.IGNORECASE):
        return Parsed()
    result = Parsed()
    if clean.strip(" 。.!！") in LEXICON["cancel"]:
        result.cancel = True
        result.status = ParseStatus.NO_WRITE
        return result
    if re.search(
        r"(?:不要|别|勿|不准|不转|不想|不同意|不能|不允许|不用|拒绝|取消这笔|如果|只有|除非|假如|否则)",
        clean,
    ):
        return result
    imperative = bool(re.search(r"(?:请(?:把|转|给|冻|买)|帮我|替我|把|给我)", clean))
    if contains(clean, LEXICON["question"]) and not imperative:
        return result
    # Historical facts, inbound receipts and read requests are not imperatives.
    read_or_past = re.search(
        r"(?:昨天|前天|上周|曾经|已经|刚才|之前|查询|查看|看看|详情|建议|举例|说明)", clean
    )
    explicit_followup = re.search(r"(?:并把|并给|然后|再从|订阅.*取消|取消.*订阅)", clean)
    if read_or_past and not explicit_followup:
        return Parsed(status=ParseStatus.NO_WRITE)
    if any(
        re.search(re.escape(normalize(payee.name)) + r"\s*(?:给我|向我|转给我)", clean)
        for payee in state.payees.values()
        if payee.customer_id == customer_id
    ):
        return Parsed(status=ParseStatus.NO_WRITE)
    # Unbalanced quoting is ambiguous, never a permission to parse its contents.
    if any(char in clean for char in "“”「」『』《》\"'"):
        return result
    own = [a for a in state.accounts.values() if a.customer_id == customer_id]
    checking = [a for a in own if a.type == "CHECKING"]
    kinds: list[str] = []
    fields: dict[str, Any] = {}
    transfers: list[ScopeTransfer] = []
    source = checking[0].id if len(checking) == 1 else None
    target_account = None
    if "从储蓄" in clean:
        source_candidates = [a for a in own if a.type == "SAVINGS"]
        source = source_candidates[0].id if len(source_candidates) == 1 else None
    if re.search(r"(?:到|转|转到|转入)(?:我的)?储蓄", clean):
        targets = [a for a in own if a.type == "SAVINGS"]
        target_account = targets[0].id if len(targets) == 1 else None
    if re.search(r"(?:到|转到|转入)(?:我的)?活期", clean):
        targets = checking
        target_account = targets[0].id if len(targets) == 1 else None
    matched_predicates = {}
    for key, words in LEXICON["predicates"].items():
        if contains(clean, words):
            matched_predicates[key] = list((predicates or {}).get(key, []))
    if matched_predicates:
        fields["predicates"] = matched_predicates

    def missing(name, candidates):
        if name not in result.required_fields:
            result.required_fields.append(name)
            result.candidates.extend(candidates)

    cards = [c for c in state.cards.values() if c.account_id in {a.id for a in own}]
    card_tools = [
        tool
        for tool in (
            "unfreeze_card",
            "freeze_card",
            "report_card_lost",
            "update_card_limits",
            "update_card_controls",
        )
        if contains(clean, LEXICON["actions"][tool])
    ]
    if "unfreeze_card" in card_tools and "freeze_card" in card_tools:
        card_tools.remove("freeze_card")
    if card_tools:
        selected_cards = cards
        if "suspicious_cards" in matched_predicates:
            selected_cards = [c for c in cards if c.id in matched_predicates["suspicious_cards"]]
        else:
            explicit = [c for c in cards if c.id in clean or c.last4 in clean]
            if explicit:
                selected_cards = explicit
        if not selected_cards:
            return result
        if len(selected_cards) == 1:
            fields["card_id"] = selected_cards[0].id
        else:
            missing(
                "card_id", [_candidate("card_id", c.id, "尾号" + c.last4) for c in selected_cards]
            )
        for tool in card_tools:
            args = {"card_id": fields.get("card_id")}
            if tool == "update_card_limits":
                # Only take the clause containing 限额 (X-1 includes other digits).
                clause = next((v for v in re.split(r"[，,。；;]", clean) if "限额" in v), clean)
                amount = _amount(clause)
                if amount is None:
                    return Parsed()
                field_name = (
                    "daily_limit_cents"
                    if "每日" in clause or "日限额" in clause
                    else "single_limit_cents"
                )
                fields[field_name] = amount
                args[field_name] = amount
                args[
                    "daily_limit_cents"
                    if field_name == "single_limit_cents"
                    else "single_limit_cents"
                ] = None
            if tool == "update_card_controls":
                channel_names = {
                    "online": "线上",
                    "overseas": "境外",
                    "atm": "ATM",
                    "quickpass": "闪付",
                }
                selected = [
                    key for key, word in channel_names.items() if word.lower() in clean.lower()
                ]
                if len(selected) != 1:
                    return Parsed()
                field_name = selected[0]
                enabled = contains(clean, ["开启", "打开"])
                fields[field_name] = enabled
                args.update({key: enabled if key == field_name else None for key in channel_names})
            if tool == "report_card_lost":
                fields["replace"] = "补卡" in clean
            if tool in ("update_card_limits", "update_card_controls") and args["card_id"] is None:
                # Direction cannot be guessed before a card is selected.
                fields["pending_tool"] = tool
            else:
                kinds.append(action_kind(tool, args, state).value)

    cancel_sub = contains(clean, LEXICON["actions"]["cancel_subscription"]) or (
        "订阅" in clean and "取消" in clean
    )
    if cancel_sub:
        kinds.append("MANDATE_CANCEL")
        ids = matched_predicates.get("idle_subscriptions")
        if ids is None:
            ids = [s.instruction_id for s in state.subscriptions if s.merchant_name in clean]
        fields["instruction_ids"] = ids
        if not ids:
            return Parsed()
    if contains(clean, LEXICON["actions"]["cancel_scheduled"]):
        kinds.append("SCHEDULE_CANCEL")
        ids = [i.id for i in state.instructions if i.id in clean or i.target_display in clean]
        if len(ids) != 1:
            missing(
                "instruction_id",
                [_candidate("instruction_id", i.id, i.target_display) for i in state.instructions],
            )
        else:
            fields["instruction_id"] = ids[0]

    fund = contains(clean, LEXICON["actions"]["subscribe_product"])
    redeem = contains(clean, LEXICON["actions"]["redeem_product"])
    if fund or redeem:
        kinds.append("FUND_REDEEM" if redeem else "FUND_SUBSCRIBE")
        products = [p for p in state.products if p.id in clean or p.name in clean]
        allowed_ids = matched_predicates.get("robust_products")
        if allowed_ids is not None:
            products = [p for p in state.products if p.id in allowed_ids]
        if not products:
            return Parsed()
        if len(products) == 1:
            fields["product_id"] = products[0].id
        else:
            missing("product_id", [_candidate("product_id", p.id, p.name) for p in products])
        fields["account_id"] = source
        if "剩下" in clean or "剩余" in clean:
            fields["derived_template"] = "available_less_7d_charges"
        else:
            amount = _amount(clean)
            if amount is None:
                return Parsed()
            fields["units" if redeem else "amount_cents"] = (
                str(Decimal(amount) / 100) if redeem else amount
            )

    period = next(
        (key for key, words in LEXICON["periods"].items() if contains(clean, words)), None
    )
    schedule = period in ("MONTHLY", "WEEKLY") and "转" in clean
    # A cancel command may contain 转账: it must not create a new transfer.
    has_transfer = "SCHEDULE_CANCEL" not in kinds and (
        contains(clean, LEXICON["actions"]["transfer"])
        or bool(re.search(r"[\u4e00-\u9fff]{2}\s*\d+\s*[，,]", clean))
    )
    if has_transfer:
        if source is None:
            missing(
                "from_account_id",
                [_candidate("from_account_id", a.id, a.display_name) for a in own],
            )
        derived = cancel_sub and contains(clean, ["省下", "省的", "省的钱"])
        if derived:
            fields["derived_template"] = "cancelled_monthly_fees"
        if target_account:
            amount = _amount(clean)
            if amount is None and not derived:
                return Parsed()
            target = state.accounts[target_account]
            transfers.append(
                ScopeTransfer(
                    target_fingerprint="int:" + target.id,
                    amount_cents=amount or 0,
                    from_account_id=source,
                )
            )
            fields["to_account_id"] = target_account
            kinds.append("SCHEDULE_CREATE" if schedule else "TRANSFER_SELF")
        else:
            payees = [p for p in state.payees.values() if p.customer_id == customer_id]
            # The target-money pairs are taken from each clause, never Cartesian products.
            clauses = [v for v in re.split(r"[，,；;。]", clean) if _amount(v) is not None]
            for clause in clauses:
                matches = [
                    p
                    for p in payees
                    if normalize(p.name) in clause or (p.alias and normalize(p.alias) in clause)
                ]
                if len(matches) == 1:
                    p = matches[0]
                    transfers.append(
                        ScopeTransfer(
                            target_fingerprint=p.target_fingerprint,
                            amount_cents=_amount(clause),
                            from_account_id=source,
                        )
                    )
                else:
                    spoken = re.search(
                        r"(?:给|向|转给)?\s*([\u4e00-\u9fff]{2,4})\s*" + NUMBER, clause
                    )
                    token = spoken.group(1) if spoken else ""
                    # Exact unique names win before phonetic candidates.
                    candidates = matches or [
                        p for p in payees if token and near_name(p.name, token)
                    ]
                    if candidates:
                        fields["amount_cents"] = _amount(clause)
                        missing(
                            "payee_id", [_candidate("payee_id", p.id, p.name) for p in candidates]
                        )
            if transfers or "payee_id" in result.required_fields:
                kinds.append("SCHEDULE_CREATE" if schedule else "TRANSFER_EXTERNAL")
            elif not cancel_sub:
                return Parsed()
        if schedule:
            fields["anchor_day"] = (
                int(re.search(r"每月\s*(\d+)\s*[号日]", clean).group(1))
                if re.search(r"每月\s*(\d+)\s*[号日]", clean)
                else None
            )
            # PRD requires an explicit end or continuous mandate.
            fields["continuous"] = contains(clean, ["自动", "一直", "持续", "每月", "每周"])
    if not kinds and not result.required_fields:
        if not contains(clean, sum(LEXICON["actions"].values(), [])):
            result.status = ParseStatus.NO_WRITE
        return result
    result.scope = NormalizedScope(
        action_kinds=tuple(dict.fromkeys(kinds)),
        from_account_id=source,
        transfers=tuple(transfers),
        period=period,
        fields=fields,
    )
    result.status = ParseStatus.CLARIFYING if result.required_fields else ParseStatus.SCOPED
    if has_transfer and (
        schedule or contains(clean, ["明天", "后天", "下周", "下月", "每天", "定时"])
    ):
        # A seed's execution time is not a default authorization for a new mandate.
        result.scope = result.scope.model_copy(
            update={"fields": {**fields, "missing_fields": ["first_run_at"]}}
        )
        result.required_fields = []
        result.candidates = []
        result.status = ParseStatus.INTENT_UNCLEAR
    return result
