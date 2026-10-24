"""C1d checkpoint isolation tests — tables, idempotent setup, round-trip, token absence.

Uses a temporary PostgreSQL container (tmpfs, destroyed on exit).  Skipped unless
``AGENT_DB_TESTS=1``.
"""

from __future__ import annotations

import asyncio

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from services.agent.app.graph import (
    AgentContext,
    Decision,
    DecisionValue,
    Ports,
    Understanding,
    UtteranceRef,
    build_graph,
)
from tests.agent.test_graph import config, ctx, draft, execution, utterance

pytestmark = pytest.mark.db


# ── Fake ports ──────────────────────────────────────────────────────────


def _fake_ports(gw_decision: Decision) -> Ports:
    """Return Ports whose evaluate always returns *gw_decision*."""
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


# ── Task 1: fixture and setup ───────────────────────────────────────────


def test_checkpointer_setup_creates_tables(agent_pg):
    """AsyncPostgresSaver.setup() creates checkpoints, checkpoint_writes, checkpoint_blobs."""
    async def _verify():
        async with AsyncPostgresSaver.from_conn_string(agent_pg.runtime_url) as saver:
            await saver.setup()
        # Verify tables exist via psql
        import subprocess
        result = subprocess.run(
            [
                "docker", "exec", agent_pg.name,
                "psql", "-U", "agent_app", "-d", "agent",
                "-c", r"\dt"
            ],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        output = result.stdout
        assert "checkpoints" in output
        assert "checkpoint_writes" in output
        assert "checkpoint_blobs" in output

    asyncio.run(_verify())


def test_repeat_setup_is_idempotent(agent_pg):
    """Calling setup() twice does not raise an error."""
    async def _verify():
        async with AsyncPostgresSaver.from_conn_string(agent_pg.runtime_url) as saver:
            await saver.setup()
            await saver.setup()  # second call must not raise

    asyncio.run(_verify())


def test_checkpoint_round_trip(agent_pg):
    """graph.ainvoke(...) persists state, and a new graph instance can recover it with ainvoke."""
    dec = Decision(plan_id="pln_a", decision=DecisionValue.ALLOW)

    async def _run():
        async with AsyncPostgresSaver.from_conn_string(agent_pg.runtime_url) as saver:
            await saver.setup()
            graph = build_graph(_fake_ports(dec), saver)
            result = await graph.ainvoke(
                {"utterances": [utterance()]}, config=config(), context=ctx()
            )
            assert result["plan_id"] == "pln_a"

            # Rebuild graph with a new saver from the same DB
            async with AsyncPostgresSaver.from_conn_string(agent_pg.runtime_url) as saver2:
                graph2 = build_graph(_fake_ports(dec), saver2)
                state = await graph2.aget_state(config())
                assert state is not None
                assert state.values.get("plan_id") == "pln_a"

    asyncio.run(_run())