"""C1d checkpoint isolation tests — tables, idempotent setup, round-trip, token absence,
thread isolation.

Uses a temporary PostgreSQL container (tmpfs, destroyed on exit).  Skipped unless
``AGENT_DB_TESTS=1``.
"""

from __future__ import annotations

import asyncio
import secrets

import psycopg
import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from services.agent.app.graph import (
    AgentContext,
    Decision,
    DecisionValue,
    Ports,
    Understanding,
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


# ── Helpers ─────────────────────────────────────────────────────────────


async def _run_full_round(agent_pg, thread_id="ses_a", customer="cus_a",
                          decision_value=DecisionValue.ALLOW) -> AgentContext:
    """Run a complete round through the graph and return the context."""
    dec = Decision(plan_id=f"pln_{secrets.token_hex(4)}", decision=decision_value,
                   approval_id="apv_a" if decision_value == DecisionValue.REQUIRE_APPROVAL else None)
    c = ctx(customer=customer, session=thread_id, token="tok_assistant_test_12345")
    cfg = config(session=thread_id)
    async with AsyncPostgresSaver.from_conn_string(agent_pg.runtime_url) as saver:
        await saver.setup()
        graph = build_graph(_fake_ports(dec), saver)
        result = await graph.ainvoke(
            {"utterances": [utterance(uid=f"utt_{secrets.token_hex(4)}")]},
            config=cfg, context=c,
        )
    return c


# ── Task 1: fixture and setup ───────────────────────────────────────────


def test_checkpointer_setup_creates_tables(agent_pg):
    """AsyncPostgresSaver.setup() creates checkpoints, checkpoint_writes, checkpoint_blobs."""
    async def _verify():
        async with AsyncPostgresSaver.from_conn_string(agent_pg.runtime_url) as saver:
            await saver.setup()
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
            await saver.setup()

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

            async with AsyncPostgresSaver.from_conn_string(agent_pg.runtime_url) as saver2:
                graph2 = build_graph(_fake_ports(dec), saver2)
                state = await graph2.aget_state(config())
                assert state is not None
                assert state.values.get("plan_id") == "pln_a"

    asyncio.run(_run())


# ── Task 2: token absence + thread isolation ────────────────────────────


def test_checkpoint_has_no_token_after_full_round(agent_pg):
    """After a full round (ALLOW), no token appears in checkpoint tables."""
    token = "tok_assistant_test_full_round_abc"
    async def _run():
        dec = Decision(plan_id="pln_tok", decision=DecisionValue.ALLOW)
        c = AgentContext(assistant_token=token, customer_id="cus_a", session_id="ses_tok")
        async with AsyncPostgresSaver.from_conn_string(agent_pg.runtime_url) as saver:
            await saver.setup()
            graph = build_graph(_fake_ports(dec), saver)
            await graph.ainvoke(
                {"utterances": [utterance(uid="utt_tok")]},
                config=config(session="ses_tok"), context=c,
            )
        # Query all three checkpoint tables for token strings
        conn = psycopg.connect(agent_pg.runtime_url)
        try:
            for table in ("checkpoints", "checkpoint_writes", "checkpoint_blobs"):
                # Cast to text and search
                cur = conn.execute(
                    f"SELECT count(*) FROM {table} WHERE {table}::text LIKE %s",
                    (f"%{token}%",)
                )
                count = cur.fetchone()[0]
                assert count == 0, f"Token found in {table}: {count} rows"
        finally:
            conn.close()

    asyncio.run(_run())


def test_thread_isolation(agent_pg):
    """Two different thread_ids have isolated state."""
    async def _run():
        dec_a = Decision(plan_id="pln_iso_a", decision=DecisionValue.ALLOW)
        dec_b = Decision(plan_id="pln_iso_b", decision=DecisionValue.ALLOW)
        async with AsyncPostgresSaver.from_conn_string(agent_pg.runtime_url) as saver:
            await saver.setup()
            graph_a = build_graph(_fake_ports(dec_a), saver)
            await graph_a.ainvoke(
                {"utterances": [utterance(uid="utt_iso_a")]},
                config=config(session="ses_iso_a"), context=ctx(session="ses_iso_a"),
            )
            graph_b = build_graph(_fake_ports(dec_b), saver)
            await graph_b.ainvoke(
                {"utterances": [utterance(uid="utt_iso_b")]},
                config=config(session="ses_iso_b"), context=ctx(session="ses_iso_b"),
            )
        # Verify isolation
        async with AsyncPostgresSaver.from_conn_string(agent_pg.runtime_url) as saver:
            state_a = await saver.aget_tuple(config(session="ses_iso_a"))
            state_b = await saver.aget_tuple(config(session="ses_iso_b"))
            assert state_a is not None
            assert state_b is not None
            assert state_a.config["configurable"]["thread_id"] == "ses_iso_a"
            assert state_b.config["configurable"]["thread_id"] == "ses_iso_b"

    asyncio.run(_run())