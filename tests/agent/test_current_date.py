"""Tests for L1 business-date injection in gather and plan prompts.

The L1 is never told the current business date, so relative dates (9月, 上个月,
最近, 下周) can resolve to the wrong year in the model's context window.
Fix: at the start of the gather step, perform a ``get_profile`` read (if not
already present in state.reads), extract ``sim_now``, and inject a date line
into both the gather and plan prompts.
"""

from __future__ import annotations

import datetime as dt

import pytest

from services.agent.app.graph.state import AgentContext, ReadRecord, Understanding, UtteranceRef
from services.agent.app.llm.gather import GatherAdapter, ReadResult
from services.agent.app.llm.plan import PlanAdapter
from tests.agent.llm_mock import ScriptedLLM, text_response, tool_response


def ctx() -> AgentContext:
    return AgentContext(assistant_token="SECRET_TOKEN", customer_id="cus_a", session_id="ses_a")


def plan_state(reads=None):
    return {
        "customer_id": "cus_a",
        "session_id": "ses_a",
        "utterances": [
            UtteranceRef(id="utt_typed", kind="typed", text="9月有没有异常交易"),
        ],
        "understanding": Understanding(intents=["detect_anomalies"], needs_write=False),
        "reads": reads or [],
    }


def gather_state(reads=None):
    return {
        "customer_id": "cus_a",
        "session_id": "ses_a",
        "utterances": [
            UtteranceRef(id="utt_a", kind="typed", text="9月有没有异常交易"),
        ],
        "understanding": Understanding(intents=["detect_anomalies"], needs_write=False),
        "reads": reads or [],
    }


def profile_read_result(sim_now="2026-10-15T09:00:00+08:00"):
    return ReadResult(
        data={
            "customer_id": "cus_a",
            "display_name": "张三",
            "phone_masked": "138****1234",
            "risk_profile": {
                "risk_level": "R2",
                "risk_valid_until": "2026-12-31T23:59:59+08:00",
            },
            "sim_now": sim_now,
        },
        source_ref="src_profile",
        observed_at="2026-10-15T09:00:00+08:00",
    )


# ---------------------------------------------------------------------------
# gather date-line tests
# ---------------------------------------------------------------------------


class TestGatherBusinessDate:
    def test_get_profile_called_once_per_turn(self):
        """get_profile is read once at gather start when not already in state.

        The profile read is NOT returned as a normal ReadRecord — only the
        formatted date string is stored in ``state["business_date"]``.
        """
        read_log = []

        def executor(name, args):
            read_log.append((name, args))
            return profile_read_result()

        scripted = ScriptedLLM([text_response("查不到异常")])
        from services.agent.app.llm.client import BETA_API_BASE, MODEL, make_llm

        llm = make_llm(api_key="test-key", http_client=scripted.client)
        st = gather_state()
        result = GatherAdapter(llm, executor)(st, ctx())
        # Model returned no tool calls → no new reads
        assert len(result) == 0
        # get_profile was still called by the date resolver
        assert ("get_profile", {}) in read_log
        # The business date was stored in state
        assert "当前业务时间：2026-10-15 09:00" in st.get("business_date", "")

    def test_date_line_in_gather_prompt(self):
        """The business date line appears in the gather system+user prompt."""
        read_log = []

        def executor(name, args):
            read_log.append((name, args))
            return profile_read_result()

        scripted = ScriptedLLM([text_response("enough")])
        from services.agent.app.llm.client import BETA_API_BASE, MODEL, make_llm

        llm = make_llm(api_key="test-key", http_client=scripted.client)
        GatherAdapter(llm, executor)(gather_state(), ctx())

        # Check that the date line appears in the prompt messages
        assert len(scripted.requests) >= 1
        all_text = ""
        for msg in scripted.requests[0]["body"]["messages"]:
            all_text += msg.get("content", "")
        assert "当前业务时间：2026-10-15 09:00" in all_text
        assert "Asia/Shanghai" in all_text

    def test_no_duplicate_get_profile_when_present(self):
        """Do not call get_profile again if already in state.reads."""
        existing = [
            ReadRecord(
                source_ref="src_prev",
                tool="get_profile",
                args={},
                data=profile_read_result().data,
            )
        ]
        read_log = []

        def executor(name, args):
            read_log.append((name, args))
            return profile_read_result()

        scripted = ScriptedLLM([text_response("enough")])
        from services.agent.app.llm.client import BETA_API_BASE, MODEL, make_llm

        llm = make_llm(api_key="test-key", http_client=scripted.client)
        result = GatherAdapter(llm, executor)(gather_state(reads=existing), ctx())
        # Should NOT have called get_profile via executor
        assert ("get_profile", {}) not in read_log
        # The existing record stays in state; gather returns only *new* reads
        profile_records = [r for r in result if r.tool == "get_profile"]
        assert len(profile_records) == 0
        # Date line still appears in prompt (extracted from existing read)
        all_text = ""
        for msg in scripted.requests[0]["body"]["messages"]:
            all_text += msg.get("content", "")
        assert "当前业务时间：2026-10-15 09:00" in all_text

    def test_failing_profile_read_does_not_block(self):
        """When get_profile fails, gather continues without the date line."""
        def executor(name, args):
            raise RuntimeError("L3 unavailable")

        scripted = ScriptedLLM([text_response("enough")])
        from services.agent.app.llm.client import BETA_API_BASE, MODEL, make_llm

        llm = make_llm(api_key="test-key", http_client=scripted.client)
        result = GatherAdapter(llm, executor)(gather_state(), ctx())
        # Should still have completed — the model's text response is collected
        assert len(scripted.requests) >= 1
        # No date line in prompt
        all_text = ""
        for msg in scripted.requests[0]["body"]["messages"]:
            all_text += msg.get("content", "")
        assert "当前业务时间" not in all_text


# ---------------------------------------------------------------------------
# plan date-line tests
# ---------------------------------------------------------------------------


class TestPlanBusinessDate:
    def test_date_line_in_plan_prompt(self):
        """The business date line appears in the plan prompt when business_date is set."""
        scripted = ScriptedLLM(
            [
                tool_response(
                    "submit_action_plan",
                    {
                        "intents": ["transfer"],
                        "steps": [
                            {
                                "step_id": "s1",
                                "tool": "transfer",
                                "args": {
                                    "from_account_id": "acc_a",
                                    "payee_id": {"kind": "id", "id": "pye_b"},
                                    "to_account_id": {"kind": "none"},
                                    "amount_cents": 80000,
                                    "memo": "__NULL__",
                                },
                                "depends_on": [],
                                "arg_sources": [],
                            }
                        ],
                        "rationale": "test",
                    },
                ),
            ]
        )
        from services.agent.app.llm.client import BETA_API_BASE, MODEL, make_llm

        llm = make_llm(api_key="test-key", http_client=scripted.client)

        st = plan_state()
        st["business_date"] = "当前业务时间：2026-10-15 09:00（北京时间，Asia/Shanghai）"
        PlanAdapter(llm)(st, ctx())

        assert len(scripted.requests) >= 1
        all_text = ""
        for msg in scripted.requests[0]["body"]["messages"]:
            all_text += msg.get("content", "")
        assert "当前业务时间：2026-10-15 09:00" in all_text

    def test_no_date_line_without_profile_read(self):
        """Plan prompt does NOT include date line when no get_profile read exists."""
        scripted = ScriptedLLM(
            [
                tool_response(
                    "submit_action_plan",
                    {
                        "intents": ["transfer"],
                        "steps": [
                            {
                                "step_id": "s1",
                                "tool": "transfer",
                                "args": {
                                    "from_account_id": "acc_a",
                                    "payee_id": {"kind": "id", "id": "pye_b"},
                                    "to_account_id": {"kind": "none"},
                                    "amount_cents": 80000,
                                    "memo": "__NULL__",
                                },
                                "depends_on": [],
                                "arg_sources": [],
                            }
                        ],
                        "rationale": "test",
                    },
                ),
            ]
        )
        from services.agent.app.llm.client import BETA_API_BASE, MODEL, make_llm

        llm = make_llm(api_key="test-key", http_client=scripted.client)

        PlanAdapter(llm)(plan_state(reads=[]), ctx())

        assert len(scripted.requests) >= 1
        all_text = ""
        for msg in scripted.requests[0]["body"]["messages"]:
            all_text += msg.get("content", "")
        assert "当前业务时间" not in all_text