"""Real gateway auth/PG: formal readonly phrases and actual scheduled projection."""

import asyncio

from sqlalchemy import select

from services.gateway.app import tables as t
from tests.gateway.w1.db.support import create, harness, headers, login, utter
from tests.gateway.w1.fixtures import bank_state
from tests.gateway.w1.test_nov1_query_cancel_parser import QUERIES, scheduled


def test_formal_queries_never_create_empty_form_or_financial_plan(gateway_db, monkeypatch):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, *_):
            token = await login(client)
            for index, text in enumerate(QUERIES):
                sid = await create(client, token, f"req_query_session_{index}")
                response = await utter(client, token, sid, text, f"req_query_{index}")
                assert response.status_code == 201, response.text
                result = response.json()
                assert result["utterance"]["parse_status"] == "NO_WRITE"
                assert not result["utterance"]["scope"]["action_kinds"]
                assert result["clarification_id"] is None
                current = await client.get(
                    f"/sessions/{sid}/clarifications/current", headers=headers(token)
                )
                assert current.status_code == 200 and current.json()["clarification"] is None
            async with db.connect() as conn:
                assert not (await conn.execute(select(t.plans))).first()
                assert not (await conn.execute(select(t.execution_jobs))).first()

    asyncio.run(run())


def test_cancel_generic_external_projection_has_exact_persisted_instruction_scope(
    gateway_db, monkeypatch
):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, _, _, fake):
            fake.set_snapshot(bank_state().model_copy(update={"instructions": (scheduled(),)}))
            token = await login(client)
            sid = await create(client, token, "req_real_cancel_sid")
            response = await utter(client, token, sid, "取消给赵六的定时转账", "req_real_cancel")
            assert response.status_code == 201, response.text
            data = response.json()
            assert data["utterance"]["parse_status"] == "SCOPED"
            assert data["utterance"]["scope"]["fields"]["instruction_id"] == "si_real"
            assert data["clarification_id"] is None
            async with db.connect() as conn:
                stored = (
                    (
                        await conn.execute(
                            select(t.utterances).where(t.utterances.c.id == data["utterance"]["id"])
                        )
                    )
                    .mappings()
                    .one()
                )
                assert stored["normalized_scope"]["fields"]["instruction_id"] == "si_real"
                assert not (await conn.execute(select(t.execution_jobs))).first()

    asyncio.run(run())


def test_form_guidance_is_fixed_and_never_contains_write_scope(gateway_db, monkeypatch):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, *_):
            token = await login(client)
            cases = [
                ("帮我把风险测评改成R5", "risk_assessment_form"),
                ("给钱七6222020000009999转5000", "register_payee_form"),
                ("我能不能给王五转100？", "transfer_consultation"),
            ]
            for index, (text, template) in enumerate(cases):
                sid = await create(client, token, f"req_guide_sid_{index}")
                response = await utter(client, token, sid, text, f"req_guide_{index}")
                assert response.status_code == 201, response.text
                assert not response.json()["utterance"]["scope"]["action_kinds"]
                current = (
                    await client.get(
                        f"/sessions/{sid}/clarifications/current", headers=headers(token)
                    )
                ).json()["clarification"]
                assert (
                    current["kind"] == "FORM_REQUIRED" and current["prompt_template_id"] == template
                )
                assert current["required_fields"] == [] and current["candidates"] == []
            async with db.connect() as conn:
                assert not (await conn.execute(select(t.plans))).first()
                assert not (await conn.execute(select(t.execution_jobs))).first()

    asyncio.run(run())


def test_real_parent_action_resolves_partial_payee_and_schedule_choices(gateway_db, monkeypatch):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, _, _, fake):
            fake.set_snapshot(
                bank_state().model_copy(
                    update={"instructions": (scheduled(), scheduled(id="si_second"))}
                )
            )
            token = await login(client)
            for index, (text, field, choice) in enumerate(
                [
                    ("给王转200块", "payee_id", "pye_ww"),
                    ("取消给赵六的定时转账", "instruction_id", "si_second"),
                ]
            ):
                sid = await create(client, token, f"req_choice_sid_{index}")
                parent = (await utter(client, token, sid, text, f"req_choice_{index}")).json()
                assert parent["utterance"]["parse_status"] == "CLARIFYING"
                action = await client.post(
                    f"/sessions/{sid}/actions",
                    headers=headers(token),
                    json={
                        "request_id": f"req_pick_{index}",
                        "kind": "CLARIFICATION",
                        "action_kind": None,
                        "fields": [{"field": field, "entity_id": choice}],
                        "parent_utterance_id": parent["utterance"]["id"],
                    },
                )
                assert action.status_code == 200, action.text
                merged = await client.post(
                    f"/sessions/{sid}/utterances",
                    headers=headers(token),
                    json={
                        "request_id": f"req_pick_root_{index}",
                        "kind": "action",
                        "action_id": action.json()["action_id"],
                    },
                )
                assert merged.status_code == 201, merged.text
                scope = merged.json()["utterance"]["scope"]
                assert merged.json()["utterance"]["parse_status"] == "SCOPED"
                if field == "instruction_id":
                    assert scope["fields"][field] == choice
                else:
                    assert (
                        scope["transfers"][0]["target_fingerprint"] == "fp_ww"
                        and scope["transfers"][0]["amount_cents"] == 20000
                    )
            async with db.connect() as conn:
                assert not (await conn.execute(select(t.execution_jobs))).first()

    asyncio.run(run())
