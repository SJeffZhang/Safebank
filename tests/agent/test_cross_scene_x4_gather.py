"""X4 needs real suspicious-card, owned-card and account read sources."""

from copy import deepcopy

import pytest
from safebank_contracts.views import READ_RESULTS

from services.agent.app.graph.state import Understanding
from services.agent.app.llm.client import make_llm
from services.agent.app.llm.gather import GatherAdapter, ReadResult
from services.agent.app.llm.plan import PlanAdapter
from tests.agent.gateway.read_cases import READ_CASES
from tests.agent.llm_mock import ScriptedLLM, tool_response
from tests.agent.test_cross_scene_gather import CardsOnly, context, executor, state

X4 = "把这笔可疑交易对应的卡冻结，再从储蓄转2000到活期应急"


def x4_state():
    current = state(X4)
    current["understanding"] = Understanding(
        intents=["freeze_card", "transfer_self"], needs_write=True
    )
    return current


def x4_executor(calls, sim_now="2026-10-15T01:00:00Z"):
    base = executor(calls, sim_now)

    def read(tool, args):
        if tool not in {"get_accounts", "list_payees"}:
            return base(tool, args)
        calls.append((tool, deepcopy(args)))
        data = deepcopy(READ_CASES[tool][1])
        if tool == "get_accounts":
            base_account = {
                "display_name": "账户",
                "account_no_masked": "**** 0818",
                "currency": "CNY",
                "balance_cents": 600000,
                "available_cents": 600000,
                "pending_debit_cents": 0,
                "pending_credit_cents": 0,
                "single_limit_cents": None,
                "daily_limit_cents": None,
            }
            data["accounts"] = [
                dict(base_account, id="acc_zs_chk", type="CHECKING", current_card_id="crd_zs_1"),
                dict(base_account, id="acc_zs_save", type="SAVINGS", current_card_id=None),
            ]
        READ_RESULTS[tool].model_validate(data)
        return ReadResult(
            data=data, source_ref=f"src_actual_{tool}", versions={"cus_zs": 7}, observed_at=sim_now
        )

    return read


@pytest.mark.parametrize(
    "sim_now,start,end",
    [
        ("2026-10-15T01:00:00Z", "2026-09-01T00:00:00+08:00", "2026-10-15T09:00:00+08:00"),
        ("2026-12-31T16:30:00Z", "2026-12-01T00:00:00+08:00", "2027-01-01T00:30:00+08:00"),
    ],
)
def test_x4_gathers_full_g3_predicate_interval_and_owned_sources_when_model_stops(
    sim_now, start, end
):
    current = x4_state()
    current["business_date"] = "当前业务时间：2020-01-01 00:00（北京时间）"
    before = deepcopy(current)
    calls = []
    records = GatherAdapter(
        CardsOnly(),
        x4_executor(calls, sim_now),
        fetch_business_date=True,
        supplement_transfer_reads=True,
    )(current, context())
    assert calls == [
        ("get_profile", {}),
        ("detect_anomalies", {"from_at": start, "to_at": end, "account_id": None}),
        ("get_cards", {"account_id": None}),
        ("get_accounts", {"history_days": 0}),
    ]
    assert [r.source_ref for r in records] == [
        "src_actual_get_profile",
        "src_actual_detect_anomalies",
        "src_actual_get_cards",
        "src_actual_get_accounts",
    ]
    assert all(r.versions == {"cus_zs": 7} and r.observed_at == sim_now for r in records)
    assert current == before


def test_x4_gather_provides_actual_sources_for_both_steps_without_inventing_authorization():
    current = x4_state()
    current["reads"] = GatherAdapter(
        CardsOnly(), x4_executor([]), fetch_business_date=True, supplement_transfer_reads=True
    )(current, context())
    amount_start = X4.index("2000")
    proposal = {
        "intents": ["freeze_card", "transfer_self"],
        "rationale": "冻结原授权可疑卡并本人应急互转",
        "steps": [
            {
                "step_id": "freeze",
                "tool": "freeze_card",
                "args": {"card_id": "crd_zs_1"},
                "depends_on": [],
                "arg_sources": [
                    {
                        "param": "card_id",
                        "source": {
                            "kind": "read",
                            "ref": "src_actual_get_cards",
                            "pointer": "/items/0/id",
                        },
                    }
                ],
            },
            {
                "step_id": "emergency",
                "tool": "transfer",
                "args": {
                    "from_account_id": "acc_zs_save",
                    "payee_id": {"kind": "none"},
                    "to_account_id": {"kind": "id", "id": "acc_zs_chk"},
                    "amount_cents": 200000,
                    "memo": "__NULL__",
                },
                "depends_on": ["freeze"],
                "arg_sources": [
                    {
                        "param": "from_account_id",
                        "source": {
                            "kind": "read",
                            "ref": "src_actual_get_accounts",
                            "pointer": "/accounts/1/id",
                        },
                    },
                    {
                        "param": "to_account_id",
                        "source": {
                            "kind": "read",
                            "ref": "src_actual_get_accounts",
                            "pointer": "/accounts/0/id",
                        },
                    },
                    {
                        "param": "amount_cents",
                        "source": {
                            "kind": "utterance",
                            "ref": "utt_cross",
                            "span": [amount_start, amount_start + 4],
                        },
                    },
                ],
            },
        ],
    }
    scripted = ScriptedLLM([tool_response("submit_action_plan", proposal)])
    draft = PlanAdapter(make_llm(api_key="synthetic", http_client=scripted.client), max_retries=0)(
        current, context()
    )
    assert [step.tool for step in draft.steps] == ["freeze_card", "transfer"]
    assert (
        draft.steps[1].args.amount_cents == 200000
        and draft.steps[1].args.from_account_id == "acc_zs_save"
    )
    assert draft.steps[1].arg_sources[-1].source.ref == "utt_cross"
    assert current["parse_status"] == "SCOPED" and current["understanding"].needs_write is True


@pytest.mark.parametrize(
    "change",
    [
        {"parse_status": "NO_WRITE"},
        {"parse_status": "CLARIFYING"},
        {"parse_status": "INTENT_UNCLEAR"},
        {"clarification_id": "clr_actual"},
        {"clarification": {"required_fields": ["card_id"]}},
        {"echoes_untrusted": True},
    ],
)
def test_x4_prerequisites_never_override_actual_scope_or_clarification(change):
    current = x4_state()
    current.update(change)
    before = deepcopy(current)
    calls = []
    GatherAdapter(
        CardsOnly(), x4_executor(calls), fetch_business_date=True, supplement_transfer_reads=True
    )(current, context())
    assert all(tool not in {"detect_anomalies", "get_accounts"} for tool, _ in calls)
    assert current == before


@pytest.mark.parametrize(
    "kind,text",
    [
        ("action", X4),
        ("hint", X4),
        ("context", X4),
        ("typed", f"“{X4}”是什么意思"),
        ("typed", X4 + "，再给王五800"),
        ("typed", "冻结可疑卡"),
        ("typed", "从储蓄转2000到活期应急"),
        ("typed", X4.replace("活期应急", "王五")),
    ],
)
def test_x4_prerequisites_are_only_for_complete_original_typed_self_emergency_combo(kind, text):
    from services.agent.app.graph.state import UtteranceRef

    current = x4_state()
    current["utterances"] = [UtteranceRef(id="utt_cross", kind=kind, text=text)]
    calls = []
    GatherAdapter(
        CardsOnly(), x4_executor(calls), fetch_business_date=True, supplement_transfer_reads=True
    )(current, context())
    assert all(tool not in {"detect_anomalies", "get_accounts"} for tool, _ in calls)


@pytest.mark.parametrize(
    "failed_tool", ["get_profile", "detect_anomalies", "get_cards", "get_accounts"]
)
def test_x4_required_read_failure_propagates_without_model_or_partial_plan(failed_tool):
    model = CardsOnly()
    base = x4_executor([])

    def read(tool, args):
        if tool == failed_tool:
            raise RuntimeError("read dependency failed")
        return base(tool, args)

    with pytest.raises(RuntimeError, match="read dependency failed"):
        GatherAdapter(model, read, fetch_business_date=True, supplement_transfer_reads=True)(
            x4_state(), context()
        )
    assert model.calls == 0


def test_x4_anomaly_range_matches_the_actual_g3_predicate_code():
    import asyncio
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from services.gateway.app.utterance.service import predicate_values
    from tests.gateway.w1.fixtures import bank_state

    sim_now = "2026-12-31T16:30:00Z"
    calls = []
    records = GatherAdapter(
        CardsOnly(),
        x4_executor(calls, sim_now),
        fetch_business_date=True,
        supplement_transfer_reads=True,
    )(x4_state(), context())
    # L3 load_bank_state normalizes sim_now to Asia/Shanghai before G3.
    bank = bank_state().model_copy(
        update={"sim_now": datetime.fromisoformat(sim_now).astimezone(ZoneInfo("Asia/Shanghai"))}
    )

    class ReadOnlyL3:
        async def snapshot(self, payload):
            assert payload["customer_id"] == "cus_zs"
            return bank.model_dump(mode="json")

        async def read(self, tool, payload):
            assert tool == "detect_anomalies" and payload["customer_id"] == "cus_zs"
            return {"items": []}

    evidence = []
    asyncio.run(predicate_values(ReadOnlyL3(), bank, "cus_zs", X4, evidence))
    anomaly = next(r for r in records if r.tool == "detect_anomalies")
    assert anomaly.args == evidence[0]["args"]


def test_x4_reads_all_owned_entities_without_selecting_or_expanding_targets():
    current = x4_state()
    before = deepcopy(current)
    base = x4_executor([])

    def read(tool, args):
        result = base(tool, args)
        if tool == "get_cards":
            data = deepcopy(result.data)
            extra = deepcopy(data["items"][0])
            extra["id"] = "crd_other"
            extra["last4"] = "6612"
            data["items"].append(extra)
            return result.model_copy(update={"data": data})
        return result

    records = GatherAdapter(
        CardsOnly(), read, fetch_business_date=True, supplement_transfer_reads=True
    )(current, context())
    assert next(r for r in records if r.tool == "get_cards").args == {"account_id": None}
    assert [
        card["id"] for card in next(r for r in records if r.tool == "get_cards").data["items"]
    ] == ["crd_zs_1", "crd_other"]
    assert current == before
