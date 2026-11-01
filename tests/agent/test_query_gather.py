"""Read-only completion uses actual returned identities and complete cursor pages."""

import pytest
from safebank_contracts.codes import Code

from services.agent.app.gateway.errors import GatewayResponseError
from services.agent.app.graph.state import Understanding, UtteranceRef
from services.agent.app.llm.gather import GatherAdapter, ReadResult
from tests.agent.llm_mock import ScriptedLLM, text_response, tool_response
from tests.agent.test_bill_accuracy import NoInference, _context
from tests.agent.test_redemption_gather import adapter, redemption_state, result_for


def state(text):
    return {
        "utterances": [UtteranceRef(id="utt_query", kind="typed", text=text)],
        "understanding": Understanding(intents=["query"], needs_write=False),
        "parse_status": "NO_WRITE",
        "reads": [],
    }


def test_product_compare_reads_actual_ids_then_actual_comparison():
    calls = []

    def execute(tool, args):
        calls.append((tool, args))
        if tool == "get_profile":
            data = {"sim_now": "2026-10-15T09:00:00+08:00"}
        elif tool == "list_products":
            data = {
                "items": [
                    {"id": "prd_from_a", "name": "稳盈短债"},
                    {"id": "prd_from_b", "name": "稳健增利"},
                ],
                "next_cursor": None,
            }
        elif tool == "compare_products":
            data = {"items": [{"id": p} for p in args["product_ids"]]}
        else:
            raise AssertionError(tool)
        return ReadResult(source_ref="src_" + tool, data=data)

    records = GatherAdapter(NoInference(), execute, fetch_business_date=True)(
        state("比较一下稳盈短债和稳健增利"), _context()
    )
    assert calls[-1] == ("compare_products", {"product_ids": ["prd_from_a", "prd_from_b"]})
    assert records[-1].source_ref == "src_compare_products"


def test_merchant_query_follows_pages_and_uses_returned_merchant_filter():
    calls = []

    def execute(tool, args):
        calls.append((tool, args))
        if tool == "get_profile":
            data = {"sim_now": "2026-10-15T09:00:00+08:00"}
        elif args["merchant_id"]:
            assert args["merchant_id"] == "mer_from_row"
            data = {
                "items": [],
                "income_cents": 0,
                "expense_cents": 4550,
                "totals_scope": "filtered_all",
                "next_cursor": None,
            }
        elif args["cursor"] is None:
            data = {
                "items": [{"counterparty": "其他", "merchant_id": "mer_other"}],
                "next_cursor": "page_two",
            }
        else:
            assert args["cursor"] == "page_two"
            data = {
                "items": [{"counterparty": "兰州拉面", "merchant_id": "mer_from_row"}],
                "next_cursor": None,
            }
        return ReadResult(source_ref=f"src_read_{len(calls)}", data=data)

    records = GatherAdapter(NoInference(), execute, fetch_business_date=True)(
        state("我在兰州拉面花了多少钱"), _context()
    )
    tx = [args for tool, args in calls if tool == "get_transactions"]
    assert len(tx) == 3
    assert tx[0]["from_at"] == "2026-04-18T09:00:00+08:00"
    assert tx[0]["to_at"] == "2026-10-15T09:00:00+08:00"
    assert all(args["account_id"] is None for args in tx)
    assert records[-1].data["expense_cents"] == 4550


@pytest.mark.parametrize("failure", ["invalid", "not_found"])
def test_redemption_retries_missing_successful_holdings_after_a_failed_attempt(failure):
    calls = []

    def execute(tool, args):
        calls.append((tool, args))
        if tool == "get_holdings" and args.get("product_id") == "prd_wrong":
            raise GatewayResponseError(
                status=404, code=Code.NOT_FOUND, details={}, trace_id="safe", decision=None
            )
        return result_for(tool)

    bad = (
        {"account_id": None, "product_id": "prd_wrong"}
        if failure == "not_found"
        else {"account_id": None, "product_id": 9}
    )
    scripted = ScriptedLLM([tool_response("get_holdings", bad), text_response("done")])
    records = adapter(scripted, execute)(redemption_state(), _context())
    assert any(r.tool == "get_holdings" and r.args["product_id"] is None for r in records)
    assert calls[-1] == ("get_holdings", {"account_id": None, "product_id": None})


def test_month_transactions_have_authoritative_consumption_source_before_verification():
    from services.agent.app.llm.respond import RespondAdapter
    from services.agent.app.llm.verify import OutputVerifier

    def execute(tool, args):
        data = {
            "get_profile": {"sim_now": "2026-10-15T09:00:00+08:00"},
            "get_transactions": {
                "items": [],
                "next_cursor": None,
                "expense_cents": 95050,
                "income_cents": 0,
            },
            "get_spending_summary": {
                "month": "2026-10",
                "total_cents": 55050,
                "transfer_out_cents": 40000,
            },
        }[tool]
        return ReadResult(source_ref="src_" + tool, data=data)

    st = state("查一下我10月的消费流水")
    st["reads"] = GatherAdapter(NoInference(), execute, fetch_business_date=True)(st, _context())
    st["reply_draft"] = RespondAdapter(NoInference())(st, _context())
    final = OutputVerifier()(st, _context())
    assert "550.50元" in final and "400.00元" in final
    assert "无法处理" not in final


def test_merchant_reply_uses_authoritative_filtered_total_and_discloses_read_window():
    from services.agent.app.graph.state import ReadRecord
    from services.agent.app.llm.respond import RespondAdapter
    from services.agent.app.llm.verify import OutputVerifier

    st = state("我在兰州拉面花了多少钱")
    args = {"from_at": "2026-04-18T09:00:00+08:00", "to_at": "2026-10-15T09:00:00+08:00"}
    st["reads"] = [
        ReadRecord(
            source_ref="src_scan",
            tool="get_transactions",
            args={**args, "merchant_id": None},
            data={
                "items": [{"counterparty": "兰州拉面", "merchant_id": "mer_actual"}],
                "next_cursor": None,
            },
        ),
        ReadRecord(
            source_ref="src_filtered",
            tool="get_transactions",
            args={**args, "merchant_id": "mer_actual"},
            data={
                "items": [],
                "next_cursor": None,
                "income_cents": 0,
                "expense_cents": 4550,
                "totals_scope": "filtered_all",
            },
        ),
    ]
    st["reply_draft"] = RespondAdapter(NoInference())(st, _context())
    final = OutputVerifier()(st, _context())
    assert "45.50元" in final and "2026-04-18" in final and "2026-10-15" in final
    assert "180天" in final


def test_incomplete_merchant_scan_never_claims_absence_or_calls_a_guessed_filter():
    calls = []

    def execute(tool, args):
        calls.append((tool, args))
        data = (
            {"sim_now": "2026-10-15T09:00:00+08:00"}
            if tool == "get_profile"
            else {"items": [], "next_cursor": "repeated_cursor"}
        )
        return ReadResult(source_ref=f"src_{len(calls)}", data=data)

    st = state("我在兰州拉面花了多少钱")
    st["reads"] = GatherAdapter(NoInference(), execute, fetch_business_date=True)(st, _context())
    from services.agent.app.llm.respond import RespondAdapter

    text = RespondAdapter(NoInference())(st, _context())
    assert len(calls) == 3
    assert all(
        args.get("merchant_id") is None for tool, args in calls if tool == "get_transactions"
    )
    assert "不能据此认定没有消费" in text and "0.00元" not in text


def test_comparison_does_not_select_duplicate_product_names():
    calls = []

    def execute(tool, args):
        calls.append(tool)
        data = (
            {"sim_now": "2026-10-15T09:00:00+08:00"}
            if tool == "get_profile"
            else {
                "items": [
                    {"id": "prd_a", "name": "稳盈短债"},
                    {"id": "prd_b", "name": "稳盈短债"},
                    {"id": "prd_c", "name": "稳健增利"},
                ],
                "next_cursor": None,
            }
        )
        return ReadResult(source_ref="src_" + tool, data=data)

    st = state("比较一下稳盈短债和稳健增利")
    st["reads"] = GatherAdapter(NoInference(), execute, fetch_business_date=True)(st, _context())
    from services.agent.app.llm.respond import RespondAdapter

    assert "未能唯一" in RespondAdapter(NoInference())(st, _context())
    assert calls == ["get_profile", "list_products"]


@pytest.mark.parametrize("status", [402, 403, 429])
def test_required_query_failure_propagates_without_another_read(status):
    calls = []

    class Stop(Exception):
        status_code = status

    def execute(tool, args):
        calls.append(tool)
        raise Stop()

    with pytest.raises(Stop):
        GatherAdapter(NoInference(), execute, fetch_business_date=True)(
            state("比较一下稳盈短债和稳健增利"), _context()
        )
    assert calls == ["get_profile"]


def test_holdings_query_reads_product_names_without_guessing_from_codes():
    calls = []

    def execute(tool, args):
        calls.append(tool)
        data = {
            "get_profile": {"sim_now": "2026-10-15T09:00:00+08:00"},
            "get_accounts": {"accounts": []},
            "get_holdings": {"holdings": [], "orders": []},
            "list_products": {"items": [], "next_cursor": None},
        }[tool]
        return ReadResult(source_ref="src_" + tool, data=data)

    GatherAdapter(NoInference(), execute, fetch_business_date=True)(
        state("我的持仓和在途订单有哪些"), _context()
    )
    assert calls == ["get_profile", "get_accounts", "get_holdings", "list_products"]


def test_upcoming_query_uses_business_clock_for_its_window_and_separates_reserve():
    from services.agent.app.llm.respond import RespondAdapter
    from services.agent.app.llm.verify import OutputVerifier

    def execute(tool, args):
        data = (
            {"sim_now": "2026-10-15T01:00:00Z"}
            if tool == "get_profile"
            else {
                "items": [],
                "scheduled_items": [],
                "charges_total_cents": 4500,
                "scheduled_transfers_total_cents": 20000,
                "reserve_total_cents": 24500,
            }
        )
        return ReadResult(source_ref="src_" + tool, data=data)

    st = state("未来7天有哪些扣费")
    st["reads"] = GatherAdapter(NoInference(), execute, fetch_business_date=True)(st, _context())
    st["reply_draft"] = RespondAdapter(NoInference())(st, _context())
    text = OutputVerifier()(st, _context())
    assert "2026-10-15 09:00" in text and "2026-10-22 09:00" in text
    assert "245.00元" in text and "200.00元" in text and "45.00元" in text


@pytest.mark.parametrize("status", ["ACTIVE", "FROZEN"])
def test_card_query_distinguishes_single_daily_and_funds_constraints(status):
    from services.agent.app.llm.respond import RespondAdapter
    from services.agent.app.llm.verify import OutputVerifier

    def execute(tool, args):
        data = {
            "get_profile": {"sim_now": "2026-10-15T09:00:00+08:00"},
            "get_accounts": {
                "accounts": [
                    {
                        "id": "acc_chk",
                        "available_cents": 610000,
                        "balance_cents": 610000,
                        "pending_debit_cents": 0,
                    }
                ]
            },
            "get_cards": {
                "items": [
                    {
                        "id": "crd_current",
                        "account_id": "acc_chk",
                        "current": True,
                        "last4": "1234",
                        "status": status,
                        "single_limit_cents": 500000,
                        "daily_limit_cents": 2000000,
                        "daily_used_cents": 40000,
                        "remaining_daily_cents": 1960000,
                    }
                ]
            },
        }[tool]
        return ReadResult(source_ref="src_" + tool, data=data)

    st = state("我的卡今天还能刷多少")
    st["reads"] = GatherAdapter(NoInference(), execute, fetch_business_date=True)(st, _context())
    st["reply_draft"] = RespondAdapter(NoInference())(st, _context())
    text = OutputVerifier()(st, _context())
    if status == "ACTIVE":
        assert "当日剩余限额19600.00元" in text
        assert "单笔最多5000.00元" in text and "今日合计最多6100.00元" in text
    else:
        assert "不能刷卡" in text and "最多" not in text
