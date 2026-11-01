"""G19 → G4 assessment panel with real gateway PG and a FakeL3 snapshot.

Does not claim a real L3 assessment execution test.
"""

import asyncio

from safebank_contracts.risk_questionnaire import QUESTIONNAIRE
from sqlalchemy import select

from services.gateway.app import tables as t
from tests.gateway.w3b.db.test_form_evaluate import setup_form


def test_assessment_form_creates_yellow_authoritative_panel_without_execution(app_config):
    async def run():
        app, db, headers, _ = await setup_form(app_config)
        fields = {
            "questionnaire_id": "rq_v1",
            "answers": [{"question_id": f"q{n:02}", "option_id": "a"} for n in range(1, 7)],
        }
        action = await app.client.post(
            "/sessions/ses_form/actions",
            json={
                "request_id": "req_assessment_action",
                "kind": "FORM",
                "action_kind": "ASSESSMENT_SUBMIT",
                "fields": fields,
            },
            headers=headers,
        )
        assert action.status_code == 200, action.text
        body = {
            "request_id": "req_assessment_plan",
            "session_id": "ses_form",
            "form_action_ids": [action.json()["action_id"]],
        }
        response = await app.client.post("/plans/evaluate", json=body, headers=headers)
        assert response.status_code == 200, response.text
        decision = response.json()
        assert decision["decision"] == "REQUIRE_APPROVAL", decision
        assert decision["risk"] == "YELLOW" and decision["approval_id"]
        async with db.connect() as conn:
            evaluation = (
                (
                    await conn.execute(
                        select(t.evaluations).where(t.evaluations.c.plan_id == decision["plan_id"])
                    )
                )
                .mappings()
                .one()
            )
            view = evaluation["view_json"]
            shown = {item["key"]: item for item in view["steps"][0]["fields"]}
            assert shown["questionnaire_id"]["value"] == "rq_v1"
            for question in QUESTIONNAIRE["rq_v1"].values():
                assert shown[f"answer:{question.id}"]["label"] == question.prompt
                assert shown[f"answer:{question.id}"]["value"] == question.options[0].label
            assert not view["otp_required"] and view["risk_ack_required"]
            assert not (await conn.execute(select(t.execution_jobs))).first()
        replay = await app.client.post("/plans/evaluate", json=body, headers=headers)
        assert replay.status_code == 200 and replay.json() == decision, replay.text
        await db.dispose()

    asyncio.run(run())
