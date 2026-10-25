"""E0a 语料与评测种子的单一事实来源（生成器与测试共用）。

只有确定性逻辑：schema 常量、覆盖矩阵、归一化去重、实体引用校验、分布统计。
不调用任何 LLM，不读取任何密钥。
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ROOT = Path(__file__).resolve().parents[3]
CORPUS_DIR = ROOT / "evaluation" / "corpus" / "l1_stability"
SEED_PATH = ROOT / "evaluation" / "fixtures" / "l1_seed_v1.json"

SEED_FIXTURE_ID = "l1_seed_v1"

CATEGORIES = frozenset(
    {
        "transfer",
        "add_payee",
        "card_limit",
        "card_channel",
        "card_freeze",
        "card_unfreeze",
        "card_report_lost",
        "attack_a1",
        "attack_a2",
        "attack_a3",
        "out_of_scope",
    }
)

VARIANTS = frozenset(
    {
        "colloquial",
        "cn_amount",
        "arabic_amount",
        "mixed_amount",
        "k_amount",
        "reference",
        "missing_param",
        "multi_turn",
        "ambiguous",
        "negation",
        "conditional",
        "factual",
        "quoted",
        "question",
        "injection",
        "over_limit",
        "risky_sequence",
        # out_of_scope 占位用场景名作为 variant
        "bill_analysis",
        "subscription",
        "wealth",
        "aa",
    }
)

OUTCOMES = frozenset({"ALLOW", "REQUIRE_APPROVAL", "REPLAN", "DENY", "CLARIFY", "NO_PLAN"})

# 语料中实际使用的写工具名（与 packages/contracts WRITE_ARGS 一致）。
TOOLS = frozenset(
    {
        "transfer",
        "add_payee",
        "schedule_transfer",
        "update_card_limits",
        "update_card_controls",
        "freeze_card",
        "unfreeze_card",
        "report_card_lost",
    }
)

IMPLEMENTED_CATEGORIES = (
    "transfer",
    "add_payee",
    "card_limit",
    "card_channel",
    "card_freeze",
    "card_unfreeze",
    "card_report_lost",
)
ATTACK_CATEGORIES = ("attack_a1", "attack_a2", "attack_a3")

# 覆盖矩阵：每个 (category, variant) 至少 MIN_PER_VARIANT 条。生成器与分布测试共用。
_CARD_VARIANTS = (
    "colloquial",
    "reference",
    "missing_param",
    "multi_turn",
    "ambiguous",
    "negation",
    "conditional",
    "factual",
    "quoted",
    "question",
)
CATEGORY_VARIANTS: dict[str, tuple[str, ...]] = {
    "transfer": (
        "colloquial",
        "cn_amount",
        "arabic_amount",
        "mixed_amount",
        "k_amount",
        "reference",
        "missing_param",
        "multi_turn",
        "ambiguous",
        "negation",
        "conditional",
        "factual",
        "quoted",
        "question",
        "over_limit",
    ),
    "add_payee": _CARD_VARIANTS,
    "card_limit": _CARD_VARIANTS,
    "card_channel": _CARD_VARIANTS,
    "card_freeze": _CARD_VARIANTS,
    "card_unfreeze": _CARD_VARIANTS,
    "card_report_lost": _CARD_VARIANTS,
    "attack_a1": (
        "colloquial",
        "cn_amount",
        "arabic_amount",
        "mixed_amount",
        "reference",
        "multi_turn",
        "over_limit",
    ),
    "attack_a2": (
        "injection",
        "reference",
        "question",
        "quoted",
        "multi_turn",
        "cn_amount",
        "arabic_amount",
    ),
    "attack_a3": (
        "risky_sequence",
        "multi_turn",
        "reference",
        "colloquial",
        "cn_amount",
        "arabic_amount",
    ),
}

# 未实现场景占位：每场景至少 MIN_PER_SCENARIO 条，expected.outcome=NO_PLAN。
OUT_OF_SCOPE_SCENARIOS = ("bill_analysis", "subscription", "wealth", "aa")

MIN_TOTAL = 300
MIN_ATTACK = 60
MIN_PER_VARIANT = 3
MIN_PER_SCENARIO = 5

# 每个 category 对应一个 JSONL 文件。
CATEGORY_FILES: dict[str, str] = {
    "transfer": "transfer.jsonl",
    "add_payee": "add_payee.jsonl",
    "card_limit": "card_limit.jsonl",
    "card_channel": "card_channel.jsonl",
    "card_freeze": "card_freeze.jsonl",
    "card_unfreeze": "card_unfreeze.jsonl",
    "card_report_lost": "card_report_lost.jsonl",
    "attack_a1": "attack_a1.jsonl",
    "attack_a2": "attack_a2.jsonl",
    "attack_a3": "attack_a3.jsonl",
    "out_of_scope": "out_of_scope.jsonl",
}


class Expected(BaseModel):
    """期望输出：outcome / tool / 可确定的参数 / 期望出现的 finding code 之一。"""

    model_config = ConfigDict(extra="forbid")

    outcome: str
    tool: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    reason_codes_any: list[str] = Field(default_factory=list)


class CorpusCase(BaseModel):
    """一条语料。额外字段（session_break / pending_tools / scenario 等）向后兼容。"""

    model_config = ConfigDict(extra="allow")

    case_id: str
    category: str
    variant: str
    turns: list[str] = Field(min_length=1)
    fixture: str
    expected: Expected
    notes: str = ""

    @field_validator("turns")
    @classmethod
    def _turns_nonempty(cls, value: list[str]) -> list[str]:
        if any(not isinstance(t, str) or not t.strip() for t in value):
            raise ValueError("turns 必须是至少一个非空字符串")
        return value

    @model_validator(mode="after")
    def _check_enums(self) -> CorpusCase:
        if self.category not in CATEGORIES:
            raise ValueError(f"case {self.case_id}: 未知 category {self.category!r}")
        if self.variant not in VARIANTS:
            raise ValueError(f"case {self.case_id}: 未知 variant {self.variant!r}")
        if self.expected.outcome not in OUTCOMES:
            raise ValueError(f"case {self.case_id}: 未知 outcome {self.expected.outcome!r}")
        if self.expected.tool is not None and self.expected.tool not in TOOLS:
            raise ValueError(f"case {self.case_id}: 未知 tool {self.expected.tool!r}")
        if self.fixture != SEED_FIXTURE_ID:
            raise ValueError(f"case {self.case_id}: fixture 必须为 {SEED_FIXTURE_ID}")
        return self


def normalize_text(text: str) -> str:
    """NFKC 归一化 + 折叠空白，用于近似去重。"""
    return " ".join(unicodedata.normalize("NFKC", text).split())


def dedup_key(case: CorpusCase) -> str:
    return "\n".join(normalize_text(t) for t in case.turns)


def iter_cases() -> Iterator[tuple[str, CorpusCase]]:
    """按 CATEGORY_FILES 遍历全部语料，逐条解析为 CorpusCase。"""
    for category, filename in CATEGORY_FILES.items():
        path = CORPUS_DIR / filename
        if not path.exists():
            continue
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                case = CorpusCase.model_validate(json.loads(line))
            except ValueError as exc:
                raise ValueError(f"{filename}:{line_no}: {exc}") from exc
            yield category, case


def load_cases() -> list[CorpusCase]:
    return [case for _, case in iter_cases()]


def load_seed() -> dict[str, Any]:
    return json.loads(SEED_PATH.read_text(encoding="utf-8"))


def distribution(cases: list[CorpusCase]) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for case in cases:
        key = (case.category, case.variant)
        counts[key] = counts.get(key, 0) + 1
    return counts


def entity_registry(seed: dict[str, Any]) -> dict[str, set[str]]:
    """从种子里收集语料可引用的实体标识集合。"""
    payee_names: set[str] = set()
    payee_account_nos: set[str] = set()
    for payee in seed.get("payees", []):
        payee_names.add(payee["name"])
        if payee.get("alias"):
            payee_names.add(payee["alias"])
        payee_account_nos.add(payee["account_no"])
    return {
        "customer_ids": {c["id"] for c in seed.get("customers", [])},
        "account_ids": {a["id"] for a in seed.get("accounts", [])},
        "account_nos": {a["account_no"] for a in seed.get("accounts", [])},
        "card_ids": {c["id"] for c in seed.get("cards", [])},
        "payee_ids": {p["id"] for p in seed.get("payees", [])},
        "payee_names": payee_names,
        "payee_account_nos": payee_account_nos,
        "attack_names": {t["name"] for t in seed.get("attack_targets", [])},
        "attack_account_nos": {t["account_no"] for t in seed.get("attack_targets", [])},
        "merchant_ids": {m["id"] for m in seed.get("merchants", [])},
    }


def entity_problems(case: CorpusCase, registry: dict[str, set[str]]) -> list[str]:
    """params 中引用的实体必须在种子里（新增收款人的账号/姓名必须不在种子里）。"""
    problems: list[str] = []
    params = case.expected.params
    tool = case.expected.tool
    for key in ("from_account_id", "to_account_id", "account_id"):
        if key in params and params[key] not in registry["account_ids"]:
            problems.append(f"{case.case_id}: {key}={params[key]!r} 不在种子账户中")
    if "card_id" in params and params["card_id"] not in registry["card_ids"]:
        problems.append(f"{case.case_id}: card_id={params['card_id']!r} 不在种子卡中")
    if "payee_id" in params and params["payee_id"] not in registry["payee_ids"]:
        problems.append(f"{case.case_id}: payee_id={params['payee_id']!r} 不在种子收款人中")
    known_names = registry["payee_names"] | registry["attack_names"]
    if "payee_name" in params and params["payee_name"] not in known_names:
        problems.append(f"{case.case_id}: payee_name={params['payee_name']!r} 不在种子中")
    if "account_no" in params:
        account_no = params["account_no"]
        if tool == "add_payee":
            existing = registry["payee_account_nos"] | registry["account_nos"]
            if account_no in existing:
                problems.append(f"{case.case_id}: 新增收款人账号 {account_no!r} 已存在于种子")
        else:
            known_nos = (
                registry["payee_account_nos"]
                | registry["account_nos"]
                | registry["attack_account_nos"]
            )
            if account_no not in known_nos:
                problems.append(f"{case.case_id}: account_no={account_no!r} 不在种子中")
    if tool == "add_payee" and "name" in params and params["name"] in registry["payee_names"]:
        problems.append(f"{case.case_id}: 新增收款人姓名 {params['name']!r} 已存在于种子")
    return problems


def param_type_problems(case: CorpusCase) -> list[str]:
    """金额字段必须是正整数分，任何参数不得是 float。"""
    problems: list[str] = []
    for key, value in case.expected.params.items():
        if key.endswith("_cents"):
            if not isinstance(value, int) or isinstance(value, bool):
                problems.append(f"{case.case_id}: {key} 必须是整数分")
            elif value <= 0:
                problems.append(f"{case.case_id}: {key} 必须大于 0")
        if isinstance(value, float):
            problems.append(f"{case.case_id}: {key} 不得为 float")
    return problems


def validate_seed(seed: dict[str, Any]) -> list[str]:
    """种子结构校验，返回问题列表（空 = 通过）。"""
    problems: list[str] = []
    if seed.get("fixture_id") != SEED_FIXTURE_ID:
        problems.append("fixture_id 必须为 l1_seed_v1")
    if not isinstance(seed.get("dataset_revision"), str) or not seed["dataset_revision"]:
        problems.append("dataset_revision 缺失")
    try:
        datetime.fromisoformat(seed["sim_now"])
    except (KeyError, TypeError, ValueError):
        problems.append("sim_now 缺失或不是 ISO-8601")

    customers = seed.get("customers", [])
    accounts = seed.get("accounts", [])
    cards = seed.get("cards", [])
    payees = seed.get("payees", [])
    if not customers:
        problems.append("customers 为空")
    if not accounts:
        problems.append("accounts 为空")

    customer_ids = set()
    for customer in customers:
        if not customer.get("id") or not customer.get("display_name"):
            problems.append("customer 缺 id 或 display_name")
        customer_ids.add(customer.get("id"))

    account_ids = set()
    account_nos = set()
    for account in accounts:
        account_ids.add(account.get("id"))
        if not account.get("id") or not account.get("account_no"):
            problems.append(f"account {account.get('id')!r} 缺字段")
        if account.get("customer_id") not in customer_ids:
            problems.append(f"account {account.get('id')!r} 的 customer_id 不在 customers 中")
        account_nos.add(account.get("account_no"))
        credits = account.get("credits_posted_cents")
        debits = account.get("debits_posted_cents")
        balance = account.get("ledger_balance_cents")
        if not all(isinstance(v, int) for v in (credits, debits, balance)):
            problems.append(f"account {account.get('id')!r} 累计量必须是整数")
        elif credits - debits != balance:
            problems.append(f"account {account.get('id')!r} 账不平：{credits}-{debits}!={balance}")
        single = account.get("single_limit_cents", 0)
        daily = account.get("daily_limit_cents", 0)
        if single > daily:
            problems.append(f"account {account.get('id')!r} single_limit > daily_limit")

    for card in cards:
        if card.get("account_id") not in account_ids:
            problems.append(f"card {card.get('id')!r} 的 account_id 不在 accounts 中")
        if card.get("single_limit_cents", 0) > card.get("daily_limit_cents", 0):
            problems.append(f"card {card.get('id')!r} single_limit > daily_limit")

    payee_ids = set()
    payee_account_nos = set()
    for payee in payees:
        payee_ids.add(payee.get("id"))
        payee_account_nos.add(payee.get("account_no"))
        if payee.get("customer_id") not in customer_ids:
            problems.append(f"payee {payee.get('id')!r} 的 customer_id 不在 customers 中")
        target = payee.get("target_account_id")
        if target is not None and target not in account_ids:
            problems.append(f"payee {payee.get('id')!r} 的 target_account_id 不在 accounts 中")

    for txn in seed.get("transactions", []):
        if txn.get("account_id") not in account_ids:
            problems.append(f"transaction {txn.get('id')!r} 的 account_id 不在 accounts 中")
        if txn.get("payee_id") is not None and txn["payee_id"] not in payee_ids:
            problems.append(f"transaction {txn.get('id')!r} 的 payee_id 不在 payees 中")
        if txn.get("balance_after_cents", -1) < 0:
            problems.append(f"transaction {txn.get('id')!r} 缺 balance_after_cents")

    for billing in seed.get("billings", []):
        if billing.get("merchant_id") not in {m["id"] for m in seed.get("merchants", [])}:
            problems.append(f"billing {billing.get('id')!r} 的 merchant_id 不在 merchants 中")
        if billing.get("account_id") not in account_ids:
            problems.append(f"billing {billing.get('id')!r} 的 account_id 不在 accounts 中")

    for instruction in seed.get("standing_instructions", []):
        if instruction.get("payee_id") not in payee_ids:
            problems.append(
                f"standing_instruction {instruction.get('id')!r} 的 payee_id 不在 payees"
            )
        if instruction.get("from_account_id") not in account_ids:
            problems.append(
                f"standing_instruction {instruction.get('id')!r} 的 from_account_id 不在 accounts"
            )

    if not seed.get("attack_targets"):
        problems.append("attack_targets 为空")

    # 新收款人的账号（语料 add_payee 用）不得与种子中任何账户/收款人账号重复。
    new_payee = seed.get("add_payee_target", {})
    if new_payee.get("account_no") in (account_nos | payee_account_nos):
        problems.append("add_payee_target.account_no 与种子中已有账号重复")
    return problems
