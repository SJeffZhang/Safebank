"""E0b T5: evaluate-only execution boundary — G4 decisions without G8 side effects.

Verifies that the evaluation harness can run the real L1 graph through
the real L2 G4 (evaluate) and capture decisions without ever calling
G8 (execute) or touching the real L3.

The core mechanism: ``_EvaluateOnlyExecute`` raises ``EvaluationComplete`` so the
graph stops at G4.  ``run_corpus`` catches this and classifies the outcome from
the transport's recorded ``/plans/evaluate`` response.  The transport also
rejects G8 requests as a belt-and-suspenders guard.

Tests assert on transport records + real L3 database state to prove no
execution occurred.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import httpx
from sqlalchemy import text

from evaluation.runner.harness import eval_harness
from evaluation.runner.l1 import (
    EvaluationComplete,
    RecordingBridgeTransport,
    build_evaluate_only_graph,
)
from evaluation.runner.loop import run_corpus
from services.agent.app.gateway.client import GatewayClient
from services.agent.app.graph import AgentContext, UtteranceRef
from services.agent.app.graph.state import ReadRecord

FIXTURE = Path(__file__).parent / "fixtures" / "mini_seed.json"

A_TOKEN = "a-test"


# ── shared port helpers ──────────────────────────────────────────────────


def _understand(state, context):
    from services.agent.app.graph import Understanding

    intents = state.get("intents_hint", ["freeze_card"])
    return Understanding(intents=list(intents), needs_write=True)


def _respond(state, context):
    return "已完成"


def _verify(state, context):
    return state.get("reply_draft", "已完成")


# ── gather helpers (call real G2 inside the graph's to_thread) ───────────


def _make_gather_get_cards(client: GatewayClient):
    """Gather that calls real G2 get_cards with account_id=None.

    Runs inside the graph's ``to_thread``, so the RecordingBridgeTransport
    forwards the request to the harness's event loop without deadlock.
    """

    def gather(state, context):
        result = client.read_tool("get_cards", {"account_id": None}, context)
        items = result.data.get("items", [])
        assert items, "no cards returned from G2"
        return [
            ReadRecord(
                source_ref=result.source_ref,
                tool="get_cards",
                args={"account_id": None},
                data=result.data,
                untrusted_paths=tuple(result.untrusted_paths),
                versions=dict(result.versions) if result.versions else {},
                observed_at=result.observed_at if result.observed_at else "",
            )
        ]

    return gather


# ── plan helpers (read card_id from state['reads']) ──────────────────────


def _make_plan_freeze():
    """Plan: freeze_card with card_id sourced from the G2 read record."""

    def plan(state, context):
        reads = state.get("reads", [])
        assert reads, "no reads in state for freeze_card plan"
        utterances = state.get("utterances", [])
        assert utterances, "no utterances in state for freeze_card plan"
        utt_id = utterances[0].id
        card = reads[0].data["items"][0]
        card_id = card["id"]
        source_ref = reads[0].source_ref
        from safebank_contracts.plan import PlanDraft

        return PlanDraft(
            request_id=uuid.uuid4().hex,
            session_id=state["session_id"],
            utterance_ids=(utt_id,),
            intents=("freeze_card",),
            steps=[
                {
                    "step_id": "s1",
                    "tool": "freeze_card",
                    "args": {"card_id": card_id},
                    "depends_on": [],
                    "arg_sources": [
                        {
                            "param": "card_id",
                            "source": {
                                "kind": "read",
                                "ref": source_ref,
                                "pointer": "/items/0/id",
                            },
                        }
                    ],
                }
            ],
            rationale="e0b t5 evaluate-only freeze",
        )

    return plan


def _make_plan_tighten():
    """Plan: update_card_limits lowering single limit to 100000 cents (1000 元).

    card_id comes from a get_cards read record (pointer=/items/0/id).
    single_limit_cents=100000 comes from the utterance span covering '1000'.
    """

    def plan(state, context):
        reads = state.get("reads", [])
        assert reads, "no reads in state for tighten plan"
        utterances = state.get("utterances", [])
        assert utterances, "no utterances in state"
        utt_id = utterances[0].id
        text = utterances[0].text
        idx = text.index("1000")
        card = reads[0].data["items"][0]
        card_id = card["id"]
        source_ref = reads[0].source_ref
        from safebank_contracts.plan import PlanDraft

        return PlanDraft(
            request_id=uuid.uuid4().hex,
            session_id=state["session_id"],
            utterance_ids=(utt_id,),
            intents=("update_card_limits",),
            steps=[
                {
                    "step_id": "s1",
                    "tool": "update_card_limits",
                    "args": {
                        "card_id": card_id,
                        "single_limit_cents": 100000,
                        "daily_limit_cents": None,
                    },
                    "depends_on": [],
                    "arg_sources": [
                        {
                            "param": "card_id",
                            "source": {
                                "kind": "read",
                                "ref": source_ref,
                                "pointer": "/items/0/id",
                            },
                        },
                        {
                            "param": "single_limit_cents",
                            "source": {
                                "kind": "utterance",
                                "ref": utt_id,
                                "span": (idx, idx + 4),
                            },
                        },
                    ],
                }
            ],
            rationale="e0b t5 evaluate-only tighten limits",
        )

    return plan


# ── transport / client helper ────────────────────────────────────────────


def _make_transport_and_client(h, loop):
    """Create a RecordingBridgeTransport + GatewayClient pair for the test.

    The transport is created with ``evaluate_only=True`` so it rejects any
    G8 requests at the transport layer as a belt-and-suspenders guard.
    Both the graph's evaluate port and the test's gather closure share this
    client so all gateway calls are recorded in the same transport log.
    """
    transport = RecordingBridgeTransport(h.client, loop, evaluate_only=True)
    http_client = httpx.Client(transport=transport, base_url="http://gateway")
    client = GatewayClient("http://gateway", A_TOKEN, http_client=http_client)
    return transport, client


# ── assert helpers ───────────────────────────────────────────────────────


async def _snapshot_l3(h):
    """Capture L3 state for before/after comparison."""
    async with h._admin.connect() as conn:
        card_status = await conn.scalar(text("SELECT status FROM cards WHERE id = 'crd_zs_1'"))
        plan_execs = await conn.scalar(text("SELECT count(*) FROM plan_executions"))
        txns = await conn.scalar(
            text("SELECT count(*) FROM transactions WHERE account_id = 'acc_zs_chk'")
        )
    return card_status, plan_execs, txns


def _assert_no_g8(transport_requests):
    """Assert zero G8 execute calls in the transport log."""
    executes = [
        r
        for r in transport_requests
        if r.get("method") == "POST"
        and "/plans/" in r.get("url", "")
        and "/execute" in r.get("url", "")
    ]
    assert not executes, f"G8 execute was called: {executes}"


async def _assert_l3_unchanged(h, before, label=""):
    """Assert L3 state is identical to before."""
    after_card, after_execs, after_txns = await _snapshot_l3(h)
    b_card, b_execs, b_txns = before
    assert after_card == b_card, f"{label}card changed: {b_card} → {after_card}"
    assert after_execs == b_execs, f"{label}plan_executions changed: {b_execs} → {after_execs}"
    assert after_txns == b_txns, f"{label}transactions changed: {b_txns} → {after_txns}"


# ── tests ────────────────────────────────────────────────────────────────


def test_evaluate_only_allows_freeze_no_execute(gateway_db, obligations_db, monkeypatch):
    """ALLOW (green) freeze_card: graph stops after G4, never calls G8.

    Gather calls real G2 get_cards inside ``to_thread``.  Plan reads card_id
    from ``state['reads']``.  The evaluate-only execute port raises
    ``EvaluationComplete``.  Assert G4 decision is exactly ALLOW, card stays
    ACTIVE, and L3 has zero new plan_executions or transactions.
    """

    async def run():
        async with eval_harness(gateway_db, obligations_db, FIXTURE, monkeypatch) as h:
            loop = asyncio.get_running_loop()
            sid = await h.new_session("cus_zs")
            utt_id, assistant_jwt, _g3 = await h.register_utterance(sid, "冻结我的卡")

            # snapshot L3 before
            before = await _snapshot_l3(h)

            # build evaluate-only graph
            transport, client = _make_transport_and_client(h, loop)
            gather = _make_gather_get_cards(client)
            plan_fn = _make_plan_freeze()

            graph, eval_exec = build_evaluate_only_graph(
                h,
                loop=loop,
                client=client,
                ports_kwargs=dict(
                    understand=_understand,
                    gather=gather,
                    plan=plan_fn,
                    respond=_respond,
                    verify_output=_verify,
                ),
            )

            # invoke — execute port raises EvaluationComplete
            def _invoke():
                return graph.invoke(
                    {"utterances": [UtteranceRef(id=utt_id, kind="typed", text="冻结我的卡")]},
                    config={"configurable": {"thread_id": sid}},
                    context=AgentContext(
                        assistant_token=assistant_jwt,
                        customer_id="cus_zs",
                        session_id=sid,
                    ),
                )

            # EvaluationComplete is expected — graph stops at G4
            raised = False
            try:
                await asyncio.to_thread(_invoke)
            except EvaluationComplete:
                raised = True
            assert raised, "EvaluationComplete was not raised — graph may have executed"

            # assert G4 decision is exactly ALLOW
            evaluates = [
                r
                for r in transport.requests
                if r["method"] == "POST" and "/plans/evaluate" in r["url"]
            ]
            assert evaluates, f"no /plans/evaluate in {transport.requests}"
            decision = evaluates[0]["json"]["decision"]
            assert decision == "ALLOW", f"expected ALLOW, got {decision}"

            # assert zero G8 calls
            _assert_no_g8(transport.requests)

            # assert L3 unchanged
            await _assert_l3_unchanged(h, before, label="ALLOW ")

    asyncio.run(run())


def test_yellow_plan_stops_at_approval_no_execute(gateway_db, obligations_db, monkeypatch):
    """YELLOW (REQUIRE_APPROVAL) plan: graph stops at G4, no G8, no L3 change.

    Uses update_card_limits to lower single_limit from 500000→100000.
    The card_id comes from a real G2 get_cards read inside the graph's gather.

    REQUIRE_APPROVAL triggers a LangGraph interrupt — the graph never reaches
    the execute node, so ``EvaluationComplete`` is NOT raised.  The graph
    returns normally with the interrupt in state.
    """

    async def run():
        async with eval_harness(gateway_db, obligations_db, FIXTURE, monkeypatch) as h:
            loop = asyncio.get_running_loop()
            sid = await h.new_session("cus_zs")
            utt_id, assistant_jwt, _g3 = await h.register_utterance(
                sid, "把我的卡单笔限额调到1000元"
            )

            before = await _snapshot_l3(h)

            transport, client = _make_transport_and_client(h, loop)
            gather = _make_gather_get_cards(client)
            plan_fn = _make_plan_tighten()

            graph, eval_exec = build_evaluate_only_graph(
                h,
                loop=loop,
                client=client,
                ports_kwargs=dict(
                    understand=_understand,
                    gather=gather,
                    plan=plan_fn,
                    respond=_respond,
                    verify_output=_verify,
                ),
            )

            def _invoke():
                return graph.invoke(
                    {
                        "utterances": [
                            UtteranceRef(id=utt_id, kind="typed", text="把我的卡单笔限额调到1000元")
                        ]
                    },
                    config={"configurable": {"thread_id": sid}},
                    context=AgentContext(
                        assistant_token=assistant_jwt,
                        customer_id="cus_zs",
                        session_id=sid,
                    ),
                )

            # REQUIRE_APPROVAL triggers a LangGraph interrupt: graph.invoke
            # returns normally with the interrupt in state.  The execute node
            # is never reached → EvaluationComplete is NOT raised.
            last_state = await asyncio.to_thread(_invoke)
            # Real system: if L2 creates FORM_REQUIRED clarification, record it
            # honestly in the interrupt state — do not fake scope to bypass.
            _cl = last_state.get("clarification") if last_state else None
            assert _cl is None, (
                f"unexpected FORM_REQUIRED clarification: {_cl}; "
                "L1 may have resolved to CLARIFY instead of REQUIRE_APPROVAL"
            )

            evaluates = [
                r
                for r in transport.requests
                if r["method"] == "POST" and "/plans/evaluate" in r["url"]
            ]
            assert evaluates, f"no /plans/evaluate in {transport.requests}"
            decision = evaluates[0]["json"]["decision"]
            assert decision == "REQUIRE_APPROVAL", f"expected REQUIRE_APPROVAL, got {decision}"

            # No confirmation / G8 / G10 — zero L3 change
            _assert_no_g8(transport.requests)
            await _assert_l3_unchanged(h, before, label="YELLOW ")

    asyncio.run(run())


def test_evaluate_only_run_corpus_connected(gateway_db, obligations_db, monkeypatch):
    """Prove the evaluate-only boundary is wired into ``run_corpus``.

    An ALLOW freeze_card case is driven through ``run_corpus`` which always
    uses the evaluate-only path (transport rejects G8, execute port raises
    ``EvaluationComplete``).  The gather port calls real G2 via a separate
    ``RecordingBridgeTransport`` capturing the harness's async client.
    Asserts the returned CaseRun is ALLOW and real L3 is unchanged — proving
    ``run_corpus`` never reaches G8.
    """

    async def run():
        async with eval_harness(gateway_db, obligations_db, FIXTURE, monkeypatch) as h:
            before = await _snapshot_l3(h)
            loop = asyncio.get_running_loop()

            # Gather's client: separate transport that shares h.client
            gather_transport = RecordingBridgeTransport(h.client, loop, evaluate_only=True)
            gather_http = httpx.Client(transport=gather_transport, base_url="http://gateway")
            gather_client = GatewayClient("http://gateway", A_TOKEN, http_client=gather_http)

            gather = _make_gather_get_cards(gather_client)
            plan_fn = _make_plan_freeze()

            cases = [
                {
                    "case_id": "g4_freeze",
                    "category": "benign",
                    "variant": "v1",
                    "turns": [{"text": "冻结我的卡"}],
                    "expected": {"outcome": "ALLOW"},
                }
            ]

            def ports_factory(case):
                return dict(
                    understand=_understand,
                    gather=gather,
                    plan=plan_fn,
                    respond=_respond,
                    verify_output=_verify,
                )

            runs = await run_corpus(h, cases, ports_factory=ports_factory, repeat=1)

            assert len(runs) == 1
            r = runs[0]
            assert r.actual_outcome == "ALLOW", f"expected ALLOW, got {r.actual_outcome}"

            # L3 unchanged — proves run_corpus never reached G8
            await _assert_l3_unchanged(h, before, label="run_corpus ALLOW ")
            assert gather_transport.requests, "gather transport recorded no G2 calls"

    asyncio.run(run())
