"""C1a graph skeleton behavior tests.

These tests exercise control flow, dependency injection, state/identity isolation and
interrupt/resume semantics with the in-memory checkpointer.  They inject fake ports and
do not call any LLM or banking backend; a stub decision is never treated as authorization.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from safebank_contracts.plan import ExecutionResult, PlanDraft

from services.agent.app.graph import (
    AgentContext,
    CrossCustomerError,
    Decision,
    DecisionValue,
    EmptyUtterancesError,
    Finding,
    MissingPortError,
    PortFailure,
    Ports,
    ReadRecord,
    SessionMismatchError,
    Understanding,
    UtteranceRef,
    build_graph,
    invoke_new_input,
)

SECRET = "TEST_ONLY_ASSISTANT_TOKEN_SECRET"


def ctx(customer="cus_a", session="ses_a", token=SECRET) -> AgentContext:
    return AgentContext(assistant_token=token, customer_id=customer, session_id=session)


def config(session="ses_a") -> dict:
    return {"configurable": {"thread_id": session}}


def utterance(text="给王五转 800", uid="utt_a") -> UtteranceRef:
    return UtteranceRef(id=uid, kind="typed", text=text)


def draft(session="ses_a", request="req_a") -> PlanDraft:
    return PlanDraft(
        request_id=request,
        session_id=session,
        utterance_ids=["utt_a"],
        intents=["transfer"],
        steps=[
            {
                "step_id": "s1",
                "tool": "transfer",
                "args": {
                    "from_account_id": "acc_a",
                    "payee_id": "pye_b",
                    "to_account_id": None,
                    "amount_cents": 100,
                    "memo": None,
                },
                "depends_on": [],
                "arg_sources": [],
            }
        ],
        rationale="test",
    )


def decision(value="ALLOW", plan_id="pln_a", approval_id=None, findings=()) -> Decision:
    return Decision(plan_id=plan_id, decision=value, approval_id=approval_id, findings=findings)


def execution(plan_id="pln_a") -> ExecutionResult:
    return ExecutionResult(
        plan_id=plan_id,
        status="EXECUTED",
        steps=(),
        versions={},
        committed_at=None,
        error_code=None,
        replayed=False,
    )


def default_ports(calls=None, **overrides):
    def bump(name):
        if calls is not None:
            calls[name] = calls.get(name, 0) + 1

    def understand(state, runtime_ctx):
        bump("understand")
        return Understanding(intents=["transfer"], needs_write=True)

    def gather(state, runtime_ctx):
        bump("gather")
        return [ReadRecord(source_ref="rd_a")]

    def plan(state, runtime_ctx):
        bump("plan")
        return draft()

    def evaluate(state, runtime_ctx):
        bump("evaluate")
        return decision("ALLOW", "pln_a")

    def execute(state, runtime_ctx):
        bump("execute")
        return execution(state.get("plan_id") or "pln_a")

    def respond(state, runtime_ctx):
        bump("respond")
        return "已执行"

    def verify_output(state, runtime_ctx):
        bump("verify_output")
        return state["reply_draft"]

    values = {
        "understand": understand,
        "gather": gather,
        "plan": plan,
        "evaluate": evaluate,
        "execute": execute,
        "respond": respond,
        "verify_output": verify_output,
        **overrides,
    }
    return Ports(**values)


def run(graph, input_utterances, customer="cus_a", session="ses_a", token=SECRET):
    return invoke_new_input(
        graph, input_utterances, config=config(session), context=ctx(customer, session, token)
    )


def test_allow_path_runs_end_to_end_and_accumulates_messages():
    calls = {}
    graph = build_graph(default_ports(calls), InMemorySaver())
    result = run(graph, [utterance()])
    assert result["decision"].decision is DecisionValue.ALLOW
    assert result["execution"].status == "EXECUTED"
    assert result["reply_draft"] == "已执行"
    assert [m.type for m in result["messages"]] == ["human", "ai"]
    assert calls == {
        "understand": 1,
        "gather": 1,
        "plan": 1,
        "evaluate": 1,
        "execute": 1,
        "respond": 1,
        "verify_output": 1,
    }


def test_second_message_resets_round_fields():
    plan_ids = ["pln_a", "pln_b"]
    reads = ["rd_a", "rd_b"]

    def evaluate(state, runtime_ctx):
        return decision("ALLOW", plan_ids.pop(0))

    def gather(state, runtime_ctx):
        return [ReadRecord(source_ref=reads.pop(0))]

    calls = {}
    graph = build_graph(default_ports(calls, evaluate=evaluate, gather=gather), InMemorySaver())

    first = run(graph, [utterance("第一句")])
    assert first["plan_id"] == "pln_a"
    assert first["execution"].plan_id == "pln_a"
    assert [r.source_ref for r in first["reads"]] == ["rd_a"]

    second = run(graph, [utterance("第二句", uid="utt_b")])
    assert second["plan_id"] == "pln_b"
    assert second["execution"].plan_id == "pln_b"
    assert [r.source_ref for r in second["reads"]] == ["rd_b"]
    assert second["replan_count"] == 0
    assert second["approval_id"] is None
    assert second["reply_draft"] == "已执行"
    # messages accumulate across rounds; round fields were replaced.
    assert [m.type for m in second["messages"]] == ["human", "ai", "human", "ai"]


def test_approval_interrupt_and_correct_resume():
    calls = {}
    ports = default_ports(
        calls, evaluate=lambda s, c: decision("REQUIRE_APPROVAL", "pln_a", "apv_a")
    )
    graph = build_graph(ports, InMemorySaver())

    first = run(graph, [utterance()])
    assert first["__interrupt__"][0].value["approval_id"] == "apv_a"
    assert calls.get("execute", 0) == 0

    result = graph.invoke(Command(resume="apv_a"), config(), context=ctx())
    assert result["approval_id"] == "apv_a"
    assert result["execution"].status == "EXECUTED"
    assert calls["execute"] == 1


def test_wrong_resume_does_not_execute():
    calls = {}
    ports = default_ports(
        calls, evaluate=lambda s, c: decision("REQUIRE_APPROVAL", "pln_a", "apv_a")
    )
    graph = build_graph(ports, InMemorySaver())

    run(graph, [utterance()])
    result = graph.invoke(Command(resume="apv_wrong"), config(), context=ctx())
    assert result.get("execution") is None
    assert calls.get("execute", 0) == 0
    assert calls["respond"] == 1


def test_new_input_replaces_pending_interrupt_and_old_resume_cannot_execute():
    calls = {}
    approvals = iter(["apv_a", "apv_b"])

    def evaluate(state, runtime_ctx):
        approval = next(approvals)
        return decision("REQUIRE_APPROVAL", f"pln_{approval}", approval)

    graph = build_graph(default_ports(calls, evaluate=evaluate), InMemorySaver())

    first = run(graph, [utterance("给王五转 800", "utt_a")])
    assert first["__interrupt__"][0].value["approval_id"] == "apv_a"

    second = run(graph, [utterance("给赵六转 900", "utt_b")])
    assert second["__interrupt__"][0].value["approval_id"] == "apv_b"

    resumed = graph.invoke(Command(resume="apv_a"), config(), context=ctx())
    assert resumed.get("execution") is None
    assert calls.get("execute", 0) == 0


def test_threads_isolated_between_customers():
    calls = {}
    ports = default_ports(calls, evaluate=lambda s, c: decision("ALLOW", f"pln_{c.customer_id}"))
    graph = build_graph(ports, InMemorySaver())

    first = run(graph, [utterance("第一句")], customer="cus_a", session="ses_a")
    second = run(graph, [utterance("第二句", "utt_b")], customer="cus_b", session="ses_b")

    assert first["plan_id"] == "pln_cus_a"
    assert second["plan_id"] == "pln_cus_b"
    assert first["customer_id"] == "cus_a"
    assert second["customer_id"] == "cus_b"


def test_same_thread_different_customer_rejected():
    graph = build_graph(default_ports(), InMemorySaver())
    run(graph, [utterance()], customer="cus_a", session="ses_a")
    with pytest.raises(CrossCustomerError):
        run(graph, [utterance("第二句", "utt_b")], customer="cus_b", session="ses_a")


def test_thread_id_must_match_session_id():
    graph = build_graph(default_ports(), InMemorySaver())
    with pytest.raises(SessionMismatchError):
        graph.invoke(
            {"utterances": [utterance()]},
            config(session="ses_other"),
            context=ctx(customer="cus_a", session="ses_a"),
        )


def test_ports_called_once_before_interrupt():
    calls = {}

    def evaluate(state, runtime_ctx):
        calls["evaluate"] = calls.get("evaluate", 0) + 1
        return decision("REQUIRE_APPROVAL", "pln_a", "apv_a")

    graph = build_graph(default_ports(calls, evaluate=evaluate), InMemorySaver())

    run(graph, [utterance()])
    assert calls["understand"] == 1
    assert calls["gather"] == 1
    assert calls["plan"] == 1
    assert calls["evaluate"] == 1
    assert calls.get("execute", 0) == 0

    graph.invoke(Command(resume="apv_a"), config(), context=ctx())
    # resume re-enters await_approval only; earlier ports are not re-invoked.
    assert calls["understand"] == 1
    assert calls["gather"] == 1
    assert calls["plan"] == 1
    assert calls["evaluate"] == 1
    assert calls["execute"] == 1


def test_runtime_secret_not_in_checkpoint_or_output():
    graph = build_graph(default_ports(), InMemorySaver())
    run(graph, [utterance()], token=SECRET)
    saver = graph.checkpointer
    for checkpoint in saver.list(config()):
        assert SECRET not in repr(checkpoint)
    assert SECRET.encode() not in repr(saver.storage).encode()


def test_missing_port_fails_closed():
    ports = default_ports()
    with pytest.raises(MissingPortError):
        build_graph(replace(ports, execute=None), InMemorySaver())
    with pytest.raises(MissingPortError):
        build_graph(ports, None)


def test_port_returning_none_fails_closed_without_execute_side_effect():
    calls = {}
    ports = default_ports(calls, evaluate=lambda s, c: None)
    graph = build_graph(ports, InMemorySaver())
    with pytest.raises(PortFailure):
        run(graph, [utterance()])
    assert calls.get("execute", 0) == 0


def test_replan_agent_reroutes_and_bounds():
    calls = {}
    evals = {"n": 0}

    def evaluate(state, runtime_ctx):
        evals["n"] += 1
        if evals["n"] < 3:
            findings = (Finding(code="ENTITY_UNSEEN", resolver="agent"),)
            return decision("REPLAN", f"pln_{evals['n']}", findings=findings)
        return decision("ALLOW", "pln_done")

    graph = build_graph(default_ports(calls, evaluate=evaluate), InMemorySaver())
    result = run(graph, [utterance()])
    assert evals["n"] == 3
    assert result["replan_count"] == 2
    assert result["decision"].decision is DecisionValue.ALLOW
    assert result["execution"].plan_id == "pln_done"


def test_replan_user_finding_goes_to_respond():
    calls = {}
    ports = default_ports(
        calls,
        evaluate=lambda s, c: decision(
            "REPLAN", "pln_a", findings=(Finding(code="UNKNOWN_SENSITIVE_ARG", resolver="user"),)
        ),
    )
    graph = build_graph(ports, InMemorySaver())
    result = run(graph, [utterance()])
    assert result["decision"].decision is DecisionValue.REPLAN
    assert result.get("execution") is None
    assert calls.get("execute", 0) == 0


def test_deny_routes_to_respond_without_execute():
    calls = {}
    ports = default_ports(calls, evaluate=lambda s, c: decision("DENY", "pln_a"))
    graph = build_graph(ports, InMemorySaver())
    result = run(graph, [utterance()])
    assert result["decision"].decision is DecisionValue.DENY
    assert result.get("execution") is None
    assert calls.get("execute", 0) == 0


def _approval_graph(calls):
    ports = default_ports(
        calls, evaluate=lambda s, c: decision("REQUIRE_APPROVAL", "pln_a", "apv_a")
    )
    return build_graph(ports, InMemorySaver())


def test_resume_wrong_customer_rejected_without_execute():
    calls = {}
    graph = _approval_graph(calls)
    run(graph, [utterance()])
    with pytest.raises(CrossCustomerError):
        graph.invoke(Command(resume="apv_a"), config(), context=ctx("cus_b", "ses_a"))
    assert calls.get("execute", 0) == 0
    assert calls.get("respond", 0) == 0
    assert calls.get("verify_output", 0) == 0


def test_resume_wrong_session_rejected_without_execute():
    calls = {}
    graph = _approval_graph(calls)
    run(graph, [utterance()])
    with pytest.raises(SessionMismatchError):
        graph.invoke(Command(resume="apv_a"), config(), context=ctx("cus_a", "ses_wrong"))
    assert calls.get("execute", 0) == 0
    assert calls.get("respond", 0) == 0
    assert calls.get("verify_output", 0) == 0


def test_thread_id_session_mismatch_rejected_via_ingest_guard():
    # thread_id "ses_other" has no checkpoint, so this starts a fresh run and is rejected by
    # ingest's thread_id-vs-session guard — it does NOT exercise await_approval's resume binding.
    calls = {}
    graph = _approval_graph(calls)
    run(graph, [utterance()])
    with pytest.raises(SessionMismatchError):
        graph.invoke(
            Command(resume="apv_a"), config(session="ses_other"), context=ctx("cus_a", "ses_a")
        )
    assert calls.get("execute", 0) == 0
    assert calls.get("respond", 0) == 0
    assert calls.get("verify_output", 0) == 0


def test_correct_resume_after_bad_resume_is_safe():
    calls = {}
    graph = _approval_graph(calls)
    run(graph, [utterance()])
    with pytest.raises(CrossCustomerError):
        graph.invoke(Command(resume="apv_a"), config(), context=ctx("cus_b", "ses_a"))
    with pytest.raises(SessionMismatchError):
        graph.invoke(Command(resume="apv_a"), config(), context=ctx("cus_a", "ses_wrong"))
    result = graph.invoke(Command(resume="apv_a"), config(), context=ctx("cus_a", "ses_a"))
    assert result["approval_id"] == "apv_a"
    assert result["execution"].status == "EXECUTED"
    assert calls["execute"] == 1


def test_empty_utterances_new_input_rejected_without_ports():
    calls = {}
    graph = build_graph(default_ports(calls), InMemorySaver())
    with pytest.raises(EmptyUtterancesError):
        run(graph, [])
    assert calls == {}


def test_empty_utterances_does_not_reuse_completed_round():
    calls = {}
    graph = build_graph(default_ports(calls), InMemorySaver())
    run(graph, [utterance("第一句")])
    assert calls["execute"] == 1
    with pytest.raises(EmptyUtterancesError):
        run(graph, [])
    assert calls["understand"] == 1
    assert calls["execute"] == 1


def test_direct_invoke_with_empty_utterances_rejected():
    calls = {}
    graph = build_graph(default_ports(calls), InMemorySaver())
    with pytest.raises(EmptyUtterancesError):
        graph.invoke({"utterances": []}, config(), context=ctx())
    assert calls == {}
