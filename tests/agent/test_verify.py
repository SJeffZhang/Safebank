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

    # ── P1-1 Chinese numeral amounts ───────────────────────────────────

    def test_chinese_bayuan(self):
        items = extract_amounts("已向赵六转账八百元")
        assert len(items) == 1
        assert items[0]["cents"] == 80000

    def test_chinese_shi_er(self):
        items = extract_amounts("十二元")
        assert len(items) == 1
        assert items[0]["cents"] == 1200

    def test_chinese_ling(self):
        items = extract_amounts("一百零五元")
        assert len(items) == 1
        assert items[0]["cents"] == 10500

    def test_chinese_large(self):
        items = extract_amounts("三千块")
        assert len(items) == 1
        assert items[0]["cents"] == 300000

    def test_chinese_wan(self):
        items = extract_amounts("两万元")
        assert len(items) == 1
        assert items[0]["cents"] == 2000000

    def test_chinese_wan_abbrev(self):
        items = extract_amounts("三万五千元")
        assert len(items) == 1
        assert items[0]["cents"] == 3500000

    def test_chinese_not_money_not_extracted(self):
        items = extract_amounts("十二年陈酿")
        assert items == []

    def test_chinese_mixed(self):
        items = extract_amounts("¥800.00 和 一千元")
        assert len(items) == 2
        cents = {i["cents"] for i in items}
        assert 80000 in cents
        assert 100000 in cents


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

    # ── card tail bug (2026-10-29): non-4-digit tails are extracted as malformed ─

    def test_malformed_id_tail(self):
        items = extract_card_last4("已为您解冻尾号 zs_1 的卡")
        assert len(items) == 1
        assert items[0]["kind"] == ExtractedKind.CARD_LAST4
        assert items[0]["last4"] is None

    def test_malformed_card_id_tail(self):
        items = extract_card_last4("尾号crd_zs_1")
        assert len(items) == 1
        assert items[0]["last4"] is None

    def test_malformed_five_digits(self):
        items = extract_card_last4("尾号12345")
        assert len(items) == 1
        assert items[0]["last4"] is None

    def test_malformed_mixed(self):
        items = extract_card_last4("尾号 ab12")
        assert len(items) == 1
        assert items[0]["last4"] is None

    def test_valid_four_digits(self):
        items = extract_card_last4("尾号1234")
        assert len(items) == 1
        assert items[0]["last4"] == "1234"

    # ── P1-1 copula variants (尾号为/尾号是/卡号尾号为) ───────────────

    def test_copula_wei_malformed(self):
        items = extract_card_last4("尾号为zs_1")
        assert len(items) == 1
        assert items[0]["last4"] is None

    def test_copula_shi_malformed(self):
        items = extract_card_last4("尾号是zs_1")
        assert len(items) == 1
        assert items[0]["last4"] is None

    def test_copula_kahaowei_malformed(self):
        items = extract_card_last4("卡号尾号为zs_1")
        assert len(items) == 1
        assert items[0]["last4"] is None

    def test_copula_wei_valid(self):
        items = extract_card_last4("尾号为1234")
        assert len(items) == 1
        assert items[0]["last4"] == "1234"

    def test_copula_amounts_after_untouched(self):
        items = extract_card_last4("尾号为1234，转账80000元，余额6000元")
        assert len(items) == 1
        assert items[0]["last4"] == "1234"


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

    def test_card_tail_malformed_reason(self):
        facts = {"amounts": [], "percentages": [], "last4s": ["1234"]}
        extracted = [
            {"span": (0, 8), "raw": "尾号 zs_1", "last4": None, "kind": ExtractedKind.CARD_LAST4}
        ]
        uv = check_against_facts(extracted, facts)
        assert len(uv) == 1
        assert uv[0]["reason"] == "card_tail_malformed"


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

    # ── E3 supplement (2026-10-25): CJK-adjacent, separated, and boundary cases ─

    def test_e3_no_space_cjk_adjacent(self):
        """卡号6222021234567890 — digits immediately after Chinese → masked."""
        result = mask_long_numbers("卡号6222021234567890")
        assert "622202" not in result
        assert "****7890" in result

    def test_e3_spaces_between_digit_groups(self):
        """6222 0212 3456 7890 — space-separated digit groups → masked."""
        result = mask_long_numbers("账号 6222 0212 3456 7890")
        assert "6222" not in result
        assert "0212" not in result
        assert "****7890" in result

    def test_e3_hyphens_between_digit_groups(self):
        """6222-0212-3456-7890 — hyphen-separated digit groups → masked."""
        result = mask_long_numbers("账号6222-0212-3456-7890")
        assert "6222" not in result
        assert "****7890" in result

    def test_e3_chinese_both_sides(self):
        """转账给62220212345678的账户 — digits between Chinese chars → masked."""
        result = mask_long_numbers("转账给62220212345678的账户")
        assert "622202" not in result
        assert "****5678" in result

    def test_e3_cjk_punctuation(self):
        """卡号：6222021234567890。 — punctuation adjacent → masked."""
        result = mask_long_numbers("卡号：6222021234567890。")
        assert "622202" not in result
        assert "****7890" in result

    def test_e3_line_start(self):
        """6222021234567890 at line start → masked."""
        result = mask_long_numbers("6222021234567890")
        assert "****7890" in result

    def test_e3_line_end(self):
        """text ending with 6222021234567890 → masked."""
        result = mask_long_numbers("尾号6222021234567890")
        assert "****7890" in result

    # ── P1-2 multi-separator bypass ────────────────────────────────────

    def test_p12_double_space_separator(self):
        """6222  0212  3456  7890 — double spaces → still masked."""
        result = mask_long_numbers("卡号6222  0212  3456  7890")
        assert "6222" not in result
        assert "****7890" in result

    def test_p12_double_hyphen_separator(self):
        """6222--0212--3456--7890 — double hyphens → still masked."""
        result = mask_long_numbers("卡号6222--0212--3456--7890")
        assert "6222" not in result
        assert "****7890" in result

    def test_p12_tab_separator(self):
        """Tab-separated digit groups → still masked."""
        result = mask_long_numbers("卡号6222\t0212\t3456\t7890")
        assert "6222" not in result
        assert "****7890" in result

    def test_p12_nfkc_fullwidth_digits(self):
        """Fullwidth ６２２２０２１２３４５６７８９０ → NFKC → masked."""
        text = "６２２２０２１２３４５６７８９０"
        result = mask_long_numbers(text)
        assert "6222" not in result
        assert "****7890" in result

    def test_p12_zero_width_chars(self):
        """Zero-width chars between digit groups → still masked."""
        text = "6222​0212​3456​7890"
        result = mask_long_numbers(text)
        assert "6222" not in result
        assert "****7890" in result

    # ── P2-3 decimal+元 protection ─────────────────────────────────────

    def test_p23_decimal_yuan_preserved(self):
        """10000000.00元 should be protected as amount, not masked."""
        result = mask_long_numbers("余额 10000000.00元")
        assert "10000000.00元" in result

    def test_p23_plain_10m_not_protected(self):
        """10000000 without amount marker → masked."""
        result = mask_long_numbers("账号 10000000")
        assert "10000000" not in result
        assert "****0000" in result


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
        """A valid repaired read-only amount is still accepted."""

        class FakeLLM:
            def invoke(self, messages):
                return type("r", (), {"content": "余额800.00元。"})

        verifier = OutputVerifier(llm=FakeLLM())
        state = {
            "reply_draft": "余额900.00元。",
            "reads": [{"data": {"balance_cents": 80000}}],
        }
        result = verifier(state, None)
        assert "800.00" in result
        assert "900.00" not in result

    def test_llm_repair_fails(self):
        """A failed repair must not pass the known wrong amount through."""

        class BadLLM:
            def invoke(self, messages):
                raise RuntimeError("down")

        verifier = OutputVerifier(llm=BadLLM())
        state = {
            "reply_draft": "余额900.00元。",
            "reads": [{"data": {"balance_cents": 80000}}],
        }
        result = verifier(state, None)
        assert len(result) > 0
        assert "900.00" not in result

    # ── card tail bug (2026-10-29): unverified card tail must not reach the user ─

    def test_malformed_card_tail_scrubbed(self):
        """'尾号 zs_1' with no LLM → replaced by 该卡, card ID never leaks."""
        verifier = OutputVerifier(llm=None)
        state = {
            "reply_draft": "已为您解冻尾号 zs_1 的卡",
            "reads": [
                ReadRecord(
                    source_ref="src_a",
                    tool="get_cards",
                    args={},
                    data={"cards": [{"id": "crd_zs_1", "last4": "1234"}]},
                ),
            ],
        }
        result = verifier(state, None)
        assert "zs_1" not in result
        assert "该卡" in result

    def test_valid_card_tail_unchanged(self):
        """'尾号1234' that matches facts stays unchanged."""
        verifier = OutputVerifier(llm=None)
        state = {
            "reply_draft": "已为您解冻尾号 1234 的卡",
            "reads": [
                ReadRecord(
                    source_ref="src_a",
                    tool="get_cards",
                    args={},
                    data={"cards": [{"id": "crd_zs_1", "last4": "1234"}]},
                ),
            ],
        }
        result = verifier(state, None)
        assert "1234" in result
        assert "该卡" not in result

    def test_scrub_deka_collapses(self):
        """'尾号 zs_1 的卡' → '该卡' (no doubled 的卡)."""
        verifier = OutputVerifier(llm=None)
        state = {
            "reply_draft": "已为您解冻尾号 zs_1 的卡",
            "reads": [
                ReadRecord(
                    source_ref="src_a",
                    tool="get_cards",
                    args={},
                    data={"cards": [{"id": "crd_zs_1", "last4": "1234"}]},
                ),
            ],
        }
        result = verifier(state, None)
        assert result == "已为您解冻该卡"

    def test_scrub_without_deka(self):
        """'尾号 zs_1' with no trailing 的卡 → replaced with 该卡."""
        verifier = OutputVerifier(llm=None)
        state = {
            "reply_draft": "已为您解冻尾号 zs_1",
            "reads": [
                ReadRecord(
                    source_ref="src_a",
                    tool="get_cards",
                    args={},
                    data={"cards": [{"id": "crd_zs_1", "last4": "1234"}]},
                ),
            ],
        }
        result = verifier(state, None)
        assert result == "已为您解冻该卡"
