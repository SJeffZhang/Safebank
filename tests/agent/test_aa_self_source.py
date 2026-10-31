"""AA inclusion needs declared typed text evidence before leaving the planner."""

from copy import deepcopy

import pytest

from services.agent.app.graph.state import UtteranceRef
from services.agent.app.llm.plan import PlanAdapter, ReferenceError
from tests.agent.llm_mock import ScriptedLLM, tool_response
from tests.agent.test_llm_adapters import body_of, build
from tests.agent.test_plan_e2e_fixes import _good_split, _split_state, ctx


def proposal(source="valid"):
    body = _good_split()
    declarations = [
        {
            "param": "account_id",
            "source": {"kind": "read", "ref": "src_acct", "pointer": "/accounts/0/account_id"},
        },
        {
            "param": "total_cents",
            "source": {"kind": "utterance", "ref": "utt_a", "span": [4, 8]},
        },
        {
            "param": "participant_payee_ids",
            "source": {"kind": "utterance", "ref": "utt_a", "span": [11, 19]},
        },
    ]
    evidence = {
        "valid": {"kind": "utterance", "ref": "utt_a", "span": [9, 11]},
        "read": {"kind": "read", "ref": "src_acct", "pointer": "/include_self"},
        "unknown_ref": {"kind": "utterance", "ref": "utt_invented", "span": [9, 11]},
        "out_of_bounds": {"kind": "utterance", "ref": "utt_a", "span": [9, 99]},
        "context": {"kind": "utterance", "ref": "utt_ctx", "span": [0, 2]},
    }.get(source)
    if evidence is not None:
        declarations.append({"param": "include_self", "source": evidence})
    body["steps"][0]["arg_sources"] = declarations
    return body


def state():
    current = _split_state()
    # A matching bool in read data still cannot supply the user's AA inclusion choice.
    current["reads"][0] = current["reads"][0].model_copy(
        update={"data": {"accounts": [{"account_id": "acc_zs_chk"}], "include_self": True}}
    )
    current["utterances"].append(UtteranceRef(id="utt_ctx", kind="context", text="我和"))
    return current


@pytest.mark.parametrize("bad", ["missing", "read", "unknown_ref", "out_of_bounds", "context"])
def test_aa_self_source_retries_without_rewriting_financial_choices(bad):
    good = proposal()
    original = deepcopy(good["steps"][0]["args"])
    scripted = ScriptedLLM(
        [
            tool_response("submit_action_plan", proposal(bad)),
            tool_response("submit_action_plan", good),
        ]
    )
    draft = PlanAdapter(build(scripted))(state(), ctx())
    assert len(scripted.requests) == 2
    assert "include_self" in body_of(scripted, 1)["messages"][1]["content"]
    actual = draft.steps[0]
    assert actual.args.total_cents == original["total_cents"] == 40000
    assert actual.args.participant_payee_ids == ("pye_zs_f1", "pye_zs_f2", "pye_zs_f3")
    assert actual.args.include_self is True
    assert actual.args.memo is None
    declared = next(s.source for s in actual.arg_sources if s.param == "include_self")
    assert declared.model_dump(mode="json") == {
        "kind": "utterance",
        "ref": "utt_a",
        "span": [9, 11],
    }


@pytest.mark.parametrize("bad", ["missing", "read", "context"])
def test_aa_self_source_exhaustion_never_returns_incomplete_plan(bad):
    scripted = ScriptedLLM([tool_response("submit_action_plan", proposal(bad))] * 2)
    with pytest.raises(ReferenceError) as caught:
        PlanAdapter(build(scripted))(state(), ctx())
    assert len(scripted.requests) == 2
    assert any("include_self" in p for p in caught.value.problems)


def test_valid_aa_self_source_is_preserved_without_regeneration():
    scripted = ScriptedLLM([tool_response("submit_action_plan", proposal())])
    draft = PlanAdapter(build(scripted))(state(), ctx())
    assert len(scripted.requests) == 1
    assert draft.steps[0].args.total_cents == 40000
    assert draft.steps[0].args.memo is None


@pytest.mark.parametrize("payees", [None, []])
def test_missing_or_empty_payee_read_cannot_release_guessed_participants(payees):
    current = state()
    current["reads"] = [r for r in current["reads"] if r.tool != "list_payees"]
    if payees is not None:
        from services.agent.app.graph.state import ReadRecord

        current["reads"].append(
            ReadRecord(source_ref="src_empty_payees", tool="list_payees", data={"items": payees})
        )
    bad = proposal()
    bad["steps"][0]["args"]["participant_payee_ids"] = [
        "pye_xiaolin",
        "pye_xiaozhou",
        "pye_xiaochen",
    ]
    scripted = ScriptedLLM([tool_response("submit_action_plan", bad)] * 2)
    with pytest.raises(ReferenceError) as caught:
        PlanAdapter(build(scripted))(current, ctx())
    assert len(scripted.requests) == 2
    assert any("participant_payee_ids" in p for p in caught.value.problems)


@pytest.mark.parametrize(
    "done,failed,expected",
    [
        ((), (), ["get_accounts", "list_payees"]),
        (("get_accounts",), (), ["get_accounts", "list_payees"]),
        (("list_payees",), (), ["list_payees", "get_accounts"]),
        (("get_accounts", "list_payees"), (), ["get_accounts", "list_payees"]),
        (("list_payees",), ("list_payees",), ["list_payees", "get_accounts"]),
        (("get_accounts",), ("get_accounts",), ["get_accounts", "list_payees"]),
    ],
)
def test_aa_gather_reads_actual_prerequisites_once(done, failed, expected):
    from safebank_contracts.codes import Code
    from safebank_contracts.tools import READ_ARGS

    from services.agent.app.gateway.errors import GatewayResponseError
    from services.agent.app.llm.gather import GatherAdapter, ReadResult
    from tests.agent.llm_mock import multi_tool_response, text_response

    calls = []
    payloads = {
        "get_accounts": {"accounts": [{"id": "acc_zs_chk", "type": "CHECKING"}]},
        "list_payees": {
            "items": [
                {"id": "pye_zs_f1", "name": "小林"},
                {"id": "pye_zs_f2", "name": "小周"},
                {"id": "pye_zs_f3", "name": "小陈"},
            ]
        },
    }

    def executor(name, args):
        READ_ARGS[name].model_validate(args)
        calls.append(name)
        if name in failed:
            raise GatewayResponseError(
                status=404, code=Code.NOT_FOUND, details={}, trace_id="", decision=None
            )
        return ReadResult(
            data=payloads[name],
            source_ref="src_actual_" + name,
            untrusted_paths=("/items/0/name",) if name == "list_payees" else (),
            versions={"cus_zs": 7},
            observed_at="2026-10-15T09:00:00+08:00",
        )

    responses = []
    if done:
        responses.append(
            multi_tool_response(
                [
                    (name, {"history_days": 0} if name == "get_accounts" else {"query": None})
                    for name in done
                ]
            )
        )
    responses.append(text_response("collected"))
    scripted = ScriptedLLM(responses)
    current = _split_state()
    current["reads"] = []
    # The actual live label was AA记账; the text must also work for other free-form labels.
    current["understanding"] = current["understanding"].model_copy(update={"intents": ("AA记账",)})
    reads = GatherAdapter(build(scripted), executor, supplement_transfer_reads=True)(current, ctx())
    assert calls == expected
    assert len(calls) == len(set(calls))
    assert {r.tool for r in reads} == set(expected) - set(failed)
    for read in reads:
        assert read.source_ref == "src_actual_" + read.tool
        assert read.versions == {"cus_zs": 7}
        assert read.observed_at == "2026-10-15T09:00:00+08:00"
    payee_read = next((r for r in reads if r.tool == "list_payees"), None)
    if payee_read is not None:
        assert [p["id"] for p in payee_read.data["items"]] == [
            "pye_zs_f1",
            "pye_zs_f2",
            "pye_zs_f3",
        ]
        assert payee_read.untrusted_paths == ("/items/0/name",)
        assert "untrusted_data" in payee_read.data["items"][0]["name"]


def test_aa_status_read_does_not_add_write_prerequisites():
    from services.agent.app.llm.gather import GatherAdapter
    from tests.agent.llm_mock import text_response

    current = _split_state()
    current["understanding"] = current["understanding"].model_copy(update={"needs_write": False})
    scripted = ScriptedLLM([text_response("collected")])

    def executor(_name, _args):
        raise AssertionError("AA status question must not supplement write prerequisites")

    assert (
        GatherAdapter(build(scripted), executor, supplement_transfer_reads=True)(current, ctx())
        == []
    )
