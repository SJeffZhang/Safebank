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


# Money is a complete slot, not a numeric substring of an arbitrary sentence.
_CN_NUM = r"[零〇一二两三四五六七八九十百千万]+"
_CN_NUM_VALUE = r"\d+(?:\.\d{1,2})?|" + _CN_NUM
MONEY = r"(?:(?:人民币|CNY)\s*)?(?:" + _CN_NUM_VALUE + r")\s*(?:元|角|分|块钱|块)?"

_CN_DIGIT_V = {
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


def _parse_chinese_numeral(text: str) -> int | None:
    """Parse a strict Chinese numeral (no 零 gaps, no mixed scripts)."""
    if not text or "零" in text:
        return None
    total = 0
    section = 0  # accumulates values under 万
    i = 0
    while i < len(text):
        c = text[i]
        if c in _CN_DIGIT_V:
            digit = _CN_DIGIT_V[c]
            if i + 1 < len(text) and text[i + 1] in {"十", "百", "千", "万"}:
                i += 1
                mult_c = text[i]
                mult_v = {"十": 10, "百": 100, "千": 1000, "万": 10000}[mult_c]
                if mult_c == "万":
                    section = (section + digit) * 10000
                    total += section
                    section = 0
                else:
                    section += digit * mult_v
            else:
                section += digit
        elif c == "十":
            section += 10  # implicit 一
        elif c in {"百", "千"}:
            return None  # 百/千 without preceding digit — ambiguous
        elif c == "万":
            if section == 0:
                return None
            section *= 10000
            total += section
            section = 0
        else:
            return None
        i += 1
    result = total + section
    return result if result > 0 else None


_MONEY_PATTERN = (
    r"(?:(?:人民币|CNY)\s*)?(?P<value>" + _CN_NUM_VALUE + r")\s*(?P<unit>元|角|分|块钱|块)?"
)


def money_cents(text: str) -> int | None:
    match = re.fullmatch(_MONEY_PATTERN, text)
    if match is None:
        return None
    value_str = match["value"]
    if value_str[0] in _CN_DIGIT_V:
        value = _parse_chinese_numeral(value_str)
        if value is None:
            return None
        value = Decimal(value)
    elif not value_str.isdigit():
        return None  # bare multiplier (千/百/etc.) or mixed-script — not a valid amount
    else:
        value = Decimal(value_str)
    grammar = LEXICON["grammar"]
    cents = value * grammar["cents_per_unit"][match["unit"] or grammar["bare_money_unit"]]
    if cents != cents.to_integral_value() or not 0 < cents <= 2**63 - 1:
        return None
    return int(cents)


# Match once so withdrawal recognition and command stripping use identical boundaries.
CORRECTION_PREFIX = re.compile(
    r"^(?:不对(?:[,。;.\s]+(?:改成|改为|换成)\s*|,\s*|$)|(?:改成|改为|换成)\s*)"
)


def _correction_body(text: str) -> str | None:
    text = normalize(text)
    if re.fullmatch(r"(?:改成|改为|换成)(?:是什么意思|什么意思|怎么理解)", text.rstrip(" ?。.!！")):
        return None
    prefix = CORRECTION_PREFIX.match(text)
    return text[prefix.end() :] if prefix is not None else None


def is_correction(text: str) -> bool:
    return _correction_body(text) is not None


def _command_text(text: str) -> str:
    body = _correction_body(text)
    return body if body is not None else normalize(text)


@dataclass
class Command:
    tool: str
    target: str | None = None
    amount_cents: int | None = None
    source: str | None = None
    fields: dict[str, Any] = field(default_factory=dict)
    future: bool = False
    bare: bool = False


def _verbs(tool: str) -> str:
    return (
        "(?:"
        + "|".join(
            re.escape(word) for word in sorted(LEXICON["actions"][tool], key=len, reverse=True)
        )
        + ")"
    )


def _transfer_clause(clause: str) -> Command | None:
    source = None
    if clause.startswith("从"):
        prefix = re.fullmatch(r"从(?P<source>.+?)(?P<body>(?:给|向|转).+)", clause)
        if prefix is None:
            return None
        source, clause = prefix["source"].strip(), prefix["body"]
    patterns = [
        (rf"把\s*(?P<money>{MONEY})\s*(?:转给|转账给)\s*(?P<target>.+)", False),
        (rf"(?:给|向)(?P<target>.+?)(?:转账?|转入)?\s*(?P<money>{MONEY})", False),
        (rf"(?:转账?给|转给|转到|转入)(?P<target>.+?)\s*(?P<money>{MONEY})", False),
        (rf"转(?:账)?\s*(?P<money>{MONEY})(?:到|给)(?P<target>.+?)(?:应急)?", False),
        (rf"(?P<target>.+?)\s*(?P<money>{MONEY})", True),
    ]
    for pattern, bare in patterns:
        match = re.fullmatch(pattern, clause)
        if match:
            amount = money_cents(match["money"])
            if amount is None:
                return None
            return Command(
                "transfer",
                target=match["target"].strip(),
                amount_cents=amount,
                source=source,
                bare=bare,
            )
    return None


def _clause(clause: str) -> Command | None:
    """Each alternative consumes its entire clause, including direction and units."""
    clause = re.sub(r"^(?:然后|并|再)\s*", "", clause, count=1)
    clause = re.sub(r"^(?:请帮我|帮我|替我|请)\s*", "", clause, count=1)
    if re.fullmatch(
        r"(?:(?:看|查看|分析)(?:我|我的)?上月(?:消费|账单)(?:和异常)?|找出异常交易|只查退款|查退款|读备注|(?:改成|改为|换成)(?:是什么意思|什么意思|怎么理解))",
        clause,
    ):
        return Command("read")
    if re.fullmatch(r"把下周要扣的(?:会员费|订阅费)留够", clause):
        return Command("reserve")
    if re.fullmatch(r"(?:取消(?:闲置的?|没用的)订阅|找出(?:闲置的?|没用的)订阅取消掉)", clause):
        return Command("cancel_subscription", target="idle_subscriptions")
    subscription = re.fullmatch(r"取消(?P<merchant>.+?)(?:的)?订阅", clause)
    if subscription:
        return Command("cancel_subscription", target=subscription["merchant"])
    scheduled = re.fullmatch(
        r"(?:把)?(?:省的钱|省下来的钱|省下的钱)每月\s*(?P<day>\d{1,2})\s*[号日](?:自动)?(?:转到|转入|转)(?P<target>.+)",
        clause,
    )
    if scheduled and 1 <= int(scheduled["day"]) <= 31:
        return Command(
            "schedule_transfer",
            target=scheduled["target"],
            future=True,
            fields={
                "anchor_day": int(scheduled["day"]),
                "continuous": True,
                "derived_template": "cancelled_monthly_fees",
                "period": "MONTHLY",
            },
        )
    # Card targets are entire noun slots. Unknown qualifiers never select a card.
    limit = re.fullmatch(
        rf"(?:把)?(?P<target>.+?)(?:的)?(?P<limit>单笔|每日|日)限额(?:设成|设置为|设为|改为|调到)(?P<money>{MONEY})",
        clause,
    )
    if limit:
        amount = money_cents(limit["money"])
        if amount is None:
            return None
        name = "single_limit_cents" if limit["limit"] == "单笔" else "daily_limit_cents"
        return Command("update_card_limits", target=limit["target"], fields={name: amount})
    for tool in ("unfreeze_card", "freeze_card", "report_card_lost"):
        verb = _verbs(tool)
        match = re.fullmatch(rf"{verb}(?P<target>.*)", clause) or re.fullmatch(
            rf"把(?P<target>.+){verb}", clause
        )
        if match:
            target = match["target"] or "卡"
            replace = False
            if tool == "report_card_lost" and target.endswith("并补卡"):
                target, replace = target.removesuffix("并补卡") or "卡", True
            return Command(
                tool,
                target=target,
                fields={"replace": replace} if tool == "report_card_lost" else {},
            )
    control = re.fullmatch(
        r"(?P<verb>关闭|关掉|打开|开启)(?:(?P<target>.+?)的)?(?P<channel>线上|境外|ATM|闪付)(?:渠道)?",
        clause,
        re.IGNORECASE,
    )
    if control:
        name = {"线上": "online", "境外": "overseas", "atm": "atm", "闪付": "quickpass"}[
            control["channel"].lower()
        ]
        return Command(
            "update_card_controls",
            target=control["target"] or "卡",
            fields={name: control["verb"] in ("打开", "开启")},
        )
    derived_fund = re.fullmatch(r"(?:把)?(?:剩下|剩余)的钱(?:买|购买|申购)(?P<product>.+)", clause)
    if derived_fund:
        return Command(
            "subscribe_product",
            target=derived_fund["product"],
            fields={"derived_template": "available_less_7d_charges"},
        )
    fund = re.fullmatch(
        rf"{_verbs('subscribe_product')}(?P<product>.+?)\s*(?P<money>{MONEY})", clause
    )
    if fund:
        amount = money_cents(fund["money"])
        return (
            Command("subscribe_product", target=fund["product"], amount_cents=amount)
            if amount
            else None
        )
    redeem = re.fullmatch(r"赎回(?P<product>.+?)\s*(?P<units>\d+(?:\.\d{1,4})?)份", clause)
    if redeem and Decimal(redeem["units"]) > 0:
        return Command(
            "redeem_product",
            target=redeem["product"],
            fields={"units": format(Decimal(redeem["units"]), ".4f")},
        )
    cancel_schedule = re.fullmatch(r"(?:取消定时|取消自动转账)(?P<target>.+)", clause)
    if cancel_schedule:
        return Command("cancel_scheduled", target=cancel_schedule["target"])
    # Recognized future commands remain partial and always require a complete form.
    future = re.fullmatch(r"(?:明天|后天|定时)(?P<body>.+)", clause)
    monthly = re.fullmatch(r"每月\s*(?P<day>\d{1,2})\s*[号日](?:自动)?(?P<body>.+)", clause)
    if future or monthly:
        match = future or monthly
        transfer = _transfer_clause(match["body"])
        if transfer is not None:
            transfer.future = True
            if monthly:
                if not 1 <= int(monthly["day"]) <= 31:
                    return None
                transfer.tool = "schedule_transfer"
                transfer.fields.update(
                    period="MONTHLY", anchor_day=int(monthly["day"]), continuous=True
                )
            return transfer
    return _transfer_clause(clause)


def _accounts(label: str | None, owned: list) -> list:
    if label is None:
        return [account for account in owned if account.type == "CHECKING"]
    label = normalize(label)
    exact = [
        account
        for account in owned
        if label in {normalize(account.id), normalize(account.display_name)}
    ]
    if exact:
        return exact
    generic = re.fullmatch(r"(?:我的)?(?P<type>储蓄|活期)(?:账户)?", label)
    if generic:
        kind = "SAVINGS" if generic["type"] == "储蓄" else "CHECKING"
        return [account for account in owned if account.type == kind]
    return []


def _card_candidates(label: str, cards: list, predicates: dict[str, list[str]]) -> list:
    label = normalize(label)
    if label in {"卡", "我的卡", "银行卡", "我的银行卡"}:
        return cards
    if label in {"可疑卡", "这笔可疑交易对应的卡", "那笔可疑交易对应的卡"}:
        return [card for card in cards if card.id in predicates.get("suspicious_cards", [])]
    return [
        card
        for card in cards
        if label in {card.id, "尾号" + card.last4, "尾号" + card.last4 + "的卡"}
    ]


def _payees(label: str, payees: list) -> list:
    label = normalize(label)
    exact = [
        payee for payee in payees if label in {normalize(payee.name), normalize(payee.alias or "")}
    ]
    if exact:
        return exact
    return [
        payee
        for payee in payees
        if near_name(payee.name, label) or (payee.alias and near_name(payee.alias, label))
    ]


def scope_is_complete(scope: NormalizedScope) -> bool:
    """A slot answer cannot manufacture fields that were never authorized."""
    fields = scope.fields
    if not scope.action_kinds or fields.get("missing_fields") or fields.get("pending_tool"):
        return False
    for kind in scope.action_kinds:
        if kind in {"TRANSFER_EXTERNAL", "TRANSFER_SELF", "SCHEDULE_CREATE"}:
            if (
                not scope.from_account_id
                or not scope.transfers
                or any(
                    item.amount_cents <= 0 or item.from_account_id != scope.from_account_id
                    for item in scope.transfers
                )
            ):
                return False
        if kind == "SCHEDULE_CREATE" and not (fields.get("first_run_at") and scope.period):
            return False
        if kind.startswith("CARD_") and not fields.get("card_id"):
            return False
        if kind in {"FUND_SUBSCRIBE", "FUND_REDEEM"} and not (
            fields.get("product_id") and fields.get("account_id")
        ):
            return False
        if (
            kind == "FUND_SUBSCRIBE"
            and fields.get("derived_template") != "available_less_7d_charges"
        ):
            amount = fields.get("amount_cents")
            if type(amount) is not int or not 0 < amount <= 2**63 - 1:
                return False
        if kind == "FUND_REDEEM":
            units = fields.get("units")
            if (
                not isinstance(units, str)
                or not re.fullmatch(r"\d{1,14}(?:\.\d{1,4})?", units)
                or Decimal(units) <= 0
            ):
                return False
        if kind == "MANDATE_CANCEL" and not (
            fields.get("instruction_ids") or fields.get("instruction_id")
        ):
            return False
        if kind == "SCHEDULE_CANCEL" and not fields.get("instruction_id"):
            return False
    return True


def parse(
    text: str, state: BankState, customer_id: str, predicates: dict[str, list[str]] | None = None
) -> Parsed:
    """Full-clause grammar; unknown text invalidates the entire write proposal."""
    text = normalize(text)
    if text.strip(" 。.!！") in LEXICON["cancel"]:
        return Parsed(status=ParseStatus.NO_WRITE, cancel=True)
    correction = is_correction(text)
    text = _command_text(text)
    clean = QUOTES.sub("", text).strip(" 。.!！")
    if not clean:
        return Parsed() if correction else Parsed(status=ParseStatus.NO_WRITE)
    clauses = [part.strip() for part in re.split(r"[，,；;。]", clean)]
    if not all(clauses):
        return Parsed()
    commands = [_clause(clause) for clause in clauses]
    if any(command is None for command in commands):
        return Parsed()
    writes = [command for command in commands if command.tool not in {"read", "reserve"}]
    if not writes:
        return Parsed(status=ParseStatus.NO_WRITE)
    if any(command.bare for command in writes) and not (
        len(writes) >= 2 and all(command.tool == "transfer" for command in writes)
    ):
        return Parsed()
    owned = [account for account in state.accounts.values() if account.customer_id == customer_id]
    cards = [
        card
        for card in state.cards.values()
        if card.account_id in {account.id for account in owned}
    ]
    payees = [payee for payee in state.payees.values() if payee.customer_id == customer_id]
    result = Parsed()
    fields: dict[str, Any] = {}
    kinds: list[str] = []
    transfers: list[ScopeTransfer] = []
    sources: set[str] = set()
    predicates = predicates or {}
    period = None
    future = False
    transfer_count = sum(command.tool in {"transfer", "schedule_transfer"} for command in writes)

    def put(name: str, value: Any) -> bool:
        if name in fields and fields[name] != value:
            return False
        fields[name] = value
        return True

    def missing(name: str, candidates: list[dict[str, str]]) -> bool:
        if name in result.required_fields or not candidates:
            return False
        result.required_fields.append(name)
        result.candidates.extend(candidates)
        return True

    for command in writes:
        tool = command.tool
        if tool in {"transfer", "schedule_transfer", "subscribe_product", "redeem_product"}:
            accounts = _accounts(command.source, owned)
            if not accounts:
                return Parsed()
            source = accounts[0].id if len(accounts) == 1 else None
            if source:
                sources.add(source)
                if len(sources) > 1:
                    return Parsed()  # Frozen scope has one common source slot.
            elif not missing(
                "from_account_id",
                [_candidate("from_account_id", a.id, a.display_name) for a in accounts],
            ):
                return Parsed()
        if tool in {"transfer", "schedule_transfer"}:
            amount = command.amount_cents
            if tool == "schedule_transfer" and command.fields.get("derived_template"):
                if not any(
                    item.tool == "cancel_subscription"
                    for item in commands[: commands.index(command)]
                ):
                    return Parsed()
                amount = 0
            targets = _accounts(command.target, owned)
            if targets:
                if len(targets) != 1 or targets[0].id == source:
                    return Parsed()
                target = targets[0]
                transfers.append(
                    ScopeTransfer(
                        target_fingerprint="int:" + target.id,
                        amount_cents=amount,
                        from_account_id=source,
                    )
                )
                if not put("to_account_id", target.id):
                    return Parsed()
                kinds.append(action_kind(tool, {"to_account_id": target.id}, state).value)
            else:
                matches = _payees(command.target, payees)
                if not matches:
                    return Parsed()
                exact = [
                    payee
                    for payee in matches
                    if normalize(command.target)
                    in {normalize(payee.name), normalize(payee.alias or "")}
                ]
                if len(exact) == 1:
                    transfers.append(
                        ScopeTransfer(
                            target_fingerprint=exact[0].target_fingerprint,
                            amount_cents=amount,
                            from_account_id=source,
                        )
                    )
                else:
                    if (
                        transfer_count > 1
                        or not put("amount_cents", amount)
                        or not missing(
                            "payee_id", [_candidate("payee_id", p.id, p.name) for p in matches]
                        )
                    ):
                        return Parsed()
                kinds.append(action_kind(tool, {"to_account_id": None}, state).value)
            future |= command.future
            if command.future:
                put("missing_fields", ["first_run_at"])
            for key, value in command.fields.items():
                if key == "period":
                    period = value
                elif not put(key, value):
                    return Parsed()
        elif tool in {
            "freeze_card",
            "unfreeze_card",
            "report_card_lost",
            "update_card_limits",
            "update_card_controls",
        }:
            selected = _card_candidates(command.target, cards, predicates)
            if not selected:
                return Parsed()
            if command.target in {"可疑卡", "这笔可疑交易对应的卡", "那笔可疑交易对应的卡"}:
                fields.setdefault("predicates", {})["suspicious_cards"] = [
                    card.id for card in selected
                ]
            card_id = selected[0].id if len(selected) == 1 else None
            if card_id:
                if not put("card_id", card_id):
                    return Parsed()
            elif not missing(
                "card_id",
                [_candidate("card_id", card.id, "尾号" + card.last4) for card in selected],
            ):
                return Parsed()
            for key, value in command.fields.items():
                if not put(key, value):
                    return Parsed()
            if tool in {"update_card_limits", "update_card_controls"}:
                names = (
                    ("single_limit_cents", "daily_limit_cents")
                    if tool == "update_card_limits"
                    else ("online", "overseas", "atm", "quickpass")
                )
                args = {name: command.fields.get(name) for name in names} | {"card_id": card_id}
                if card_id is None:
                    if not put("pending_tool", tool):
                        return Parsed()
                    continue
            else:
                args = {"card_id": card_id, **command.fields}
            kinds.append(action_kind(tool, args, state).value)
        elif tool == "cancel_subscription":
            if command.target == "idle_subscriptions":
                ids = predicates.get("idle_subscriptions", [])
                fields.setdefault("predicates", {})["idle_subscriptions"] = ids
            else:
                ids = [
                    item.instruction_id
                    for item in state.subscriptions
                    if normalize(item.merchant_name) == normalize(command.target)
                ]
            if not ids or not put("instruction_ids", list(ids)):
                return Parsed()
            kinds.append(action_kind(tool, {}, state).value)
        elif tool in {"subscribe_product", "redeem_product"}:
            if command.target == "稳健型理财":
                selected = [
                    product
                    for product in state.products
                    if product.id in predicates.get("robust_products", [])
                ]
                fields.setdefault("predicates", {})["robust_products"] = [
                    product.id for product in selected
                ]
            else:
                selected = [
                    product
                    for product in state.products
                    if normalize(command.target) in {normalize(product.id), normalize(product.name)}
                ]
            if not selected:
                return Parsed()
            if len(selected) == 1:
                if not put("product_id", selected[0].id):
                    return Parsed()
            elif not missing(
                "product_id",
                [_candidate("product_id", product.id, product.name) for product in selected],
            ):
                return Parsed()
            if not put("account_id", source):
                return Parsed()
            if command.fields.get("derived_template") and not any(
                item.tool == "reserve" for item in commands[: commands.index(command)]
            ):
                return Parsed()
            for key, value in command.fields.items():
                if not put(key, value):
                    return Parsed()
            if command.amount_cents is not None and not put("amount_cents", command.amount_cents):
                return Parsed()
            kinds.append(action_kind(tool, {}, state).value)
        elif tool == "cancel_scheduled":
            selected = [
                item
                for item in state.instructions
                if normalize(command.target) in {normalize(item.id), normalize(item.target_display)}
            ]
            if len(selected) != 1 or not put("instruction_id", selected[0].id):
                return Parsed()
            kinds.append(action_kind(tool, {}, state).value)
    if len(result.required_fields) > 1 or (transfer_count > 1 and result.required_fields):
        return Parsed()  # No indexed slots exist in the frozen clarification model.
    source = next(iter(sources), None)
    if source is None and not result.required_fields:
        defaults = _accounts(None, owned)
        source = defaults[0].id if len(defaults) == 1 else None
    result.scope = NormalizedScope(
        action_kinds=tuple(dict.fromkeys(kinds)),
        from_account_id=source,
        transfers=tuple(transfers),
        period=period,
        fields=fields,
    )
    if future:
        result.status = ParseStatus.INTENT_UNCLEAR
        result.required_fields = []
        result.candidates = []
    elif result.required_fields:
        result.status = ParseStatus.CLARIFYING
    elif scope_is_complete(result.scope):
        result.status = ParseStatus.SCOPED
    else:
        return Parsed()
    return result
