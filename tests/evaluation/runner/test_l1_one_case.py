"""E0b T2b: one real L1 graph invocation reaches the real gateway G4.

Builds the compiled L1 graph with fake LLM ports (freeze the customer's single
card) and runs it against the real gateway through the in-process bridge.  The
assertions only care about the G4 exchange: a recorded POST /plans/evaluate
with HTTP 200 and a valid Decision, plus the persisted plan row and a real
(non-fake) L3 client.  G8 is still a stub, so any exception from the tail of
the graph is tolerated — the decision is already captured by then.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import text

from evaluation.runner.harness import eval_harness
from evaluation.runner.l1 import build_real_gateway_graph
from services.agent.app.graph import AgentContext, UtteranceRef
from services.gateway.app.l3.client import L3Client
from services.gateway.app.l3.fake import FakeL3

FIXTURE = Path(__file__).parent / "fixtures" / "mini_seed.json"

VALID_DECISIONS = {"ALLOW", "REQUIRE_APPROVAL", "REPLAN", "DENY"}


def test_l1_graph_reaches_real_g4(gateway_db, obligations_db, monkeypatch):
    async def run():
        async with eval_harness(gateway_db, obligations_db, FIXTURE, monkeypatch) as h:
            assert isinstance(h.app.state.l3, L3Client)
            assert not isinstance(h.app.state.l3, FakeL3)

            sid = await h.new_session("cus_zs")
            utterance_id, assistant_jwt = await h.register_utterance(sid, "冻结我的卡")

            graph, transport = build_real_gateway_graph(
                h, loop=asyncio.get_running_loop(), utterance_id=utterance_id
            )

            def _invoke():
                return graph.invoke(
                    {"utterances": [UtteranceRef(id=utterance_id, kind="typed", text="")]},
                    config={"configurable": {"thread_id": sid}},
                    context=AgentContext(
                        assistant_token=assistant_jwt,
                        customer_id="cus_zs",
                        session_id=sid,
                    ),
                )

            # G8 is a stub: the tail of the graph may raise after G4; we only
            # need the /plans/evaluate decision captured in the transport log.
            try:
                await asyncio.to_thread(_invoke)
            except Exception as exc:  # noqa: BLE001 — tolerated per task brief
                print("T2b graph tail error (tolerated):", repr(exc))

            evaluates = [
                r
                for r in transport.requests
                if r["method"] == "POST" and "/plans/evaluate" in r["url"]
            ]
            assert evaluates, f"no /plans/evaluate recorded: {transport.requests}"

            evaluate = evaluates[0]
            assert evaluate["status"] == 200, evaluate
            body = evaluate["json"]
            assert body is not None, evaluate
            decision = body["decision"]
            assert decision in VALID_DECISIONS, decision
            print("T2b decision:", decision)

            plan_id = body.get("plan_id")
            assert plan_id, body
            async with h.database.connect() as conn:
                count = await conn.scalar(
                    text("SELECT count(*) FROM plans WHERE id = :id"), {"id": plan_id}
                )
                assert count == 1, f"plan {plan_id} not persisted"

    asyncio.run(run())
