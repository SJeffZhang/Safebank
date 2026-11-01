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
from safebank_bankcore.reserve import BUSINESS_TIMEZONE
from safebank_contracts.state import BankState

from ..policy.registry import action_kind
from ..schemas.enums import ParseStatus
from ..schemas.scope import NormalizedScope, ScopeTransfer
from .phone import phone_matches, phone_number

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
    guidance_template: str | None = None


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


_PAYEE_NAME_RE = re.compile(r"^[一-鿿·]{1,8}$")
_PAYEE_NAME_META_RE = re.compile(r"是什么|什么意思|怎么|能不能|可以吗|可行吗|行吗|吗|呢|啥|吧|嘛")
_PAYEE_NAME_ACTION_RE = re.compile(r"转|给|冻结|关闭|开通|打开|开启|关掉|取消|赎回|申购|新增|添加")
_PAYEE_NAME_GENERIC_BLACKLIST = frozenset(
    {"收款人", "联系人", "账户", "账号", "卡", "银行卡", "对方"}
)


def _valid_payee_name(name: str) -> bool:
    """Reject non-name payloads masquerading as payee names in add_payee clauses."""
    if not _PAYEE_NAME_RE.fullmatch(name):
        return False
    if _PAYEE_NAME_META_RE.search(name):
        return False
    if _PAYEE_NAME_ACTION_RE.search(name):
        return False
    if name in _PAYEE_NAME_GENERIC_BLACKLIST:
        return False
    return True


def _parse_chinese_numeral(text: str) -> int | None:
    """Parse a strict Chinese numeral (no 零 gaps, no mixed scripts)."""
    if not text or "零" in text:
        return None
    total = 0
    section = 0  # accumulates values under 万
    i = 0
    prev_char = ""
    prev_big_mult = False  # True after digit×千/万 — lone digit is then ambiguous
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
                    prev_big_mult = True
                else:
                    section += digit * mult_v
                    if mult_c in {"千"}:
                        prev_big_mult = True
                    else:
                        prev_big_mult = False
            else:
                if prev_big_mult:
                    return None  # lone digit after 千/万 — ambiguous abbreviation
                section += digit
                prev_big_mult = False
        elif c == "十":
            if prev_char == "十":
                return None  # repeated 十 — not a valid numeral
            section += 10  # implicit 一
            prev_big_mult = False
        elif c in {"百", "千"}:
            return None  # 百/千 without preceding digit — ambiguous
        elif c == "万":
            if section == 0:
                return None
            section *= 10000
            total += section
            section = 0
            prev_big_mult = True
        else:
            return None
        prev_char = c
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
    elif not value_str.isdigit() and not re.fullmatch(r"\d+(?:\.\d{1,2})?", value_str):
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


_QUERY_CLAUSES = tuple(
    re.compile(pattern + r"[?？]?")
    for pattern in (
        r"我有哪些定时转账",
        r"AA收款进度怎么样",
        r"查(?:一下)?我(?:(?:20\d{2}年)?(?:0?[1-9]|1[0-2])月|上个月|上月)的消费流水",
        r"我在[\u4e00-\u9fffA-Za-z0-9·]{1,40}花了多少钱",
        r"我上个月各类消费的占比",
        r"和前一个月比怎么样",
        r"最近有没有异常交易",
        r"我的卡今天还能刷多少",
        r"找出所有周期性扣费",
        r"我有哪些订阅",
        r"一年总共要花多少",
        r"未来[1-9]\d?天有哪些扣费",
        r"哪些订阅是闲置没用的",
        r"我的风险等级是多少",
        r"有哪些理财产品适合我",
        r"我的持仓和在途订单有哪些",
    )
)


def _query_clause(clause: str) -> bool:
    if any(pattern.fullmatch(clause) for pattern in _QUERY_CLAUSES):
        return True
    compare = re.fullmatch(
        r"比较(?:一下)?[\u4e00-\u9fffA-Za-z0-9·]{1,24}和"
        r"[\u4e00-\u9fffA-Za-z0-9·]{1,24}[?？]?",
        clause,
    )
    return bool(
        compare and not re.search(r"申购|赎回|转账|取消|冻结|解冻|挂失|开启|关闭|新增", clause)
    )


def _clause(clause: str) -> Command | None:
    """Each alternative consumes its entire clause, including direction and units."""
    clause = re.sub(r"^(?:然后|并|再)\s*", "", clause, count=1)
    clause = re.sub(r"^(?:请帮我|帮我|替我|请)\s*", "", clause, count=1)
    # These are read-only clauses, not write capabilities. Match the whole
    # clause so a query cannot swallow a trailing operation or manufacture
    # authorization from its dates, product names or numeric filter values.
    if _query_clause(clause):
        return Command("read")
    # Consume the whole report clause. A following write clause is still parsed
    # independently; report keywords cannot hide an unknown action suffix.
    if re.fullmatch(
        r"(?:给我)?(?:出|生成)(?:一份)?(?:(?:20\d{2}年)?(?:0?[1-9]|1[0-2])月|上个月|上月)"
        r"(?:的)?账单报告",
        clause,
    ):
        return Command("read")
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
    lost = re.fullmatch(r"(?P<card>我的?卡|我的银行卡)(?:丢了|丢失了|丢失|丢)", clause)
    if lost:
        return Command("report_card_lost", target=lost["card"], fields={"replace": False})
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
    control_verb = _verbs("update_card_controls")
    _control_verb_named = control_verb.replace("?:", "")  # (?:...) -> (...)
    # 把-patterns: verb before channel ("把<target>开通<channel>...")
    #                verb after channel  ("把<target>的<channel>...关掉")
    control_ba = re.fullmatch(
        rf"把(?P<target>.+?)(?:的)?(?P<verb>{_control_verb_named})(?P<channel>线上|境外|ATM|闪付)(?:取现|支付)?(?:功能|渠道)?",
        clause,
        re.IGNORECASE,
    ) or re.fullmatch(
        rf"把(?P<target>.+?)(?:的)?(?P<channel>线上|境外|ATM|闪付)(?:取现|支付)?(?:功能|渠道)?(?P<verb>{_control_verb_named})",
        clause,
        re.IGNORECASE,
    )
    if control_ba:
        name = {"线上": "online", "境外": "overseas", "atm": "atm", "闪付": "quickpass"}[
            control_ba["channel"].lower()
        ]
        return Command(
            "update_card_controls",
            target=control_ba["target"] or "卡",
            fields={name: control_ba["verb"] in ("打开", "开启", "开通")},
        )
    control = re.fullmatch(
        rf"(?P<verb>{_control_verb_named})(?:(?P<target>.+?)的)?(?P<channel>线上|境外|ATM|闪付)(?:取现|支付)?(?:功能|渠道)?",
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
            fields={name: control["verb"] in ("打开", "开启", "开通")},
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
    cancel_schedule_gei = re.fullmatch(r"取消给(?P<target>.+?)(?:的)?定时转账", clause)
    if cancel_schedule_gei:
        return Command("cancel_scheduled", target=cancel_schedule_gei["target"])
    # add_payee: verb-prefix pattern  (新增收款人李阿姨)
    add_verb = _verbs("add_payee")
    m = re.fullmatch(rf"({add_verb})(?P<name>.+)", clause)
    if m:
        name = m["name"].strip()
        if not _valid_payee_name(name):
            return None
        return Command("add_payee", target=name)
    # add_payee: 把-name-verb pattern  (把李阿姨加为收款人)
    m = re.fullmatch(rf"把(?P<name>.+?)({add_verb})", clause)
    if m:
        name = m["name"].strip()
        if not _valid_payee_name(name):
            return None
        return Command("add_payee", target=name)
    # add_payee property clauses: only valid alongside an add_payee main clause
    m = re.fullmatch(r"(?:账号|卡号)(?P<acct>\d{8,})", clause)
    if m:
        return Command("_payee_attr", fields={"account_no": m["acct"]})
    bank = re.fullmatch(r"(?:开户行(?:是|为)?)?(?P<bank>他行|本行)", clause)
    if bank:
        return Command("_payee_attr", fields={"bank_name": bank["bank"]})
    # Recognized future commands remain partial and always require a complete form.
    # A concrete monthly/weekly anchor, by contrast, is a complete schedule: the
    # anchor is encoded into first_run_at by the parse() loop (the Schedule
    # contract has no anchor_day/weekday field), so it must NOT be marked future.
    future = re.fullmatch(r"(?:明天|后天|定时)(?P<body>.+)", clause)
    monthly = re.fullmatch(r"每月\s*(?P<day>\d{1,2})\s*[号日](?:自动)?(?P<body>.+)", clause)
    weekly = re.fullmatch(
        r"每(?:周|星期|礼拜)(?P<weekday>[一二三四五六日天])(?:自动)?(?P<body>.+)", clause
    )
    if future or monthly or weekly:
        match = future or monthly or weekly
        transfer = _transfer_clause(match["body"])
        if transfer is not None:
            if monthly:
                if not 1 <= int(monthly["day"]) <= 31:
                    return None
                transfer.tool = "schedule_transfer"
                transfer.fields.update(
                    period="MONTHLY", anchor_day=int(monthly["day"]), continuous=True
                )
            elif weekly:
                transfer.tool = "schedule_transfer"
                transfer.fields.update(period="WEEKLY", weekday=weekly["weekday"], continuous=True)
            else:
                transfer.future = True
            return transfer
    split = re.fullmatch(r"(?P<self>我和)?(?P<names>.+?)(?:AA|分摊)", clause)
    if split:
        names = [name for name in re.split(r"[、]", split["names"]) if name]
        if not names:
            return None
        return Command(
            "_split_participants",
            fields={"participants": names, "include_self": bool(split["self"])},
        )
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
    if label in {"卡", "我的卡", "银行卡", "我的银行卡", "我卡", "我的借记卡", "我借记卡"}:
        return cards
    if label in {"可疑卡", "这笔可疑交易对应的卡", "那笔可疑交易对应的卡"}:
        return [card for card in cards if card.id in predicates.get("suspicious_cards", [])]
    return [
        card
        for card in cards
        if label in {card.id, "尾号" + card.last4, "尾号" + card.last4 + "的卡"}
    ]


def _phone_target(label: str) -> str | None:
    return phone_number(re.sub(r"^(?:手机号码|手机号)", "", normalize(label)))


def _exact_payee(label: str, payee) -> bool:
    number = _phone_target(label)
    if number is not None:
        # A phone-looking target must use the saved phone field, never a
        # numeric name/alias or an account-number coincidence.
        return phone_matches(number, payee.phone)
    return normalize(label) in {normalize(payee.name), normalize(payee.alias or "")}


def _payees(label: str, payees: list) -> list:
    label = normalize(label)
    exact = [payee for payee in payees if _exact_payee(label, payee)]
    if exact or _phone_target(label) is not None:
        return exact
    near = [
        payee
        for payee in payees
        if near_name(payee.name, label) or (payee.alias and near_name(payee.alias, label))
    ]
    if near:
        return near
    # A partial registered name is a clarification candidate only. It never
    # satisfies _exact_payee and therefore cannot directly authorize transfer.
    if re.fullmatch(r"[\u4e00-\u9fff]{1,7}", label):
        return [
            payee
            for payee in payees
            if normalize(payee.name).startswith(label)
            or (payee.alias and normalize(payee.alias).startswith(label))
        ]
    return []


_WEEKDAYS = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}


def _first_run_at(
    sim_now, period: str, anchor_day: int | None = None, weekday: str | None = None
) -> str | None:
    """First occurrence of a monthly/weekly anchor strictly after sim_now.

    The Schedule contract carries the anchor only through first_run_at (08:00
    local); there is no anchor_day/weekday field, so a concrete anchor makes a
    complete schedule request rather than a FORM_REQUIRED one.
    """
    import calendar
    from datetime import timedelta

    # L3 PostgreSQL timestamps may arrive in UTC. Calendar anchors and the
    # fixed 08:00 execution time always use the bank's business timezone.
    sim_now = sim_now.astimezone(BUSINESS_TIMEZONE)
    if period == "MONTHLY":
        day = int(anchor_day)
        year, month = sim_now.year, sim_now.month
        if sim_now.day < day:
            if day > calendar.monthrange(year, month)[1]:
                return None
            target = sim_now.replace(day=day)
        else:
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
            if day > calendar.monthrange(year, month)[1]:
                return None
            target = sim_now.replace(year=year, month=month, day=day)
        return target.replace(hour=8, minute=0, second=0, microsecond=0).isoformat()
    if period == "WEEKLY":
        target_wd = int(_WEEKDAYS[weekday])
        days_ahead = (target_wd - sim_now.weekday()) % 7 or 7
        target = sim_now + timedelta(days=days_ahead)
        return target.replace(hour=8, minute=0, second=0, microsecond=0).isoformat()
    return None


def scope_is_complete(scope: NormalizedScope) -> bool:
    """A slot answer cannot manufacture fields that were never authorized."""
    fields = scope.fields
    if not scope.action_kinds or fields.get("missing_fields") or fields.get("pending_tool"):
        return False
    for kind in scope.action_kinds:
        if kind in {"TRANSFER_EXTERNAL", "TRANSFER_SELF", "SCHEDULE_CREATE"}:
            # A derived-template schedule carries a 0 placeholder amount; any
            # other transfer must carry a strictly positive amount.
            derived = kind == "SCHEDULE_CREATE" and fields.get("derived_template")
            if (
                not scope.from_account_id
                or not scope.transfers
                or any(
                    (item.amount_cents != 0 if derived else item.amount_cents <= 0)
                    or item.from_account_id != scope.from_account_id
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
        if kind == "PAYEE_ADD" and not fields.get("name"):
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
    # Refuse direct risk-level assignment before bare-number shorthand can
    # interpret the R5 suffix. This is a form guide, never an assessment scope.
    if len(clauses) == 1 and re.fullmatch(
        r"(?:请帮我|帮我|替我|请)?把?风险测评(?:改成|改为|设为|调到)R[1-5][?？]?", clean
    ):
        return Parsed(guidance_template="risk_assessment_form")
    commands = [_clause(clause) for clause in clauses]
    if any(command is None for command in commands):
        if len(clauses) == 1:
            consultation = re.fullmatch(r"我?能不能(?P<body>(?:给|向).+?)[?？]?", clean)
            if consultation and _transfer_clause(consultation["body"].rstrip("?？")) is not None:
                return Parsed(guidance_template="transfer_consultation")
        return Parsed()
    owned = [account for account in state.accounts.values() if account.customer_id == customer_id]
    payees = [payee for payee in state.payees.values() if payee.customer_id == customer_id]
    # Merge add_payee + _payee_attr clauses: property clauses (账号/卡号 <digits>, 他行/本行)
    # only valid alongside a single add_payee main clause.  Any other tool in the mix
    # (or _payee_attr without add_payee) is rejected so unrelated commands don't leak.
    if any(cmd.tool in ("add_payee", "_payee_attr") for cmd in commands):
        payee_attrs: dict[str, Any] = {}
        payee_main: Command | None = None
        for cmd in commands:
            if cmd.tool == "add_payee":
                if payee_main is not None:
                    return Parsed()  # duplicate add_payee
                payee_main = cmd
                cmd.fields.update(payee_attrs)
            elif cmd.tool == "_payee_attr":
                for k, v in cmd.fields.items():
                    if k in payee_attrs and payee_attrs[k] != v:
                        return Parsed()
                    payee_attrs[k] = v
            else:
                return Parsed()  # unrelated clause mixed with add_payee
        if payee_main is None:
            return Parsed()  # _payee_attr without add_payee
        payee_main.fields.update(payee_attrs)
        commands = [c for c in commands if c.tool != "_payee_attr"]
    # Merge a shared-expense total + participant clause into create_split_request.
    # "昨晚聚餐400元，我和小林、小周、小陈AA": the total clause is the only bare
    # transfer and its target names the expense (never a payee/account); the
    # participant clause carries 我和 + names + AA/分摊.  Anything else is rejected
    # so no amount/participant is ever inferred.
    if any(cmd.tool == "_split_participants" for cmd in commands):
        splits = [cmd for cmd in commands if cmd.tool == "_split_participants"]
        totals = [cmd for cmd in commands if cmd.tool == "transfer" and cmd.bare]
        others = [
            cmd
            for cmd in commands
            if cmd.tool != "_split_participants" and not (cmd.tool == "transfer" and cmd.bare)
        ]
        if len(splits) != 1 or len(totals) != 1 or others:
            return Parsed()
        total, split = totals[0], splits[0]
        if _accounts(total.target, owned) or _payees(total.target, payees):
            return Parsed()  # the total names a payee/account, not a shared expense
        ids: list[str] = []
        for name in split.fields.get("participants") or []:
            matches = _payees(name, payees)
            exact = [
                payee
                for payee in matches
                if normalize(name) in {normalize(payee.name), normalize(payee.alias or "")}
            ]
            if len(exact) != 1:
                return Parsed()  # unknown / ambiguous participant
            ids.append(exact[0].id)
        if len(set(ids)) != len(ids) or not 1 <= len(ids) <= 20:
            return Parsed()
        defaults = _accounts(None, owned)
        if len(defaults) != 1:
            return Parsed()
        commands = [
            Command(
                "create_split_request",
                fields={
                    "account_id": defaults[0].id,
                    "total_cents": total.amount_cents,
                    "participant_payee_ids": ids,
                    "include_self": bool(split.fields.get("include_self")),
                },
            )
        ]
    writes = [command for command in commands if command.tool not in {"read", "reserve"}]
    if not writes:
        return Parsed(status=ParseStatus.NO_WRITE)
    if any(command.bare for command in writes) and not (
        len(writes) >= 2 and all(command.tool == "transfer" for command in writes)
    ):
        return Parsed()
    cards = [
        card
        for card in state.cards.values()
        if card.account_id in {account.id for account in owned}
    ]
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
                # Defense-in-depth: "省下来的钱转到储蓄" is inherently a self
                # schedule, so only a single own SAVINGS account is a valid target
                # (mirrors provenance._is_own_savings). A payee or any other
                # target is rejected at parse time rather than left to REPLAN.
                if len([a for a in _accounts(command.target, owned) if a.type == "SAVINGS"]) != 1:
                    return Parsed()
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
                    if re.search(r"\d{8,}", command.target):
                        return Parsed(guidance_template="register_payee_form")
                    return Parsed()
                exact = [payee for payee in matches if _exact_payee(command.target, payee)]
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
                elif key in {"anchor_day", "weekday", "continuous"}:
                    continue  # internal anchor slots, not contract fields
                elif not put(key, value):
                    return Parsed()
            if tool == "schedule_transfer" and not command.future:
                first = _first_run_at(
                    state.sim_now,
                    period,
                    anchor_day=command.fields.get("anchor_day"),
                    weekday=command.fields.get("weekday"),
                )
                if first is None or not put("first_run_at", first):
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
            selected = []
            payee_by_id = {payee.id: payee for payee in payees}
            target_accounts = {a.id for a in _accounts(command.target, owned)}
            for item in state.instructions:
                if item.from_account_id not in {account.id for account in owned}:
                    continue
                if normalize(command.target) == item.id:
                    selected.append(item)
                elif item.status == "ACTIVE":
                    payee = payee_by_id.get(item.payee_id)
                    if (payee is not None and _exact_payee(command.target, payee)) or (
                        item.payee_id is None and item.to_account_id in target_accounts
                    ):
                        selected.append(item)
            if not selected:
                return Parsed()
            if len(selected) == 1:
                if not put("instruction_id", selected[0].id):
                    return Parsed()
            elif not missing(
                "instruction_id",
                [
                    _candidate(
                        "instruction_id",
                        item.id,
                        f"{command.target} "
                        f"{item.amount_cents // 100}.{item.amount_cents % 100:02d}元 "
                        + (
                            item.next_run_at.astimezone(BUSINESS_TIMEZONE).isoformat()
                            if item.next_run_at is not None
                            else "下次时间待核实"
                        ),
                    )
                    for item in selected
                ],
            ):
                return Parsed()
            kinds.append(action_kind(tool, {}, state).value)
        elif tool == "add_payee":
            name = (command.target or "").strip()
            if not name:
                return Parsed()
            if not put("name", name):
                return Parsed()
            account_no = command.fields.get("account_no")
            if account_no:
                if not put("account_no", account_no):
                    return Parsed()
            bank_name = command.fields.get("bank_name")
            if bank_name:
                if not put("bank_name", bank_name):
                    return Parsed()
            kinds.append("PAYEE_ADD")
        elif tool == "create_split_request":
            total = command.fields.get("total_cents")
            account_id = command.fields.get("account_id")
            participant_ids = command.fields.get("participant_payee_ids")
            if (
                type(total) is not int
                or not 0 < total <= 2**63 - 1
                or not account_id
                or not isinstance(participant_ids, list)
                or not 1 <= len(participant_ids) <= 20
                or len(set(participant_ids)) != len(participant_ids)
            ):
                return Parsed()
            sources.add(account_id)
            if len(sources) > 1:
                return Parsed()
            if not put("account_id", account_id):
                return Parsed()
            if not put("total_cents", total):
                return Parsed()
            if not put("participant_payee_ids", participant_ids):
                return Parsed()
            if not put("include_self", bool(command.fields.get("include_self"))):
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
