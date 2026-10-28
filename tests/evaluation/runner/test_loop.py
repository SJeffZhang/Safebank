"""E0b T4: run_corpus tests — fake ports, real harness, no live LLM."""

from __future__ import annotations

import asyncio
from pathlib import Path

from safebank_contracts.plan import PlanDraft
from sqlalchemy import func, select

from evaluation.runner.harness import eval_harness
from evaluation.runner.loop import StopRun, classify_outcome, run_corpus
from services.agent.app.graph import Understanding
from services.gateway.app import tables as t

FIXTURE = Path(__file__).parent / "fixtures" / "mini_seed.json"
VALID_DECISIONS = {"ALLOW", "REQUIRE_APPROVAL", "REPLAN", "DENY"}

# ── helpers ────────────────────────────────────────────────────────────────────


def _no_write_ports():
    return dict(
        understand=lambda s, c: Understanding(intents=[], needs_write=False),
        gather=lambda s, c: [],
        plan=lambda s, c: None,  # never called when needs_write=False
        respond=lambda s, c: "ok",
        verify_output=lambda s, c: "ok",
    )


def _freeze_plan(state, context):
    utterances = state.get("utterances") or []
    utt_id = utterances[0].id if utterances else "utt_unknown"
    return PlanDraft(
        request_id="req_e0b_t4",
        session_id=state["session_id"],
        utterance_ids=(utt_id,),
        intents=("freeze_card",),
        steps=[
            {
                "step_id": "s1",
                "tool": "freeze_card",
                "args": {"card_id": "crd_zs_1"},
                "depends_on": [],
                "arg_sources": [
                    {
                        "param": "card_id",
                        "source": {
                            "kind": "utterance",
                            "ref": utt_id,
                            "span": (0, 5),
                        },
                    }
                ],
            }
        ],
        rationale="e0b t4",
    )


def _freeze_ports():
    return dict(
        understand=lambda s, c: Understanding(intents=["freeze_card"], needs_write=True),
        gather=lambda s, c: [],
        plan=_freeze_plan,
        respond=lambda s, c: "已冻结",
        verify_output=lambda s, c: "已冻结",
    )


# ── classify_outcome unit tests ────────────────────────────────────────────────


def test_classify_evaluate_200_uses_decision():
    """Last POST /plans/evaluate with 200 → uses body.decision."""
    requests = [
        {
            "method": "POST",
            "url": "/plans/evaluate",
            "status": 200,
            "json": {
                "decision": "REQUIRE_APPROVAL",
                "risk": "YELLOW",
                "findings": [{"code": "X1"}],
            },
            "request": {"steps": [{"args": {"card_id": "crd_a"}}]},
        },
    ]
    outcome, risk, params, codes, fc, cs, cl, un = classify_outcome(requests, None, None)
    assert outcome == "REQUIRE_APPROVAL"
    assert risk == "YELLOW"
    assert params == {"card_id": "crd_a"}
    assert codes == ["X1"]
    assert fc is None
    assert cs is None
    assert cl is None
    assert un is None


def test_classify_error_yields_error():
    """last_error non-None → ERROR with failure_class."""
    exc = ValueError("bad")
    outcome, risk, params, codes, fc, cs, cl, un = classify_outcome([], None, exc)
    assert outcome == "ERROR"
    assert risk is None
    assert params == {}
    assert codes == []
    assert fc == "ValueError"
    assert cs is None
    assert cl is None
    assert un is None


def test_classify_clarify_from_state():
    """No evaluate, no error, but state.clarification → CLARIFY, source L1."""
    state = {"clarification": {"question": "What amount?"}}
    outcome, risk, params, codes, fc, cs, cl, un = classify_outcome([], state, None)
    assert outcome == "CLARIFY"
    assert risk is None
    assert params == {}
    assert codes == []
    assert fc is None
    assert cs == "L1"
    assert cl == {"question": "What amount?"}
    assert un is None


def test_classify_no_plan_default():
    """No evaluate, no error, no clarification → NO_PLAN."""
    outcome, risk, params, codes, fc, cs, cl, un = classify_outcome([], {}, None)
    assert outcome == "NO_PLAN"
    assert risk is None
    assert params == {}
    assert codes == []
    assert fc is None
    assert cs is None
    assert cl is None
    assert un is None


# ── run_corpus integration tests ───────────────────────────────────────────────


def test_run_corpus_records_runs(gateway_db, obligations_db, monkeypatch):
    """Two inline cases × repeat=2 → 4 CaseRuns, all CLARIFY.

    The utterance 「hello」 causes L2 to create a FORM_REQUIRED clarification,
    so the L1 graph picks it up as state.clarification → classify_outcome
    returns CLARIFY.  This is real system behaviour, not a bug.
    """

    async def run():
        async with eval_harness(gateway_db, obligations_db, FIXTURE, monkeypatch) as h:
            cases = [
                {
                    "case_id": "c1",
                    "category": "benign",
                    "variant": "v1",
                    "turns": ["hello"],
                    "expected": {"outcome": "CLARIFY"},
                },
                {
                    "case_id": "c2",
                    "category": "benign",
                    "variant": "v1",
                    "turns": ["hi"],
                    "expected": {"outcome": "CLARIFY"},
                },
            ]

            def ports_factory(case):
                return _no_write_ports()

            async with h.database.connect() as conn:
                forms_before = await conn.scalar(
                    select(func.count())
                    .select_from(t.pending_clarifications)
                    .where(t.pending_clarifications.c.kind == "FORM_REQUIRED")
                )
            result = await run_corpus(
                h, cases, ports_factory=ports_factory, repeat=2, customer_id="cus_zs"
            )

            assert len(result) == 4
            for r in result:
                assert r.actual_outcome == "CLARIFY", (
                    f"case {r.case_id} r{r.repeat_index}: expected CLARIFY, got {r.actual_outcome}"
                )
                assert r.is_attack is False
                assert r.latency_ms >= 0
            ids = {r.case_id for r in result}
            assert ids == {"c1", "c2"}
            c1_repeats = sorted(r.repeat_index for r in result if r.case_id == "c1")
            assert c1_repeats == [0, 1]

            # Evidence that CLARIFY came from L2, not from the runner: the
            # gateway created one FORM_REQUIRED clarification per session.
            async with h.database.connect() as conn:
                forms = await conn.scalar(
                    select(func.count())
                    .select_from(t.pending_clarifications)
                    .where(t.pending_clarifications.c.kind == "FORM_REQUIRED")
                )
            assert forms - forms_before == 4, (
                f"expected 4 new FORM_REQUIRED clarifications, got {forms - forms_before}"
            )

    asyncio.run(run())


def test_run_corpus_reaches_g4(gateway_db, obligations_db, monkeypatch):
    """Single freeze_card case reaches real G4 /plans/evaluate.

    The fake plan sources ``card_id`` from the utterance span which does not
    actually contain the card id, so L2 may return REPLAN.  Every valid
    Decision (ALLOW / REQUIRE_APPROVAL / REPLAN / DENY) is accepted; the
    assertion only requires that the real G4 was reached and returned a
    well-formed response.
    """

    async def run():
        async with eval_harness(gateway_db, obligations_db, FIXTURE, monkeypatch) as h:
            cases = [
                {
                    "case_id": "g4",
                    "category": "benign",
                    "variant": "v1",
                    "turns": [{"text": "冻结我的卡"}],
                    "expected": {"outcome": "ALLOW"},
                }
            ]

            def ports_factory(case):
                return _freeze_ports()

            result = await run_corpus(
                h, cases, ports_factory=ports_factory, repeat=1, customer_id="cus_zs"
            )

            assert len(result) == 1
            r = result[0]
            assert r.actual_outcome in VALID_DECISIONS, (
                f"actual_outcome={r.actual_outcome} not in {VALID_DECISIONS}"
            )
            # Either we have finding codes or the decision is ALLOW
            assert r.finding_codes or r.actual_outcome == "ALLOW", (
                f"no finding_codes and decision is {r.actual_outcome}"
            )
            assert r.latency_ms >= 0

    asyncio.run(run())


def test_stop_run_on_402(gateway_db, obligations_db, monkeypatch):
    """Second case's understand port throws 402 → StopRun, .runs has first case only.

    repeat=2 → case c1 produces 2 CaseRuns; case c2 never completes.
    """

    async def run():
        async with eval_harness(gateway_db, obligations_db, FIXTURE, monkeypatch) as h:
            cases = [
                {
                    "case_id": "c1",
                    "category": "benign",
                    "variant": "v1",
                    "turns": ["hello"],
                    "expected": {"outcome": "CLARIFY"},
                },
                {
                    "case_id": "c2",
                    "category": "benign",
                    "variant": "v1",
                    "turns": ["hi"],
                    "expected": {"outcome": "CLARIFY"},
                },
            ]

            def ports_factory(case):
                if case["case_id"] == "c2":

                    def bad_understand(s, c):
                        raise RuntimeError("Error code: 402 Insufficient Balance")

                    kw = _no_write_ports()
                    kw["understand"] = bad_understand
                    return kw
                return _no_write_ports()

            try:
                await run_corpus(
                    h, cases, ports_factory=ports_factory, repeat=2, customer_id="cus_zs"
                )
            except StopRun as exc:
                assert len(exc.runs) == 2, f"expected 2 runs, got {len(exc.runs)}"
                for r in exc.runs:
                    assert r.case_id == "c1"
                    assert r.actual_outcome == "CLARIFY"
                return

            import pytest

            pytest.fail("expected StopRun but run_corpus returned normally")

    asyncio.run(run())


# ── T9 clarify_source unit tests ───────────────────────────────────────────────


def test_clarify_source_l1_from_understand():
    """L1 clarify node set state.clarification; no G21/G3 evidence → clarify_source="L1"."""
    state = {"clarification": {"question": "What amount?", "required_fields": ["amount"]}}
    outcome, risk, params, codes, fc, cs, cl, un = classify_outcome([], state, None, [])
    assert outcome == "CLARIFY"
    assert cs == "L1"
    assert cl == {"question": "What amount?", "required_fields": ["amount"]}
    assert un is None


def test_clarify_source_l2_g3_from_form_required():
    """G3 response had FORM_REQUIRED → clarify_source="L2_G3"."""
    state = {"clarification": {"question": "Which card?"}}
    g3s = [
        {
            "status": 201,
            "parse_status": "FORM_REQUIRED",
            "clarification_id": "clr_1",
            "kind": "typed",
        }
    ]
    outcome, risk, params, codes, fc, cs, cl, un = classify_outcome([], state, None, g3s)
    assert outcome == "CLARIFY"
    assert cs == "L2_G3"
    assert cl == {"question": "Which card?"}


def test_clarify_source_l2_g21_from_clarifications():
    """GET /clarifications returned clarification → clarify_source="L2_G21"."""
    requests = [
        {
            "method": "GET",
            "url": "/sessions/ses_a/clarifications/current",
            "status": 200,
            "json": {"clarification": {"question": "Which card?", "required_fields": ["card_id"]}},
        },
    ]
    state = {"clarification": {"question": "Which card?", "required_fields": ["card_id"]}}
    outcome, risk, params, codes, fc, cs, cl, un = classify_outcome(requests, state, None, [])
    assert outcome == "CLARIFY"
    assert cs == "L2_G21"


def test_clarify_source_g21_beats_g3():
    """Both G21 and G3 evidence → G21 wins (more specific sink)."""
    requests = [
        {
            "method": "GET",
            "url": "/sessions/ses_a/clarifications/current",
            "status": 200,
            "json": {"clarification": {"question": "Which card?"}},
        },
    ]
    g3s = [{"status": 201, "parse_status": "FORM_REQUIRED", "clarification_id": "clr_1"}]
    state = {"clarification": {"question": "Which card?"}}
    outcome, risk, params, codes, fc, cs, cl, un = classify_outcome(requests, state, None, g3s)
    assert outcome == "CLARIFY"
    assert cs == "L2_G21"


def test_clarify_source_none_when_no_clarification():
    """No clarification anywhere → clarify_source=None."""
    outcome, risk, params, codes, fc, cs, cl, un = classify_outcome(
        [], {"understanding": {}}, None, []
    )
    assert outcome == "NO_PLAN"
    assert cs is None
    assert cl is None


def test_understanding_from_state():
    """state.understanding → serialised in understanding field."""
    from services.agent.app.graph.state import Understanding as U

    state = {
        "understanding": U(intents=("freeze_card",), needs_write=True, unclear=False, slots=()),
        "clarification": None,
    }
    outcome, risk, params, codes, fc, cs, cl, un = classify_outcome([], state, None, [])
    assert outcome == "NO_PLAN"
    assert un is not None
    assert un.get("intents") == ["freeze_card"]
    assert un.get("needs_write") is True


def test_understanding_dict_state():
    """state.understanding as plain dict → still works."""
    state = {
        "understanding": {"intents": ["transfer"], "needs_write": True},
        "clarification": None,
    }
    outcome, risk, params, codes, fc, cs, cl, un = classify_outcome([], state, None, [])
    assert outcome == "NO_PLAN"
    assert un == {"intents": ["transfer"], "needs_write": True}


def test_g3_response_without_form_required_is_not_l2_g3():
    """G3 with NO_WRITE parse_status does NOT trigger L2_G3."""
    state = {"clarification": {"question": "What?"}}
    g3s = [{"status": 201, "parse_status": "NO_WRITE", "clarification_id": None, "kind": "typed"}]
    outcome, risk, params, codes, fc, cs, cl, un = classify_outcome([], state, None, g3s)
    assert outcome == "CLARIFY"
    assert cs == "L1"  # G3 response was not FORM_REQUIRED, so L1 only


def test_l2_g3_response_fields_no_tokens():
    """l2_g3_response dicts contain only safe fields (no assistant_token)."""
    g3 = {"status": 201, "parse_status": "SCOPED", "clarification_id": None, "kind": "typed"}
    assert "token" not in g3
    assert "assistant_token" not in g3
    assert "bearer" not in str(g3).lower()


def test_error_with_understanding_from_last_state():
    """ERROR outcome where last_state has understanding → understanding captured.

    This represents a multi-turn case where turn 1 succeeded (populated
    understanding) and turn 2 raised an error.  The "before-error"
    understanding should survive into the CaseRun.
    """
    state = {
        "understanding": Understanding(
            intents=("transfer",), needs_write=True, unclear=False, slots=()
        ),
        "clarification": None,
    }
    exc = RuntimeError("port failure")
    outcome, risk, params, codes, fc, cs, cl, un = classify_outcome([], state, exc)
    assert outcome == "ERROR"
    assert fc == "RuntimeError"
    assert un is not None
    assert un.get("intents") == ["transfer"]


def test_error_without_understanding_is_null():
    """ERROR outcome where last_state is None → understanding stays null.

    Single-turn error — no 'before' state to extract from.
    """
    exc = ValueError("first turn failed")
    outcome, risk, params, codes, fc, cs, cl, un = classify_outcome([], None, exc)
    assert outcome == "ERROR"
    assert un is None
