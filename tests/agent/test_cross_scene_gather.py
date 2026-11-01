"""Scoped cross-scene analysis uses actual read envelopes, never model prose."""

from copy import deepcopy

import pytest
from langchain_core.messages import AIMessage
from safebank_contracts.tools import READ_ARGS
from safebank_contracts.views import READ_RESULTS

from services.agent.app.graph.state import AgentContext, ReadRecord, Understanding, UtteranceRef
from services.agent.app.llm.cards import generate_cards
from services.agent.app.llm.gather import GatherAdapter, ReadResult, monthly_report_requested
from tests.agent.gateway.read_cases import READ_CASES
from tests.agent.test_bill_accuracy import _LIVE_ANOMALIES, _summary

X1 = "分析我上月消费，找出异常交易，并把卡的单笔限额设成1000"


class CardsOnly:
    """Reproduce the production omission: valid model output reads only cards."""

    def __init__(self):
        self.calls = 0

    def bind_tools(self, _tools):
        return self

    def invoke(self, _messages):
        self.calls += 1
        if self.calls == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {"id": "call_cards", "name": "get_cards", "args": {"account_id": None}}
                ],
            )
        return AIMessage(content="done")


def state(text=X1):
    return {
        "customer_id": "cus_zs",
        "session_id": "ses_cross",
        "utterances": [UtteranceRef(id="utt_cross", kind="typed", text=text)],
        "understanding": Understanding(intents=["bill", "update_card_limits"], needs_write=True),
        "parse_status": "SCOPED",
        "reads": [],
    }


def context():
    return AgentContext(
        assistant_token="synthetic-token", customer_id="cus_zs", session_id="ses_cross"
    )


def executor(calls, sim_now="2026-10-15T01:00:00Z"):
    def read(tool, args):
        READ_ARGS[tool].model_validate(args)
        calls.append((tool, deepcopy(args)))
        if tool == "get_profile":
            data = deepcopy(READ_CASES[tool][1])
            data.update(customer_id="cus_zs", sim_now=sim_now)
            untrusted = ("/display_name",)
        elif tool == "get_spending_summary":
            data = _summary(args["month"])
            untrusted = ()
        elif tool == "detect_anomalies":
            data = deepcopy(_LIVE_ANOMALIES["data"])
            month = args["from_at"][:7]
            for item in data["items"]:
                item["transaction"]["occurred_at"] = f"{month}-12T02:00:00+08:00"
            untrusted = ("/items/0/transaction/counterparty",)
        elif tool == "get_cards":
            data = {
                "items": [
                    {
                        "id": "crd_zs_1",
                        "account_id": "acc_zs_chk",
                        "last4": "0818",
                        "status": "ACTIVE",
                        "single_limit_cents": 500000,
                        "daily_limit_cents": 2000000,
                        "daily_used_cents": 0,
                        "remaining_daily_cents": 2000000,
                        "max_single_limit_cents": 1000000,
                        "max_daily_limit_cents": 5000000,
                        "controls": {
                            "online": True,
                            "overseas": False,
                            "atm": True,
                            "quickpass": True,
                        },
                        "current": True,
                        "replacement_id": None,
                    }
                ]
            }
            untrusted = ()
        else:
            raise AssertionError(f"unexpected read {tool}")
        READ_RESULTS[tool].model_validate(data)
        return ReadResult(
            data=data,
            source_ref=f"src_actual_{tool}",
            versions={"cus_zs": 7},
            observed_at=sim_now,
            untrusted_paths=untrusted,
        )

    return read


@pytest.mark.parametrize(
    "text,sim_now,month,next_month",
    [
        (X1, "2026-10-15T01:00:00Z", "2026-09", "2026-10"),
        (X1, "2026-12-31T16:30:00Z", "2026-12", "2027-01"),
        (
            "分析我2024年2月账单，找出异常交易，并把卡的单笔限额降低到1000元",
            "2026-10-15T01:00:00Z",
            "2024-02",
            "2024-03",
        ),
    ],
)
def test_x1_reads_complete_month_and_cards_despite_model_stopping(text, sim_now, month, next_month):
    calls = []
    current = state(text)
    current["business_date"] = "当前业务时间：2020-01-01 09:00（北京时间，Asia/Shanghai）"
    current["reads"] = [
        ReadRecord(
            tool="get_transactions",
            source_ref="src_stale_page",
            data={"items": [{"amount_cents": 1}], "next_cursor": "more"},
        )
    ]
    before = deepcopy(current)
    records = GatherAdapter(
        CardsOnly(),
        executor(calls, sim_now),
        fetch_business_date=True,
        supplement_transfer_reads=True,
    )(current, context())
    assert calls == [
        ("get_profile", {}),
        ("get_spending_summary", {"month": month, "account_id": None}),
        (
            "detect_anomalies",
            {
                "from_at": f"{month}-01T00:00:00+08:00",
                "to_at": f"{next_month}-01T00:00:00+08:00",
                "account_id": None,
            },
        ),
        ("get_cards", {"account_id": None}),
    ]
    assert [record.source_ref for record in records] == [
        "src_actual_get_profile",
        "src_actual_get_spending_summary",
        "src_actual_detect_anomalies",
        "src_actual_get_cards",
    ]
    assert all(
        record.versions == {"cus_zs": 7} and record.observed_at == sim_now for record in records
    )
    assert records[0].untrusted_paths == ("/display_name",)
    assert "<untrusted_data" in records[0].data["display_name"]
    assert "<untrusted_data" in records[2].data["items"][0]["transaction"]["counterparty"]
    assert records[1].data["total_cents"] == 644800
    assert current == before and not monthly_report_requested(current)


@pytest.mark.parametrize("phase", ["REQUIRE_APPROVAL", "EXECUTED"])
def test_x1_actual_analysis_evidence_is_not_hidden_by_write_status(phase):
    calls = []
    current = state()
    read = executor(calls)
    current["reads"] = [
        ReadRecord(tool=tool, args=args, **read(tool, args).model_dump())
        for tool, args in [
            ("get_profile", {}),
            ("get_spending_summary", {"month": "2026-09", "account_id": None}),
            (
                "detect_anomalies",
                {
                    "from_at": "2026-09-01T00:00:00+08:00",
                    "to_at": "2026-10-01T00:00:00+08:00",
                    "account_id": None,
                },
            ),
            ("get_cards", {"account_id": None}),
        ]
    ]
    current["decision"] = {
        "plan_id": "pln_x1",
        "decision": "REQUIRE_APPROVAL",
        "approval_id": "apv_x1",
    }
    if phase == "EXECUTED":
        current["execution"] = {"plan_id": "pln_x1", "status": "EXECUTED", "steps": []}
    cards = generate_cards(current)
    assert [card.type for card in cards] == ["PLAN_STATUS", "TRANSACTIONS", "ANOMALIES"]
    assert (
        cards[1].source_ref == "src_actual_get_spending_summary"
        and cards[1].data["month"] == "2026-09"
    )
    assert cards[1].data["total_cents"] == 644800
    assert cards[2].source_ref == "src_actual_detect_anomalies" and len(cards[2].data["items"]) == 3
    assert cards[0].data["status"] == ("EXECUTED" if phase == "EXECUTED" else "PENDING_APPROVAL")


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
def test_x1_fast_reads_never_bypass_scope_or_actual_clarification(change):
    current = state()
    current.update(change)
    before = deepcopy(current)
    calls = []
    GatherAdapter(
        CardsOnly(), executor(calls), fetch_business_date=True, supplement_transfer_reads=True
    )(current, context())
    assert all(tool not in {"get_spending_summary", "detect_anomalies"} for tool, _ in calls)
    assert current == before


@pytest.mark.parametrize(
    "text,kind,needs_write",
    [
        (X1, "action", True),
        (X1, "hint", True),
        (X1, "context", True),
        (X1, "typed", False),
        (f"“{X1}”是什么意思", "typed", True),
        (X1 + "，给王五转800", "typed", True),
        ("分析我上月消费，找出异常交易", "typed", True),
        ("分析我上月消费，并把卡的单笔限额设成1000", "typed", True),
    ],
)
def test_x1_fast_reads_are_limited_to_original_complete_typed_combo(text, kind, needs_write):
    current = state(text)
    current["utterances"] = [UtteranceRef(id="utt_cross", kind=kind, text=text)]
    current["understanding"] = Understanding(intents=["bill", "card"], needs_write=needs_write)
    before = deepcopy(current)
    calls = []
    GatherAdapter(
        CardsOnly(), executor(calls), fetch_business_date=True, supplement_transfer_reads=True
    )(current, context())
    assert all(tool not in {"get_spending_summary", "detect_anomalies"} for tool, _ in calls)
    assert current == before


def test_x1_does_not_use_multiple_utterances_as_one_new_authorization():
    current = state()
    current["utterances"].append(UtteranceRef(id="utt_extra", kind="typed", text="转账800"))
    calls = []
    GatherAdapter(
        CardsOnly(), executor(calls), fetch_business_date=True, supplement_transfer_reads=True
    )(current, context())
    assert all(tool not in {"get_spending_summary", "detect_anomalies"} for tool, _ in calls)


@pytest.mark.parametrize(
    "failed_tool", ["get_profile", "get_spending_summary", "detect_anomalies", "get_cards"]
)
def test_x1_dependency_failure_does_not_return_partial_analysis_or_fallback(failed_tool):
    calls = []
    read = executor(calls)
    model = CardsOnly()

    def failing(tool, args):
        if tool == failed_tool:
            raise RuntimeError("dependency unavailable")
        return read(tool, args)

    with pytest.raises(RuntimeError, match="dependency unavailable"):
        GatherAdapter(model, failing, fetch_business_date=True, supplement_transfer_reads=True)(
            state(), context()
        )
    assert model.calls == 0


@pytest.mark.parametrize("status", [402, 403, 429])
def test_x1_profile_provider_limits_propagate_without_inference(status):
    class ProviderLimit(Exception):
        status_code = status

    model = CardsOnly()

    def failing(_tool, _args):
        raise ProviderLimit()

    with pytest.raises(ProviderLimit):
        GatherAdapter(model, failing, fetch_business_date=True, supplement_transfer_reads=True)(
            state(), context()
        )
    assert model.calls == 0


def test_read_only_monthly_report_remains_no_write_and_uses_actual_full_aggregates():
    current = state("生成上个月的账单报告")
    current["parse_status"] = "NO_WRITE"
    current["understanding"] = Understanding(intents=["bill_report"], needs_write=False)
    before = deepcopy(current)
    calls = []
    model = CardsOnly()
    records = GatherAdapter(
        model, executor(calls), fetch_business_date=True, supplement_transfer_reads=True
    )(current, context())
    assert [r.tool for r in records] == ["get_profile", "get_spending_summary", "detect_anomalies"]
    assert records[1].data["total_cents"] == 644800 and model.calls == 0
    assert current == before and monthly_report_requested(current)


@pytest.mark.parametrize(
    "corruption",
    [
        "missing_profile",
        "missing_summary",
        "missing_anomalies",
        "wrong_summary_month",
        "partial_anomaly_range",
        "out_of_month_transaction",
        "non_x1_write",
    ],
)
def test_x1_analysis_cards_require_complete_current_month_evidence(corruption):
    current = state()
    read = executor([])
    current["reads"] = [
        ReadRecord(tool=tool, args=args, **read(tool, args).model_dump())
        for tool, args in [
            ("get_profile", {}),
            ("get_spending_summary", {"month": "2026-09", "account_id": None}),
            (
                "detect_anomalies",
                {
                    "from_at": "2026-09-01T00:00:00+08:00",
                    "to_at": "2026-10-01T00:00:00+08:00",
                    "account_id": None,
                },
            ),
        ]
    ]
    current["decision"] = {
        "plan_id": "pln_x1",
        "decision": "REQUIRE_APPROVAL",
        "approval_id": "apv_x1",
    }
    if corruption.startswith("missing_"):
        missing = {
            "missing_profile": "get_profile",
            "missing_summary": "get_spending_summary",
            "missing_anomalies": "detect_anomalies",
        }[corruption]
        current["reads"] = [r for r in current["reads"] if r.tool != missing]
    elif corruption == "wrong_summary_month":
        current["reads"][1].data["month"] = "2026-10"
    elif corruption == "partial_anomaly_range":
        current["reads"][2].args["to_at"] = "2026-09-30T23:59:59+08:00"
    elif corruption == "out_of_month_transaction":
        current["reads"][2].data["items"][0]["transaction"]["occurred_at"] = (
            "2026-10-01T00:00:00+08:00"
        )
    else:
        current["utterances"] = [
            UtteranceRef(id="utt_cross", kind="typed", text="把卡单笔限额设成1000")
        ]
    assert [card.type for card in generate_cards(current)] == ["PLAN_STATUS"]
