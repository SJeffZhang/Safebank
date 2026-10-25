"""C1e respond adapter tests (Task 3f).

Red-test-first design: each path is tested with fake state, fake LLM,
and verified for non-empty output.  No real API calls.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage

from services.agent.app.graph.state import (
    ReadRecord,
)
from services.agent.app.llm.respond import (
    RespondAdapter,
    _build_fallback,
    _build_prompt,
    _exec_status,
    _execution_steps_summary,
    _extract_decision,
    _has_user_resolver,
    _reads_summary,
    _replan_hint_labels,
    _safe_truncate,
    _utterance_text,
)

# ── Helpers ────────────────────────────────────────────────────────────


class FakeLLM:
    """Configurable fake LangChain chat model for controlled testing."""

    def __init__(self, response: str = "操作已完成。", raises: Exception | None = None):
        self.response = response
        self.raises = raises
        self.calls: list[list] = []

    def invoke(self, messages: list) -> AIMessage:
        self.calls.append(messages)
        if self.raises:
            raise self.raises
        return AIMessage(content=self.response)


def _fake_execution(**kw) -> dict:
    defaults = {
        "plan_id": "pln_X1Y2Z3ABCDEFGHJKMNPRSTVWZ01",
        "status": "EXECUTED",
        "steps": [
            {
                "step_id": "step_a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "tool": "transfer",
                "status": "SUCCESS",
                "created_ids": ("txn_X2Y3Z4ABCDEFGHJKMNPRSTVWZ02",),
                "result": {"balance_cents": 520000},
            }
        ],
        "committed_at": "2026-10-25T10:00:00+08:00",
    }
    defaults.update(kw)
    return defaults


def _make_state(**kw) -> dict:
    return kw


# ── Fixed-template paths (no LLM) ─────────────────────────────────────


class TestFixedTemplatePaths:
    def test_route_error_with_reply_returns_reply(self):
        adapter = RespondAdapter(FakeLLM("never called"))
        state = _make_state(
            route_error={"status": 409, "code": "APPROVAL_EXPIRED"},
            reply_draft="审批已过期。",
        )
        result = adapter(state, None)
        assert result == "审批已过期。"

    def test_unknown_execution_returns_fixed_template(self):
        """UNKNOWN status → fixed '请求已提交，正在确认中…'."""
        adapter = RespondAdapter(FakeLLM("never called"))
        state = _make_state(
            execution=_fake_execution(status="UNKNOWN"),
        )
        result = adapter(state, None)
        assert "确认中" in result

    def test_deny_decision_returns_fixed_template(self):
        """DENY → fixed '该操作未被授权'."""
        adapter = RespondAdapter(FakeLLM("never called"))
        state = _make_state(
            decision={"decision": "DENY", "plan_id": "p1"},
            # No route_error → should still DENY
        )
        result = adapter(state, None)
        assert "未被授权" in result


# ── LLM-driven paths ──────────────────────────────────────────────────


class TestLLMPaths:
    def test_executed_calls_llm(self):
        """EXECUTED → LLM respond with utterance + step summary."""
        llm = FakeLLM("已向王五转账 800.00 元，余额 5,200.00 元。")
        adapter = RespondAdapter(llm)
        state = _make_state(
            execution=_fake_execution(status="EXECUTED"),
            utterances=[type("u", (), {"text": "向王五转账800元"})],
        )
        result = adapter(state, None)
        assert len(result) > 0
        assert len(llm.calls) == 1

    def test_failed_calls_llm(self):
        """FAILED → LLM explains which step failed."""
        llm = FakeLLM("转账失败，提示余额不足。")
        adapter = RespondAdapter(llm)
        state = _make_state(
            execution=_fake_execution(status="FAILED"),
        )
        result = adapter(state, None)
        assert len(result) > 0
        assert len(llm.calls) == 1

    def test_replan_user_calls_llm(self):
        """REPLAN + user resolver → LLM with replan_hints labels."""
        llm = FakeLLM("请选择收款账户。")
        adapter = RespondAdapter(llm)
        state = _make_state(
            decision={
                "decision": "REPLAN",
                "plan_id": "p1",
                "findings": [{"code": "AMBIGUOUS_PAYEE", "resolver": "user"}],
                "replan_hints": [{"label": "请选择收款人"}],
            },
        )
        result = adapter(state, None)
        assert len(result) > 0
        assert len(llm.calls) == 1

    def test_analysis_calls_llm(self):
        """No decision/execution → analysis LLM with reads summary."""
        llm = FakeLLM("您的余额是 6,000.00 元。")
        adapter = RespondAdapter(llm)
        read_record = ReadRecord(
            source_ref="src_abc123",
            tool="get_balances",
            args={},
            data={"balance_cents": 600000},
        )
        state = _make_state(
            reads=[read_record],
            utterances=[type("u", (), {"text": "查余额"})],
        )
        result = adapter(state, None)
        assert len(result) > 0
        assert len(llm.calls) == 1

    def test_llm_failure_fallback(self):
        """LLM raises → fallback template."""
        llm = FakeLLM(raises=RuntimeError("API down"))
        adapter = RespondAdapter(llm)
        state = _make_state(
            execution=_fake_execution(status="EXECUTED"),
        )
        result = adapter(state, None)
        assert len(result) > 0  # should use fallback
        # EXECUTED fallback is "操作已完成。"
        assert "操作已完成" in result

    def test_llm_empty_content_fallback(self):
        """LLM returns empty string → fallback."""
        llm = FakeLLM("")
        adapter = RespondAdapter(llm)
        state = _make_state(
            execution=_fake_execution(status="EXECUTED"),
        )
        result = adapter(state, None)
        assert len(result) > 0  # fallback

    def test_long_response_truncated(self):
        """LLM returns >600 chars → truncated."""
        long_text = "钱" * 700
        llm = FakeLLM(long_text)
        adapter = RespondAdapter(llm)
        state = _make_state(
            execution=_fake_execution(status="EXECUTED"),
        )
        result = adapter(state, None)
        assert len(result) <= 601  # max + "…"
        assert result.endswith("…")


# ── Pure helpers ──────────────────────────────────────────────────────


class TestExecStatus:
    def test_from_pydantic_model(self):
        from safebank_contracts.plan import ExecutionResult as ER

        obj = ER(
            plan_id="pln_X1Y2Z3ABCDEFGHJKMNPRSTVWZ01",
            status="EXECUTED",
            steps=(),
            versions={},
            committed_at="2026-10-25T10:00:00+08:00",
            error_code=None,
            replayed=False,
        )
        assert _exec_status(obj) == "EXECUTED"

    def test_from_dict(self):
        assert _exec_status({"status": "FAILED"}) == "FAILED"

    def test_none(self):
        assert _exec_status(None) is None


class TestExtractDecision:
    def test_from_dict(self):
        result = _extract_decision({"decision": {"decision": "ALLOW"}})
        assert result["decision"] == "ALLOW"

    def test_empty_state(self):
        assert _extract_decision({"no_decision": 1}) == {}


class TestHasUserResolver:
    def test_user_found(self):
        decision = {
            "findings": [
                {"code": "X1", "resolver": "agent"},
                {"code": "X2", "resolver": "user"},
            ]
        }
        assert _has_user_resolver(decision) is True

    def test_no_user(self):
        decision = {"findings": [{"code": "X1", "resolver": "agent"}]}
        assert _has_user_resolver(decision) is False

    def test_empty(self):
        assert _has_user_resolver({}) is False


class TestUtteranceText:
    def test_from_utterances(self):
        u1 = type("u", (), {"text": "向王五转账800元"})
        state = {"utterances": [u1]}
        assert "王五" in _utterance_text(state)


class TestExecutionStepsSummary:
    def test_with_steps(self):
        exec_obj = _fake_execution()
        result = _execution_steps_summary(exec_obj)
        assert "transfer" in result
        assert "SUCCESS" in result
        assert "txn_" in result


class TestReplanHintLabels:
    def test_dict_hints(self):
        decision = {
            "replan_hints": [
                {"label": "请选择收款人"},
                {"label": "请确认金额"},
            ]
        }
        labels = _replan_hint_labels(decision)
        assert labels == ["请选择收款人", "请确认金额"]

    def test_empty(self):
        assert _replan_hint_labels({}) == []


class TestReadsSummary:
    def test_multiple_reads(self):
        reads = [
            ReadRecord(
                source_ref="src_a",
                tool="get_balances",
                args={},
                data={"balance_cents": 600000},
            ),
            ReadRecord(
                source_ref="src_b",
                tool="get_accounts",
                args={},
                data={"accounts": []},
            ),
        ]
        state = {"reads": reads}
        result = _reads_summary(state)
        assert "get_balances" in result
        assert "get_accounts" in result
        assert "600000" in result

    def test_empty(self):
        result = _reads_summary({})
        assert "无读取数据" in result


class TestBuildPrompt:
    def test_builds_sections(self):
        result = _build_prompt("system", utterance="hello", extra="world")
        assert "【utterance】" in result
        assert "hello" in result
        assert "【extra】" in result
        assert "world" in result

    def test_skips_empty(self):
        result = _build_prompt("system", utterance="hi", execution_summary="")
        assert "【execution_summary】" not in result


class TestBuildFallback:
    def test_executed(self):
        result = _build_fallback("EXECUTED")
        assert "操作已完成" in result

    def test_executed_with_steps(self):
        result = _build_fallback("EXECUTED", steps=["转账 SUCCESS"])
        assert "操作已完成" in result
        assert "转账" in result


class TestSafeTruncate:
    def test_short(self):
        assert _safe_truncate("hi", 10) == "hi"

    def test_exact(self):
        assert _safe_truncate("12345", 5) == "12345"

    def test_long(self):
        result = _safe_truncate("1234567890", 5)
        assert result == "12345…"
