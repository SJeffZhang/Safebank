"""Formal case12 through compiled graph+real read/respond/verify adapters.

The provider understanding and L2 read boundary are synthetic. No production
model or live bank demo is invoked; the original fallback's exact trigger was
not retained in the report.
"""

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from services.agent.app.gateway.wire import GatewayUtterance
from services.agent.app.graph import Ports, UtteranceRef, build_gateway_graph
from services.agent.app.llm.gather import GatherAdapter, ReadResult
from services.agent.app.llm.respond import RespondAdapter
from services.agent.app.llm.verify import OutputVerifier
from tests.agent.routing.test_graph_compiled import _g20_item
from tests.agent.test_bill_accuracy import NoInference, _context
from tests.agent.test_query_gather import state


@pytest.mark.parametrize("remaining_page", [False, True])
def test_original_month_query_never_repairs_consumption_into_transfer_total(remaining_page):
    calls = []

    def execute(tool, args):
        calls.append(tool)
        data = {
            "get_profile": {"sim_now": "2026-10-15T09:00:00+08:00"},
            "get_transactions": {
                "items": [],
                "income_cents": 0,
                "expense_cents": 95050,
                "next_cursor": "more" if remaining_page else None,
            },
            "get_spending_summary": {
                "month": "2026-10",
                "total_cents": 55050,
                "transfer_out_cents": 40000,
            },
        }[tool]
        return ReadResult(source_ref="src_" + tool, data=data)

    def no_write(*args):
        raise AssertionError("read-only request must never plan/evaluate/execute")

    text = "查一下我10月的消费流水"
    ports = Ports(
        understand=lambda *args: state(text)["understanding"],
        gather=GatherAdapter(NoInference(), execute, fetch_business_date=True),
        plan=no_write,
        evaluate=no_write,
        execute=no_write,
        respond=RespondAdapter(NoInference()),
        verify_output=OutputVerifier(),
    )

    class GatewayReads:
        def get_utterances(self, ids, context):
            item = _g20_item("utt_query", text=text, parse_status="NO_WRITE")
            item["scope"]["action_kinds"] = []
            return (GatewayUtterance.model_validate(item),)

        def get_clarification(self, context):
            return None

    graph = build_gateway_graph(ports, InMemorySaver(), GatewayReads())
    result = graph.invoke(
        {
            "utterances": [UtteranceRef(id="utt_query", kind="typed", text=text)],
        },
        config={"configurable": {"thread_id": "ses_bill"}},
        context=_context(),
    )
    reply = result["messages"][-1].content
    assert "消费合计550.50元" in reply
    assert "转账支出另列400.00元" in reply
    assert "950.50元" not in reply and "无法处理" not in reply
    assert result["execution"] is None and result["plan_draft"] is None
    assert calls[0] == "get_profile" and calls[-1] == "get_spending_summary"
    assert set(calls) == {"get_profile", "get_transactions", "get_spending_summary"}
    assert len(calls) < 20  # repeated cursors cannot cause unbounded reads
    assert ("不是全部流水" in reply) == remaining_page
