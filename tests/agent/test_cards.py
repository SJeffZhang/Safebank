"""C1e cards.py deterministic UICard tests (Task 5).

No real LLM, no PG — card generation from fake state.
"""

from __future__ import annotations

from services.agent.app.gateway.wire import GatewayDecision, GatewayReplanHint
from services.agent.app.graph.state import ReadRecord
from services.agent.app.llm.cards import (
    _filter_untrusted,
    _safe_data,
    generate_cards,
)

# ── _safe_data / _filter_untrusted ─────────────────────────────────────


class TestFilterUntrusted:
    def test_strips_name(self):
        data = {"name": "张三", "balance_cents": 600000}
        result = _filter_untrusted(data)
        assert "balance_cents" in result
        assert "name" not in result

    def test_strips_memo_merchant(self):
        data = {"memo": "还款", "merchant_name": "某商户", "amount_cents": 80000}
        result = _filter_untrusted(data)
        assert "amount_cents" in result
        assert "memo" not in result
        assert "merchant_name" not in result

    def test_nested(self):
        data = {
            "accounts": [
                {"id": "acc_1", "name": "活期", "balance_cents": 100000},
                {"id": "acc_2", "alias": "储蓄", "balance_cents": 200000},
            ]
        }
        result = _filter_untrusted(data)
        assert result["accounts"][0]["id"] == "acc_1"
        assert "name" not in result["accounts"][0]
        assert result["accounts"][1]["balance_cents"] == 200000
        assert "alias" not in result["accounts"][1]


class TestSafeData:
    def test_from_dict(self):
        result = _safe_data({"a": 1, "name": "bad"})
        assert result == {"a": 1}

    def test_from_none(self):
        assert _safe_data(None) == {}

    def test_from_int(self):
        assert _safe_data(42) == {}


# ── generate_cards ─────────────────────────────────────────────────────


class TestGenerateCards:
    def test_empty_state(self):
        assert generate_cards({}) == ()

    def test_deny_generates_blocked(self):
        state = {
            "decision": {
                "decision": "DENY",
                "plan_id": "pln_1",
                "findings": [
                    {"code": "LIMIT_EXCEEDED", "resolver": None},
                ],
            }
        }
        cards = generate_cards(state)
        assert len(cards) == 1
        assert cards[0].type == "BLOCKED"
        assert "LIMIT_EXCEEDED" in str(cards[0].data)

    def test_executed_generates_plan_status(self):
        state = {
            "execution": {
                "plan_id": "pln_1",
                "status": "EXECUTED",
                "steps": [
                    {
                        "step_id": "step_1",
                        "tool": "transfer",
                        "status": "SUCCESS",
                    }
                ],
            }
        }
        cards = generate_cards(state)
        assert len(cards) == 1
        assert cards[0].type == "PLAN_STATUS"
        assert cards[0].data["status"] == "EXECUTED"
        assert len(cards[0].data["steps"]) == 1

    def test_unknown_generates_plan_status(self):
        state = {"execution": {"plan_id": "pln_1", "status": "UNKNOWN", "steps": []}}
        cards = generate_cards(state)
        assert len(cards) == 1
        assert cards[0].type == "PLAN_STATUS"

    def test_replan_user_generates_hints(self):
        decision = GatewayDecision(
            plan_id="pln_1",
            evaluation_id="evl_1",
            revision=1,
            decision="REPLAN",
            risk="RED",
            findings=[{"code": "CARD_SINGLE_LIMIT", "effect": "REPLAN", "resolver": "user"}],
            replan_hints=[
                GatewayReplanHint(
                    id="hnt_abc",
                    label="最多 5000.00 元",
                    scope={
                        "action_kinds": ["TRANSFER_EXTERNAL"],
                        "from_account_id": "acc_a",
                        "transfers": [{"target_fingerprint": "fp_wang", "amount_cents": 500000}],
                    },
                    expires_at_wall="2026-11-01T14:00:00Z",
                )
            ],
        )
        state = {"decision": decision.model_dump(mode="json")}
        cards = generate_cards(state)
        assert len(cards) == 1
        assert cards[0].type == "REPLAN_HINTS"
        assert cards[0].data["hints"] == ["最多 5000.00 元"]
        assert len(cards[0].actions) == 1
        assert cards[0].actions[0].kind == "HINT"
        assert cards[0].actions[0].ref == "hnt_abc"

    def test_require_approval_adds_open_approval(self):
        state = {
            "decision": {
                "decision": "REQUIRE_APPROVAL",
                "plan_id": "pln_1",
                "approval_id": "apv_test123",
            },
            "execution": {
                "plan_id": "pln_1",
                "status": "EXECUTED",  # EXECUTED → PLAN_STATUS first
                "steps": [],
            },
        }
        cards = generate_cards(state)
        assert len(cards) == 1  # PLAN_STATUS + OPEN_APPROVAL action
        assert cards[0].type == "PLAN_STATUS"
        # OPEN_APPROVAL was appended as action
        approval_actions = [a for a in cards[0].actions if a.kind == "OPEN_APPROVAL"]
        assert len(approval_actions) == 1
        assert approval_actions[0].ref == "apv_test123"

    def test_clarification(self):
        state = {
            "understanding": type(
                "u", (), {"unclear": True, "needs_write": False, "intents": []}
            )(),
            "parse_status": "INTENT_UNCLEAR",
            "clarification": {
                "id": "asd_test",
                "candidates": [{"entity_id": "acc_zs_chk", "label": "活期"}],
            },
        }
        cards = generate_cards(state)
        assert len(cards) == 1
        assert cards[0].type == "CLARIFICATION"
        assert len(cards[0].actions) == 1
        assert cards[0].actions[0].kind == "CLARIFY"

    def test_reads_analysis_cards(self):
        state = {
            "reads": [
                ReadRecord(
                    source_ref="src_a",
                    tool="get_accounts",
                    args={},
                    data={
                        "accounts": [
                            {"id": "acc_zs_chk", "balance_cents": 600000},
                        ]
                    },
                ),
                ReadRecord(
                    source_ref="src_b",
                    tool="get_cards",
                    args={},
                    data={"cards": [{"id": "crd_zs_1", "last4": "1234"}]},
                ),
            ],
            "understanding": type(
                "u", (), {"unclear": False, "needs_write": False, "intents": []}
            )(),
        }
        cards = generate_cards(state)
        assert len(cards) == 2
        types = [c.type for c in cards]
        assert "ACCOUNT" in types
        assert "CARD" in types

    def test_reads_not_generated_when_write_pending(self):
        """Analysis reads should NOT become cards when a write operation is pending."""
        state = {
            "decision": {"decision": "ALLOW", "plan_id": "pln_1"},
            "reads": [
                ReadRecord(
                    source_ref="src_a",
                    tool="get_accounts",
                    args={},
                    data={"accounts": []},
                ),
            ],
            "understanding": type(
                "u", (), {"unclear": False, "needs_write": True, "intents": []}
            )(),
        }
        cards = generate_cards(state)
        # ALLOW + needs_write=True → no read cards (write flow, not analysis)
        assert all(c.type != "ACCOUNT" for c in cards)

    def test_name_stripped_from_read_cards(self):
        """Untrusted name fields must NOT appear in card data."""
        state = {
            "reads": [
                ReadRecord(
                    source_ref="src_a",
                    tool="get_accounts",
                    args={},
                    data={
                        "accounts": [
                            {
                                "id": "acc_zs_chk",
                                "name": "张三活期",
                                "alias": "日常",
                                "balance_cents": 600000,
                            }
                        ]
                    },
                ),
            ],
        }
        cards = generate_cards(state)
        assert len(cards) >= 1
        card = cards[0]
        raw = str(card.data)
        assert "张三" not in raw
        assert "日常" not in raw

    def test_card_ids_unique(self):
        state = {
            "execution": {"plan_id": "p1", "status": "EXECUTED", "steps": []},
            "reads": [
                ReadRecord(
                    source_ref="s1",
                    tool="get_accounts",
                    args={},
                    data={"accounts": [{"id": "a1", "balance_cents": 1}]},
                ),
            ],
        }
        cards = generate_cards(state)
        ids = [c.id for c in cards]
        assert len(ids) == len(set(ids))

    def test_failed_generates_blocked(self):
        state = {
            "execution": {"plan_id": "p1", "status": "FAILED", "steps": []},
            "decision": {"decision": "ALLOW", "plan_id": "p1"},
        }
        cards = generate_cards(state)
        assert any(c.type == "BLOCKED" for c in cards)

    # ── P1-3 card cap at 5 ─────────────────────────────────────────────

    def test_card_cap_at_5(self):
        """With 6+ read cards, output capped at 5; priority cards first."""
        state = {
            "reads": [
                ReadRecord(
                    source_ref=f"src_{i}",
                    tool="get_accounts",
                    args={},
                    data={"accounts": [{"id": f"acc_{i}", "balance_cents": i}]},
                )
                for i in range(8)
            ],
            "understanding": type(
                "u", (), {"unclear": False, "needs_write": False, "intents": []}
            )(),
        }
        cards = generate_cards(state)
        assert len(cards) == 5
