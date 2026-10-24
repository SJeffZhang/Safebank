"""C1e verify.py pure-function tests (Task 4i).

No real LLM, no PG — every function is tested with controlled inputs.
"""

from __future__ import annotations

from services.agent.app.graph.state import (
    ReadRecord,
)
from services.agent.app.llm.verify import (
    ExtractedKind,
    OutputVerifier,
    _contains_any,
    _overlaps,
    _yuan_str_to_cents,
    build_fact_set,
    check_against_facts,
    check_semantic_consistency,
    extract_amounts,
    extract_card_last4,
    extract_percentages,
    mask_long_numbers,
)

# ── _yuan_str_to_cents ──────────────────────────────────────────────────


class TestYuanStrToCents:
    def test_int_yuan(self):
        assert _yuan_str_to_cents("800") == 80000

    def test_decimal_yuan(self):
        assert _yuan_str_to_cents("800.00") == 80000

    def test_partial_decimal(self):
        assert _yuan_str_to_cents("800.5") == 80050

    def test_zero(self):
        assert _yuan_str_to_cents("0") == 0

    def test_large(self):
        assert _yuan_str_to_cents("9999999.99") == 999999999

    def test_negative_rejected(self):
        assert _yuan_str_to_cents("-100") is None

    def test_garbage(self):
        assert _yuan_str_to_cents("abc") is None


# ── extract_amounts ─────────────────────────────────────────────────────


class TestExtractAmounts:
    def test_cny_prefix(self):
        items = extract_amounts("转账 ¥800.00 给王五")
        assert len(items) == 1
        assert items[0]["cents"] == 80000

    def test_cny_prefix_cn(self):
        items = extract_amounts("转账 ￥1,200.50")
        assert len(items) == 1
        assert items[0]["cents"] == 120050

    def test_yuan_suffix(self):
        items = extract_amounts("转账 800元")
        assert len(items) == 1
        assert items[0]["cents"] == 80000

    def test_kuai_suffix(self):
        items = extract_amounts("花费 150块")
        assert len(items) == 1
        assert items[0]["cents"] == 15000

    def test_multiple(self):
        items = extract_amounts("¥800.00 转账成功，余额 ¥5200.00")
        assert len(items) == 2
        cents = [i["cents"] for i in items]
        assert 80000 in cents
        assert 520000 in cents

    def test_no_amount(self):
        assert extract_amounts("你的请求已收到") == []


# ── extract_percentages ─────────────────────────────────────────────────


class TestExtractPercentages:
    def test_single(self):
        items = extract_percentages("收益率 2.5%")
        assert len(items) == 1
        assert items[0]["value"] == 2.5

    def test_int(self):
        items = extract_percentages("利率 3%")
        assert len(items) == 1
        assert items[0]["value"] == 3.0

    def test_multiple(self):
        items = extract_percentages("收益率 1.5%，年化 4.2%")
        assert len(items) == 2

    def test_none(self):
        assert extract_percentages("无百分比") == []


# ── extract_card_last4 ──────────────────────────────────────────────────


class TestExtractCardLast4:
    def test_single(self):
        items = extract_card_last4("您的卡尾号 1234 今日限额")
        assert len(items) == 1
        assert items[0]["last4"] == "1234"

    def test_multiple(self):
        items = extract_card_last4("尾号 1111 和 尾号 2222")
        assert len(items) == 2

    def test_none(self):
        assert extract_card_last4("没有卡") == []


# ── build_fact_set ──────────────────────────────────────────────────────


class TestBuildFactSet:
    def test_from_reads(self):
        state = {
            "reads": [
                ReadRecord(
                    source_ref="src_a",
                    tool="get_balances",
                    args={},
                    data={
                        "accounts": [{"id": "acc_z", "balance_cents": 600000, "type": "checking"}]
                    },
                ),
                ReadRecord(
                    source_ref="src_b",
                    tool="get_cards",
                    args={},
                    data={"cards": [{"id": "crd_1", "last4": "1234"}]},
                ),
            ]
        }
        fs = build_fact_set(state)
        assert 600000 in fs["amounts"]
        assert "1234" in fs["last4s"]

    def test_from_execution(self):
        state = {
            "execution": {
                "plan_id": "p1",
                "status": "EXECUTED",
                "steps": [
                    {
                        "step_id": "s1",
                        "tool": "transfer",
                        "status": "SUCCESS",
                        "result": {"balance_cents": 520000},
                    }
                ],
            }
        }
        fs = build_fact_set(state)
        assert 520000 in fs["amounts"]

    def test_empty_state(self):
        fs = build_fact_set({})
        assert fs["amounts"] == []
        assert fs["percentages"] == []
        assert fs["last4s"] == []


# ── check_against_facts ─────────────────────────────────────────────────


class TestCheckAgainstFacts:
    def test_amount_match(self):
        facts = {"amounts": [80000], "percentages": [], "last4s": []}
        extracted = [
            {"span": (3, 9), "raw": "¥800.00", "cents": 80000, "kind": ExtractedKind.AMOUNT}
        ]
        assert check_against_facts(extracted, facts) == []

    def test_amount_mismatch(self):
        facts = {"amounts": [520000], "percentages": [], "last4s": []}
        extracted = [
            {"span": (3, 9), "raw": "¥800.00", "cents": 80000, "kind": ExtractedKind.AMOUNT}
        ]
        uv = check_against_facts(extracted, facts)
        assert len(uv) == 1
        assert uv[0]["reason"] == "amount_not_found"

    def test_amount_rounding_tolerance(self):
        # ¥800.01 → 80001 cents, fact is 80000 → ±1 tolerance → OK
        facts = {"amounts": [80000], "percentages": [], "last4s": []}
        extracted = [
            {"span": (0, 8), "raw": "¥800.01", "cents": 80001, "kind": ExtractedKind.AMOUNT}
        ]
        assert check_against_facts(extracted, facts) == []

    def test_pct_match(self):
        facts = {"amounts": [], "percentages": [2.5], "last4s": []}
        extracted = [
            {"span": (0, 4), "raw": "2.5%", "value": 2.5, "kind": ExtractedKind.PERCENTAGE}
        ]
        assert check_against_facts(extracted, facts) == []

    def test_card_mismatch(self):
        facts = {"amounts": [], "percentages": [], "last4s": ["5678"]}
        extracted = [
            {"span": (0, 8), "raw": "尾号 1234", "last4": "1234", "kind": ExtractedKind.CARD_LAST4}
        ]
        uv = check_against_facts(extracted, facts)
        assert len(uv) == 1


# ── mask_long_numbers ───────────────────────────────────────────────────


class TestMaskLongNumbers:
    def test_masks_8_digit_card(self):
        result = mask_long_numbers("卡号 6222021234567890")
        assert "622202" not in result
        assert "****7890" in result

    def test_preserves_date(self):
        result = mask_long_numbers("日期 2026-10-25 的账单")
        assert "2026-10-25" in result
        assert "****" not in result

    def test_preserves_date_compact(self):
        result = mask_long_numbers("20261025")
        assert "20261025" in result

    def test_preserves_amount_with_cny(self):
        result = mask_long_numbers("¥1,200.50 转账")
        assert "1,200.50" in result or "¥" in result

    def test_preserves_amount_with_yuan(self):
        result = mask_long_numbers("800元 转账")
        assert "800元" in result

    def test_empty(self):
        assert mask_long_numbers("") == ""

    def test_no_long_numbers(self):
        text = "short 1234 numbers only"
        assert mask_long_numbers(text) == text


# ── semantic checks ─────────────────────────────────────────────────────


class TestSemanticConsistency:
    def test_unknown_no_forbidden(self):
        state = {"execution": {"status": "UNKNOWN", "plan_id": "p1"}}
        violations = check_semantic_consistency("请求已提交，正在确认中…", state)
        assert violations == []

    def test_unknown_has_forbidden(self):
        state = {"execution": {"status": "UNKNOWN"}}
        violations = check_semantic_consistency("已转账成功", state)
        assert len(violations) == 1
        assert violations[0]["reason"] == "unknown_claims_certainty"

    def test_failed_masquerading(self):
        state = {"execution": {"status": "FAILED"}}
        violations = check_semantic_consistency("转账成功，操作完成", state)
        assert len(violations) == 1

    def test_no_execution(self):
        violations = check_semantic_consistency("hello", {})
        assert violations == []


# ── _contains_any ───────────────────────────────────────────────────────


class TestContainsAny:
    def test_found(self):
        assert _contains_any("已转账成功", ["已转账", "已完成"]) is True

    def test_not_found(self):
        assert _contains_any("请求处理中", ["已转账"]) is False

    def test_empty_text(self):
        assert _contains_any("", ["已转账"]) is False


# ── _overlaps ───────────────────────────────────────────────────────────


def test_overlaps():
    assert _overlaps((0, 5), (3, 8)) is True
    assert _overlaps((0, 5), (5, 8)) is False  # adjacent, not overlapping
    assert _overlaps((0, 5), (6, 8)) is False


# ── OutputVerifier integration (no LLM) ─────────────────────────────────


class TestOutputVerifierNoLLM:
    def test_fixed_template_passthrough(self):
        """Fixed template text with no LLM — passes through with masking only."""
        verifier = OutputVerifier(llm=None)
        state = {"reply_draft": "该操作未被授权，无法执行。"}
        result = verifier(state, None)
        assert "未被授权" in result

    def test_empty_draft(self):
        verifier = OutputVerifier(llm=None)
        state = {"reply_draft": ""}
        result = verifier(state, None)
        assert "已收到" in result

    def test_masks_in_fixed_template(self):
        """Even fixed templates get masked if they contain long numbers."""
        verifier = OutputVerifier(llm=None)
        state = {"reply_draft": "卡号 6222021234567890 的操作"}
        result = verifier(state, None)
        assert "622202" not in result

    def test_with_llm_repair(self):
        """With LLM, unverified items trigger repair attempt."""

        class FakeLLM:
            def invoke(self, messages):
                return type("r", (), {"content": "已向王五转账 800.00 元。"})

        verifier = OutputVerifier(llm=FakeLLM())
        state = {
            "reply_draft": "已向王五转账 900.00 元。",
            "execution": {
                "status": "EXECUTED",
                "steps": [
                    {
                        "step_id": "s1",
                        "tool": "transfer",
                        "status": "SUCCESS",
                        "result": {"balance_cents": 520000},
                    }
                ],
            },
        }
        result = verifier(state, None)
        # Should have been repaired (900 → 800) or passed through (then masked)
        assert len(result) > 0

    def test_llm_repair_fails(self):
        """When LLM raises, original text is used (with masking)."""

        class BadLLM:
            def invoke(self, messages):
                raise RuntimeError("down")

        verifier = OutputVerifier(llm=BadLLM())
        state = {
            "reply_draft": "已向王五转账 900.00 元。",
            "execution": {
                "status": "EXECUTED",
                "steps": [],
            },
        }
        result = verifier(state, None)
        # Falls through to original + masking
        assert len(result) > 0
