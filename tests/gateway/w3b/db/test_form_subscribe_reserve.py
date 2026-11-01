"""G19/G4 ordinary FORM subscribe must not consume authorized bill reserves."""

import asyncio

from sqlalchemy import select

from services.gateway.app import locks
from services.gateway.app import tables as t
from tests.bankcore.obligations_fixtures import (
    raw_billing,
    raw_mandate,
    raw_merchant,
    raw_projection_state,
)
from tests.bankcore.test_funds import funds_state
from tests.gateway.w3b.db.test_form_evaluate import setup_form


def test_form_subscribe_shortfall_replans_without_approval_or_execution(app_config):
    async def run():
        app, db, headers, _ = await setup_form(app_config)
        try:
            locks.configure(app.client._transport.app.state.db, max_executions=8)
            state = funds_state().model_copy(
                update={
                    "obligations": raw_projection_state(
                        billings=[raw_billing()],
                        mandates=[raw_mandate()],
                        merchants=[raw_merchant()],
                    ).obligations
                }
            )
            app.client._transport.app.state.l3._state_fn = lambda: state.model_dump(mode="json")
            action = await app.client.post(
                "/sessions/ses_form/actions",
                headers=headers,
                json={
                    "request_id": "req_sub_action",
                    "kind": "FORM",
                    "action_kind": "FUND_SUBSCRIBE",
                    "fields": {
                        "account_id": "acc_a",
                        "product_id": "prd_mmf",
                        "amount_cents": 2000000,
                    },
                },
            )
            assert action.status_code == 200, action.text
            decision = await app.client.post(
                "/plans/evaluate",
                headers=headers,
                json={
                    "request_id": "req_sub_plan",
                    "session_id": "ses_form",
                    "form_action_ids": [action.json()["action_id"]],
                },
            )
            assert decision.status_code == 200, decision.text
            result = decision.json()
            assert result["decision"] == "REPLAN" and result["approval_id"] is None, result
            assert any(f["code"] == "SCHEDULED_SHORTFALL" for f in result["findings"])
            assert result["preflight"]["scheduled_reserve_cents"] == 3000
            async with db.connect() as conn:
                assert not (await conn.execute(select(t.approvals))).first()
                assert not (await conn.execute(select(t.execution_jobs))).first()
        finally:
            await db.dispose()

    asyncio.run(run())
