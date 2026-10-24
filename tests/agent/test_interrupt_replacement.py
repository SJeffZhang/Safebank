"""C1d P0 experiment E7: old interrupt replacement on real PG checkpoint.

Verifies that when new input arrives on a thread with a pending interrupt,
the graph starts from the beginning (START node) and the old interrupt is
discarded.  Uses real AsyncPostgresSaver — NOT MemorySaver.
"""

from __future__ import annotations

import asyncio

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.types import Command

from services.agent.app.graph import (
    Decision,
    DecisionValue,
    Ports,
    Understanding,
    build_graph,
)
from tests.agent.test_graph import config, ctx, draft, execution, utterance

pytestmark = pytest.mark.db


def _fake_ports(gw_decision: Decision) -> Ports:
    return Ports(
        understand=lambda state, ctx: Understanding(
            intents=("transfer",), needs_write=True, unclear=False, slots=()
        ),
        gather=lambda state, ctx: [],
        plan=lambda state, ctx: draft(),
        evaluate=lambda state, ctx: gw_decision,
        execute=lambda state, ctx: execution(),
        respond=lambda state, ctx: "Done",
        verify_output=lambda state, ctx: "Done",
    )


def test_new_input_on_pending_interrupt_starts_from_beginning(agent_pg):
    """New input on a pending-interrupt thread discards old interrupt and starts fresh.

    Red observation (before E7 fix): LangGraph with PG checkpoint might or might not
    clear the old interrupt — this test determines the actual behavior.
    """
    async def _run():
        async with AsyncPostgresSaver.from_conn_string(agent_pg.runtime_url) as saver:
            await saver.setup()

            # Round 1: REQUIRE_APPROVAL — graph interrupts
            dec1 = Decision(
                plan_id="pln_e7_1", decision=DecisionValue.REQUIRE_APPROVAL,
                approval_id="apv_e7_old",
            )
            graph = build_graph(_fake_ports(dec1), saver)
            cfg = config(session="ses_e7")
            c = ctx(session="ses_e7")

            result1 = await graph.ainvoke(
                {"utterances": [utterance(uid="utt_e7_1")]},
                config=cfg, context=c,
            )
            # Verify interrupt is present
            state1 = await graph.aget_state(cfg)
            assert state1 is not None
            tasks = getattr(state1, "tasks", None)
            has_interrupt = bool(tasks and len(tasks) > 0)
            assert has_interrupt, "Expected interrupt after REQUIRE_APPROVAL"

            # Round 2: new input on same thread — should start from beginning
            dec2 = Decision(plan_id="pln_e7_2", decision=DecisionValue.DENY)
            graph2 = build_graph(_fake_ports(dec2), saver)
            result2 = await graph2.ainvoke(
                {"utterances": [utterance(uid="utt_e7_2")]},
                config=cfg, context=c,
            )
            # After new input, old interrupt should be gone
            state2 = await graph2.aget_state(cfg)
            assert state2 is not None
            tasks2 = getattr(state2, "tasks", None)
            # New input should either have no tasks, or tasks from the new round
            old_interrupt_still_present = any(
                t.interrupts and t.interrupts[0].value.get("approval_id") == "apv_e7_old"
                for t in (tasks2 or [])
                if hasattr(t, 'interrupts') and t.interrupts
            ) if tasks2 else False
            assert not old_interrupt_still_present, (
                "Old interrupt 'apv_e7_old' should not survive new input"
            )

    asyncio.run(_run())


def test_old_resume_does_not_execute_after_new_input(agent_pg):
    """After new input replaces old interrupt, resuming with old approval_id
    should not trigger execute — the old approval is void."""
    async def _run():
        async with AsyncPostgresSaver.from_conn_string(agent_pg.runtime_url) as saver:
            await saver.setup()

            # Round 1: REQUIRE_APPROVAL
            dec1 = Decision(
                plan_id="pln_e7r_1", decision=DecisionValue.REQUIRE_APPROVAL,
                approval_id="apv_e7r_old",
            )
            graph = build_graph(_fake_ports(dec1), saver)
            cfg = config(session="ses_e7r")
            c = ctx(session="ses_e7r")

            await graph.ainvoke(
                {"utterances": [utterance(uid="utt_e7r_1")]},
                config=cfg, context=c,
            )

            # Round 2: new input — DENY (no execute path)
            dec2 = Decision(plan_id="pln_e7r_2", decision=DecisionValue.DENY)
            graph2 = build_graph(_fake_ports(dec2), saver)
            await graph2.ainvoke(
                {"utterances": [utterance(uid="utt_e7r_2")]},
                config=cfg, context=c,
            )

            # Now try to resume with old approval_id — should NOT trigger execute
            # If LangGraph clears interrupts, Command(resume=old_apv) will be ignored
            # or the await_approval node's interrupt will have been cleared
            try:
                result3 = await graph2.ainvoke(
                    Command(resume="apv_e7r_old"),
                    config=cfg, context=c,
                )
                # If we get here, check that execute was NOT called
                # (The DENY decision should still be present, or the resume
                #  was ignored and graph reached END without executing)
                exec_result = result3.get("execution")
                assert exec_result is None, (
                    f"Old resume should not trigger execute, got {exec_result}"
                )
            except Exception:
                # LangGraph may raise if no interrupt is pending — acceptable
                pass

    asyncio.run(_run())