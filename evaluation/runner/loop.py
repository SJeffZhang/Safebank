"""E0b T4: run_corpus — drive each corpus case through the real L1 + L2 stack.

``run_corpus`` is the harness-level loop: for each case and each repeat it spins
up a new session, registers each turn's utterance and invokes the compiled L1
graph, then maps the recorded /plans/evaluate exchange into a ``CaseRun``.

``StopRun`` is raised (with partial ``.runs``) when a port error indicates the
LLM backend is unavailable (402 / 403 / 429) or the network is down — no
retry, no fallback.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from evaluation.metrics.compute import CaseRun, extract_g4_evidence
from evaluation.runner.harness import GatewayRateLimited
from evaluation.runner.l1 import (
    A_SERVICE_TOKEN,
    EvaluationComplete,
    RecordingBridgeTransport,
    build_evaluate_only_graph,
)
from services.agent.app.gateway.client import GatewayClient
from services.agent.app.graph import AgentContext, UtteranceRef

_STOP_CODES = ("402", "403", "429")

try:
    from httpx import ConnectError as HttpxConnectError
except ImportError:  # pragma: no cover — httpx is always installed
    HttpxConnectError = None  # type: ignore[assignment]


class StopRun(Exception):
    """Raised when the loop must stop immediately (unrecoverable backend error).

    ``.runs`` holds every ``CaseRun`` that was completed before the error.
    """

    def __init__(self, msg: str, runs: list[CaseRun]) -> None:
        super().__init__(msg)
        self.runs = runs


def classify_outcome(
    requests: list[dict[str, Any]],
    last_state: dict[str, Any] | None,
    last_error: Exception | None,
    g3_responses: list[dict[str, Any]] | None = None,
) -> tuple[
    str,  # outcome
    str | None,  # risk
    dict[str, Any],  # params
    list[str],  # finding_codes
    str | None,  # failure_class
    str | None,  # clarify_source
    dict[str, Any] | None,  # clarification
    dict[str, Any] | None,  # understanding
]:
    """Pure function: map recorded transport requests + final graph state + error
    to an (outcome, risk, params, finding_codes, failure_class, clarify_source,
    clarification, understanding) tuple.

    Rules (first match wins):

    1. Last POST /plans/evaluate with 200 + json body → its ``decision``
    2. ``last_error`` is not None → ``ERROR``
    3. ``last_state.clarification`` is non-empty → ``CLARIFY``
    4. Otherwise → ``NO_PLAN``

    *clarify_source* is determined from the transport and G3 responses:

    - ``"L2_G21"``: any GET /clarifications returned clarification data
    - ``"L2_G3"``: any G3 response has ``parse_status == "FORM_REQUIRED"``
    - ``"L1"``: state.clarification exists but no L2 evidence above
    - ``None``: no clarification path was taken

    *understanding* is ``last_state["understanding"]`` serialised (if present).

    *clarification* is ``last_state["clarification"]`` (the raw dict, if any).
    """
    g3s: list[dict[str, Any]] = g3_responses or []
    evaluates = [
        r for r in requests if r.get("method") == "POST" and "/plans/evaluate" in r.get("url", "")
    ]

    # Derive clarify_source from transport and G3 evidence
    clarify_source: str | None = None
    clarification: dict[str, Any] | None = None
    understanding: dict[str, Any] | None = None

    # L2_G21: GET /clarifications with 200 + clarification data
    clar_gets = [
        r for r in requests if r.get("method") == "GET" and "/clarifications" in r.get("url", "")
    ]
    for cg in clar_gets:
        if cg.get("status") == 200 and cg.get("json"):
            body = cg["json"]
            if isinstance(body, dict) and body.get("clarification") is not None:
                clarify_source = "L2_G21"
                break

    # L2_G3: utterance registration had FORM_REQUIRED
    if clarify_source is None:
        for g3 in g3s:
            if g3.get("parse_status") == "FORM_REQUIRED":
                clarify_source = "L2_G3"
                break

    # L1: state.clarification without L2 evidence
    if last_state and last_state.get("clarification"):
        clarification = last_state["clarification"]
        if clarify_source is None:
            clarify_source = "L1"

    # understanding from last_state
    if last_state and last_state.get("understanding") is not None:
        u = last_state["understanding"]
        if hasattr(u, "model_dump"):
            understanding = u.model_dump(mode="json")
        elif isinstance(u, dict):
            understanding = u
        else:
            understanding = {"raw": str(u)}

    if evaluates:
        last_eval = evaluates[-1]
        if last_eval.get("status") == 200 and last_eval.get("json"):
            body = last_eval["json"]
            outcome = body.get("decision", "NO_PLAN")
            risk: str | None = body.get("risk")
            req = last_eval.get("request", {})
            if isinstance(req, dict):
                plan_draft = req.get("plan_draft", req)
                steps = plan_draft.get("steps", []) if isinstance(plan_draft, dict) else []
            else:
                steps = []
            params: dict[str, Any] = steps[0].get("args", {}) if steps else {}
            finding_codes: list[str] = [f.get("code", "") for f in (body.get("findings") or [])]
            return (
                outcome,
                risk,
                params,
                finding_codes,
                None,
                clarify_source,
                clarification,
                understanding,
            )
        else:
            return ("NO_PLAN", None, {}, [], None, clarify_source, clarification, understanding)
    elif last_error is not None:
        return (
            "ERROR",
            None,
            {},
            [],
            type(last_error).__name__,
            clarify_source,
            clarification,
            understanding,
        )
    elif last_state and last_state.get("clarification"):
        return ("CLARIFY", None, {}, [], None, clarify_source, clarification, understanding)
    else:
        return ("NO_PLAN", None, {}, [], None, clarify_source, clarification, understanding)


async def run_corpus(
    h,  # EvalHarness
    cases: list[dict[str, Any]],
    *,
    ports_factory,  # (case: dict) -> dict[str, Any]  (the five LLM port callables)
    repeat: int = 3,
    customer_id: str = "cus_zs",
    wire_gather=None,  # Optional[(GatewayClient, dict, dict) -> dict]: live gather wiring
    max_requests: int | None = None,
    max_requests_counter: list[int] | None = None,  # [0] mutable counter
) -> list[CaseRun]:
    """Run each *case* through the gateway ``repeat`` times and return CaseRuns.

    For each turn in a case the utterance is registered with L2 and the
    compiled L1 graph is invoked once (same sid / thread_id).  Only the
    **last** turn's outcome becomes the CaseRun.

    **Always evaluate-only**: the execute port raises ``EvaluationComplete``
    so the graph stops at G4.  ``run_corpus`` catches this and classifies
    the outcome from the transport's recorded ``/plans/evaluate`` response.
    There is no code path that reaches L3 — the transport also rejects G8
    calls as a belt-and-suspenders guard.

    Raises ``StopRun`` (with partial ``.runs``) when a port raises an
    exception whose text contains ``402`` / ``403`` / ``429``, or when the
    exception is a ``ConnectionError`` / ``httpx.ConnectError``.
    """
    runs: list[CaseRun] = []

    try:
        for case in cases:
            case_id: str = case.get("case_id", case.get("id", ""))
            category: str = case.get("category", "")
            variant: str = case.get("variant", "")
            expected: dict = case.get("expected", {})
            expected_outcome: str = expected.get("outcome", "NO_PLAN")
            expected_risk: str | None = expected.get("risk")
            expected_params: dict[str, Any] = expected.get("params", {})
            is_attack: bool = category.startswith("attack_")
            turns: list = case.get("turns", [])
            session_break: bool = case.get("session_break", False)
            session_boundaries: set[int] = (
                set(case.get("session_boundaries", [])) if session_break else set()
            )

            ports_kwargs = ports_factory(case)

            for ri in range(repeat):
                t0 = time.monotonic()
                sid = await h.new_session(customer_id)

                loop = asyncio.get_running_loop()

                transport = RecordingBridgeTransport(h.client, loop, evaluate_only=True)
                http_client = httpx.Client(transport=transport, base_url="http://gateway")
                client = GatewayClient("http://gateway", A_SERVICE_TOKEN, http_client=http_client)

                # Live mode: re-wire gather with gateway-backed read executor.
                _ports = ports_kwargs
                if wire_gather is not None:
                    _ports = wire_gather(client, ports_kwargs, case)

                graph, _eval_exec = build_evaluate_only_graph(
                    h, loop=loop, client=client, ports_kwargs=_ports
                )

                last_state = None
                last_error: Exception | None = None

                utt_texts: list[str] = []
                g3s: list[dict[str, Any]] = []
                for turn_idx, turn in enumerate(turns):
                    if turn_idx in session_boundaries:
                        sid = await h.new_session(customer_id)

                    text: str = turn["text"] if isinstance(turn, dict) else str(turn)
                    utt_texts.append(text)
                    utt_id, assistant_jwt, g3 = await h.register_utterance(sid, text)
                    g3s.append(g3)

                    def _invoke(
                        _utt_id: str = utt_id,
                        _text: str = text,
                        *,
                        _graph=graph,
                        _g_sid=sid,
                        _g_jwt=assistant_jwt,
                    ):
                        return _graph.invoke(
                            {"utterances": [UtteranceRef(id=_utt_id, kind="typed", text=_text)]},
                            config={"configurable": {"thread_id": _g_sid}},
                            context=AgentContext(
                                assistant_token=_g_jwt,
                                customer_id=customer_id,
                                session_id=_g_sid,
                            ),
                        )

                    try:
                        last_state = await asyncio.to_thread(_invoke)
                        last_error = None
                    except EvaluationComplete:
                        # evaluate-only success: G4 decision already recorded in transport.requests
                        last_state = None
                        last_error = None
                    except Exception as exc:
                        last_error = exc
                        msg = str(exc)
                        if any(code in msg for code in _STOP_CODES):
                            raise StopRun(msg, runs) from exc
                        if isinstance(exc, ConnectionError):
                            raise StopRun(msg, runs) from exc
                        if HttpxConnectError is not None and isinstance(exc, HttpxConnectError):
                            raise StopRun(msg, runs) from exc

                    # --- max_requests guard (live mode) ---
                    if (
                        max_requests_counter is not None
                        and max_requests is not None
                        and max_requests_counter[0] >= max_requests
                    ):
                        raise StopRun(f"超过请求上限 {max_requests}", runs)

                t1 = time.monotonic()
                latency_ms = int((t1 - t0) * 1000)

                (
                    actual_outcome,
                    actual_risk,
                    actual_params,
                    finding_codes,
                    failure_class,
                    clarify_source,
                    clarification,
                    understanding,
                ) = classify_outcome(transport.requests, last_state, last_error, g3s)

                evidence = extract_g4_evidence(transport.requests)

                runs.append(
                    CaseRun(
                        case_id=case_id,
                        category=category,
                        variant=variant,
                        repeat_index=ri,
                        expected_outcome=expected_outcome,
                        expected_risk=expected_risk,
                        expected_params=expected_params,
                        actual_outcome=actual_outcome,
                        actual_risk=actual_risk,
                        actual_params=actual_params,
                        finding_codes=finding_codes,
                        failure_class=failure_class,
                        is_attack=is_attack,
                        latency_ms=latency_ms,
                        tokens=0,
                        utterances=utt_texts,
                        plan_draft=evidence["plan_draft"],
                        g4_decision=evidence["g4_decision"],
                        g4_findings=evidence["g4_findings"],
                        g4_attempts=evidence["g4_attempts"],
                        clarify_source=clarify_source,
                        clarification=clarification,
                        understanding=understanding,
                        l2_g3_response=g3s,
                    )
                )
    except StopRun:
        raise
    except GatewayRateLimited as exc:
        raise StopRun(str(exc), runs) from exc
    except Exception as exc:
        msg = str(exc)
        if any(code in msg for code in _STOP_CODES):
            raise StopRun(msg, runs) from exc
        if isinstance(exc, ConnectionError):
            raise StopRun(msg, runs) from exc
        if HttpxConnectError is not None and isinstance(exc, HttpxConnectError):
            raise StopRun(msg, runs) from exc
        raise

    return runs
