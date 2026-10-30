"""Red tests for two real e2e respond defects (2026-10-30).

A. REPLAN reply with unregistered finding codes → empty body after header.
B. Executed reply claiming "已新增收款人" when step was a no_op.
"""

from __future__ import annotations

from services.agent.app.llm.respond import (
    RespondAdapter,
    _build_executed_user_prompt,
    _execution_steps_summary,
    _replan_user_items,
)

# ── A. REPLAN missing-code labels ────────────────────────────────────────


class TestReplanMissingCodeLabels:
    """REPLAN findings with codes not in _REPLAN_USER_CODE_LABELS produced
    an empty body after '需要您确认以下信息：'."""

    def test_amount_above_yellow_cap_has_label(self):
        """AMOUNT_ABOVE_YELLOW_CAP → specific explanation, not just fallback."""
        decision = {
            "decision": "REPLAN",
            "findings": [
                {"code": "AMOUNT_ABOVE_YELLOW_CAP", "resolver": "user"},
            ],
        }
        items = _replan_user_items(decision)
        assert len(items) >= 1
        # Must contain a Chinese explanation, not just the generic fallback
        combined = " ".join(items)
        assert len(combined) >= 6
        assert any(ch >= "一" and ch <= "鿿" for ch in combined)

    def test_card_single_limit_has_label(self):
        """CARD_SINGLE_LIMIT → specific explanation."""
        decision = {
            "decision": "REPLAN",
            "findings": [
                {"code": "CARD_SINGLE_LIMIT", "resolver": "user"},
            ],
        }
        items = _replan_user_items(decision)
        assert len(items) >= 1
        combined = " ".join(items)
        assert len(combined) >= 6

    def test_cumulative_outflow_has_label(self):
        """CUMULATIVE_OUTFLOW → specific explanation."""
        decision = {
            "decision": "REPLAN",
            "findings": [
                {"code": "CUMULATIVE_OUTFLOW", "resolver": "user"},
            ],
        }
        items = _replan_user_items(decision)
        assert len(items) >= 1
        combined = " ".join(items)
        assert len(combined) >= 6

    def test_unregistered_code_has_fallback_per_code(self):
        """Completely unknown code → generic per-code fallback (not empty)."""
        decision = {
            "decision": "REPLAN",
            "findings": [
                {"code": "XYZ_SOME_FUTURE_CODE", "resolver": "user"},
            ],
        }
        items = _replan_user_items(decision)
        assert len(items) >= 1
        combined = " ".join(items)
        assert len(combined) >= 6
        assert any(ch >= "一" and ch <= "鿿" for ch in combined)

    def test_mixed_known_and_unknown_codes(self):
        """Known code gets label; unknown code gets fallback line."""
        decision = {
            "decision": "REPLAN",
            "findings": [
                {"code": "CARD_SINGLE_LIMIT", "resolver": "user"},
                {"code": "XYZ_UNKNOWN_CODE", "resolver": "user"},
            ],
        }
        items = _replan_user_items(decision)
        assert len(items) >= 2

    def test_replan_reply_never_empty_tail(self):
        """Full adapter: REPLAN reply must have non-empty body after the header."""

        class FakeLLM:
            def invoke(self, messages):
                raise RuntimeError("should not be called for REPLAN")

        adapter = RespondAdapter(FakeLLM())
        for code in (
            "AMOUNT_ABOVE_YELLOW_CAP",
            "CARD_SINGLE_LIMIT",
            "CUMULATIVE_OUTFLOW",
            "CARD_DAILY_LIMIT",
            "INSUFFICIENT_FUNDS",
            "XYZ_UNKNOWN_CODE",
        ):
            state = {
                "decision": {
                    "decision": "REPLAN",
                    "findings": [{"code": code, "resolver": "user"}],
                },
            }
            result = adapter(state, None)
            assert "需要您确认" in result
            tail = result.split("需要您确认以下信息：", 1)[1]
            assert tail.strip(), f"empty tail for code {code}"
            assert any(ch >= "一" and ch <= "鿿" for ch in tail), (
                f"no CJK chars in tail for code {code}"
            )


# ── B. no_op in executed response ────────────────────────────────────────


class TestExecutedNoOp:
    """When a step result contains no_op=True, the reply must not claim success."""

    def test_steps_summary_shows_no_op(self):
        """_execution_steps_summary includes explicit CN no_op explanation."""
        execution = {
            "plan_id": "pln_test",
            "status": "EXECUTED",
            "steps": [
                {
                    "step_id": "step_test",
                    "tool": "add_payee",
                    "status": "SUCCESS",
                    "created_ids": (),
                    "result": {"no_op": True, "payee_id": "pye_existing"},
                }
            ],
            "committed_at": "2026-10-30T10:00:00+08:00",
        }
        summary = _execution_steps_summary(execution)
        # Must contain explicit CN text, not just the raw JSON key "no_op"
        assert "未重复" in summary or "已存在" in summary

    def test_steps_summary_no_false_no_op(self):
        """Regular success (no no_op) should NOT mention no_op."""
        execution = {
            "plan_id": "pln_test",
            "status": "EXECUTED",
            "steps": [
                {
                    "step_id": "step_test",
                    "tool": "add_payee",
                    "status": "SUCCESS",
                    "created_ids": ("pye_new",),
                    "result": {"balance_cents": 520000},
                }
            ],
            "committed_at": "2026-10-30T10:00:00+08:00",
        }
        summary = _execution_steps_summary(execution)
        assert "no_op" not in summary.lower()
        assert "未重复" not in summary
        assert "已存在" not in summary

    def test_executed_prompt_includes_no_op_fact(self):
        """The user prompt sent to the LLM must mention no_op as CN fact."""
        state = {
            "utterances": [type("u", (), {"text": "添加收款人孙九"})],
        }
        state["reads"] = ()
        execution = {
            "plan_id": "pln_test",
            "status": "EXECUTED",
            "steps": [
                {
                    "step_id": "step_test",
                    "tool": "add_payee",
                    "status": "SUCCESS",
                    "created_ids": (),
                    "result": {"no_op": True, "payee_id": "pye_existing"},
                }
            ],
            "committed_at": "2026-10-30T10:00:00+08:00",
        }
        prompt = _build_executed_user_prompt(state, execution)
        # Prompt must contain the CN no_op fact so the LLM won't hallucinate success
        assert "已存在" in prompt or "未重复" in prompt

    def test_no_op_fallback_explicitly_says_unchanged(self):
        """When LLM fails and all steps are no_op, fallback must say unchanged."""

        class FailingLLM:
            def invoke(self, messages):
                raise RuntimeError("API down")

        adapter = RespondAdapter(FailingLLM())
        state = {
            "utterances": [type("u", (), {"text": "添加收款人孙九"})],
        }
        execution = {
            "plan_id": "pln_test",
            "status": "EXECUTED",
            "steps": [
                {
                    "step_id": "step_test",
                    "tool": "add_payee",
                    "status": "SUCCESS",
                    "created_ids": (),
                    "result": {"no_op": True, "payee_id": "pye_existing"},
                }
            ],
            "committed_at": "2026-10-30T10:00:00+08:00",
        }
        state["execution"] = execution
        result = adapter(state, None)
        # Must NOT say "操作已完成" when nothing actually happened
        assert "未重复" in result or "已存在" in result
