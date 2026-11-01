"""Real gateway PG G4 X2 with prior live session context; L3 is a snapshot fake."""

import asyncio
from datetime import datetime, timedelta

import pytest
from safebank_contracts.state import BankState
from sqlalchemy import select

from services.gateway.app import locks
from services.gateway.app import tables as t
from services.gateway.app.utterance.parser import parse
from tests.gateway.w2.test_derived_cancelled import obligations_for, subscription
from tests.gateway.w3b.db.conftest import (
    WALL,
    _default_bank_state,
    _seed_auth,
    _seed_read,
    _seed_session,
    _seed_utterance,
    build_app,
    make_plan_draft_dict,
)
from tests.gateway.w3b.db.test_g4_evaluate import _runtime_db

TEXT = "找出没用的订阅取消掉，省下来的钱每月1号自动转到我的储蓄账户"


def _state():
    state = _default_bank_state()
    state["accounts"]["acc_s"] = {
        **state["accounts"]["acc_a"],
        "id": "acc_s",
        "type": "SAVINGS",
        "display_name": "储蓄",
        "account_no": "6222000000000002",
        "current_card_id": None,
    }
    sub = subscription()
    state["subscriptions"] = [sub.model_dump(mode="json")]
    state["obligations"] = obligations_for(sub).model_dump(mode="json")
    state["obligations"]["covered_customer_ids"] = list(state["versions"])
    return state


def _detected():
    return {
        "items": [
            {
                "merchant_id": "mer_a",
                "merchant_name": "合成订阅",
                "instruction_id": "si_a",
                "billing_id": "bil_a",
                "period": "MONTHLY",
                "typical_amount_cents": 19900,
                "occurrences": 6,
                "last_charged_at": WALL.isoformat(),
                "is_idle": True,
                "usage_evidence": {"recent_usage_count": 0, "previous_usage_count": 1},
                "annual_cost_cents": 238800,
            }
        ]
    }


def _source(param, kind, **source):
    return {"param": param, "source": {"kind": kind, **source}}


@pytest.mark.parametrize("previous_status", ["NO_WRITE", "SCOPED"])
@pytest.mark.parametrize("mandate_condition", ["valid", "cap", "expired"])
def test_g4_x2_uses_current_declared_jwt_bound_root_only(
    app_config, previous_status, mandate_condition
):
    async def run():
        state = _state()
        mandate = state["obligations"]["standing_instructions"]["si_a"]
        if mandate_condition == "cap":
            mandate["max_amount_cents"] = 100
            state["subscriptions"][0]["max_amount_cents"] = 100
        elif mandate_condition == "expired":
            due = datetime.fromisoformat(state["obligations"]["billings"]["bil_a"]["next_due_at"])
            mandate["end_at"] = (due - timedelta(seconds=1)).isoformat()
        app = await build_app(app_config, bank_state_fn=lambda: state)
        db = _runtime_db(app)
        locks.configure(app.client._transport.app.state.db, max_executions=8)

        # Exercise the real fetch_read contract at the predicate stage while
        # retaining the existing snapshot fake; no real bank backend is used.
        async def read(tool, body):
            assert tool == "detect_subscriptions" and body["customer_id"] == "cus_a"
            return _detected()

        app.client._transport.app.state.l3.read = read
        scope = parse(
            TEXT,
            BankState.model_validate(state),
            "cus_a",
            predicates={"idle_subscriptions": ["si_a"]},
        )
        assert scope.status == "SCOPED"
        async with db.begin() as conn:
            await _seed_session(conn, sid="ses_current", customer="cus_a")
            await _seed_auth(conn, jti="jti_current", customer="cus_a")
            await _seed_utterance(
                conn,
                utt_id="utt_context",
                sid="ses_current",
                text="给我出一份9月的账单报告" if previous_status == "NO_WRITE" else "转账 800 元",
                parse_status=previous_status,
            )
            await _seed_utterance(
                conn,
                utt_id="utt_current",
                sid="ses_current",
                text=TEXT,
                scope=scope.scope.model_dump(mode="json"),
            )
            account_read = await _seed_read(
                conn,
                sid="ses_current",
                tool="get_accounts",
                response={
                    "accounts": [
                        {"id": "acc_a", "customer_id": "cus_a", "type": "CHECKING"},
                        {"id": "acc_s", "customer_id": "cus_a", "type": "SAVINGS"},
                    ]
                },
            )
            subscription_read = await _seed_read(
                conn, sid="ses_current", tool="detect_subscriptions", response=_detected()
            )
        steps = [
            {
                "step_id": "cancel",
                "tool": "cancel_subscription",
                "depends_on": [],
                "args": {"instruction_id": "si_a"},
                "arg_sources": [
                    _source(
                        "instruction_id",
                        "read",
                        ref=subscription_read,
                        pointer="/items/0/instruction_id",
                    )
                ],
            },
            {
                "step_id": "save",
                "tool": "schedule_transfer",
                "depends_on": ["cancel"],
                "args": {
                    "from_account_id": "acc_a",
                    "payee_id": None,
                    "to_account_id": "acc_s",
                    "amount_cents": 19900,
                    "period": "MONTHLY",
                    "first_run_at": scope.scope.fields["first_run_at"],
                    "end_at": None,
                },
                "arg_sources": [
                    _source("from_account_id", "read", ref=account_read, pointer="/accounts/0/id"),
                    _source("to_account_id", "read", ref=account_read, pointer="/accounts/1/id"),
                    _source(
                        "amount_cents",
                        "derived",
                        template="cancelled_monthly_fees",
                        inputs=[
                            {"kind": "step", "ref": "cancel"},
                            {
                                "kind": "read",
                                "ref": subscription_read,
                                "pointer": "/items/0/typical_amount_cents",
                            },
                        ],
                    ),
                    _source("period", "utterance", ref="utt_current", span=[16, 18]),
                    _source("first_run_at", "utterance", ref="utt_current", span=[18, 20]),
                ],
            },
        ]
        body = make_plan_draft_dict(
            request_id="req_current",
            session_id="ses_current",
            utterance_ids=["utt_current"],
            steps=steps,
        )
        response = await app.client.post(
            "/plans/evaluate",
            json=body,
            headers=app.auth_headers(sid="ses_current", jti="jti_current", utts=["utt_current"]),
        )
        assert response.status_code == 200, response.text
        decision = response.json()
        if mandate_condition == "valid":
            assert decision["decision"] == "REQUIRE_APPROVAL", decision
            assert decision["risk"] == "YELLOW" and decision["approval_id"], decision
        else:
            assert decision["decision"] == "REPLAN" and decision["approval_id"] is None, decision
            assert any(f["code"] == "UNKNOWN_SENSITIVE_ARG" for f in decision["findings"])
        async with db.connect() as conn:
            plan = (
                (await conn.execute(select(t.plans).where(t.plans.c.id == decision["plan_id"])))
                .mappings()
                .one()
            )
            assert plan["utterance_ids"] == plan["scope_ids"] == ["utt_current"]
            assert not (await conn.execute(select(t.execution_jobs))).first()
        await db.dispose()

    asyncio.run(run())
