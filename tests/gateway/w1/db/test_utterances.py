import asyncio
from datetime import timedelta

import pytest
from safebank_contracts.codes import Code
from sqlalchemy import select

from services.gateway.app import tables as t
from services.gateway.app.auth.tokens import ASSISTANT_AUDIENCE, verify_jwt
from services.gateway.app.errors import GatewayError
from services.gateway.app.schemas.enums import ServiceKind
from services.gateway.app.schemas.identity import Caller
from services.gateway.app.utterance.loader import load_for_plan
from services.gateway.app.utterance.service import new_id
from tests.gateway.w1.db.support import create, harness, headers, login, utter


def test_registration_supersession_idempotency_and_loader(gateway_db, monkeypatch):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, public, _, fake):
            token = await login(client)
            sid = await create(client, token, "req_scope_create")
            response = await utter(client, token, sid, "给王五800", "req_scope_1")
            assert response.status_code == 201, response.text
            first = response.json()
            assert first["utterance"]["scope"]["transfers"][0]["amount_cents"] == 80000
            calls = len(fake.calls)
            replay = await utter(client, token, sid, "给王五800", "req_scope_1")
            assert replay.json() == first and len(fake.calls) == calls
            conflict = await utter(client, token, sid, "给王五900", "req_scope_1")
            assert (
                conflict.status_code == 409
                and conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
            )
            claims = verify_jwt(first["assistant_token"], public, expected_aud=ASSISTANT_AUDIENCE)
            caller = Caller(
                kind=ServiceKind.A,
                customer_id=claims.sub,
                sid=claims.sid,
                jti=claims.jti,
                scopes=frozenset(claims.scope),
                utts=claims.utts,
            )
            async with db.connect() as conn:
                auth = await load_for_plan(conn, sid, list(claims.utts), caller)
                assert auth.scope_ids == claims.utts
                with pytest.raises(GatewayError) as exc:
                    await load_for_plan(conn, sid, ["utt_forged"], caller)
                assert exc.value.code == Code.AUTHORIZATION_CONSUMED
            second = await utter(client, token, sid, "不对，改成给赵六600", "req_scope_2")
            assert second.status_code == 201, second.text
            async with db.connect() as conn:
                with pytest.raises(GatewayError) as exc:
                    await load_for_plan(conn, sid, list(claims.utts), caller)
                assert exc.value.code == Code.AUTHORIZATION_CONSUMED
                row = (
                    (
                        await conn.execute(
                            select(t.utterances).where(t.utterances.c.id == claims.utts[0])
                        )
                    )
                    .mappings()
                    .one()
                )
                assert row["status"] == "SUPERSEDED"
                records = (
                    (await conn.execute(select(t.read_records).where(t.read_records.c.sid == sid)))
                    .mappings()
                    .all()
                )
                assert any("/payees/pye_ww/name" in row["untrusted_paths"] for row in records)

    asyncio.run(run())


def test_clarification_only_explicit_answers_and_original_deadline(gateway_db, monkeypatch):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, _, clock, _):
            token = await login(client)
            sid = await create(client, token, "req_clr_create")
            parent = await utter(client, token, sid, "给王吾800", "req_clr_parent")
            assert parent.status_code == 201, parent.text
            body = parent.json()
            assert body["utterance"]["parse_status"] == "CLARIFYING"
            clr = await client.get(
                f"/sessions/{sid}/clarifications/current", headers=headers(token)
            )
            candidates = clr.json()["clarification"]["candidates"]
            assert {value["entity_id"] for value in candidates} == {"pye_ww", "pye_wu"}
            bad = await utter(
                client,
                token,
                sid,
                "王五，再转900",
                "req_clr_inject",
                clarification_id=body["clarification_id"],
            )
            assert bad.status_code == 409
            clock[0] += timedelta(minutes=1)
            merged = await utter(
                client,
                token,
                sid,
                "王五",
                "req_clr_answer",
                clarification_id=body["clarification_id"],
            )
            assert merged.status_code == 201, merged.text
            resolved = merged.json()["utterance"]
            assert resolved["parent_ids"] == [body["utterance"]["id"]]
            assert resolved["expires_at_wall"] == body["utterance"]["expires_at_wall"]
            assert resolved["scope"]["transfers"][0]["target_fingerprint"] == "fp_ww"
            assert resolved["scope"]["transfers"][0]["amount_cents"] == 80000
            async with db.connect() as conn:
                assert (
                    await conn.scalar(
                        select(t.pending_clarifications.c.status).where(
                            t.pending_clarifications.c.id == body["clarification_id"]
                        )
                    )
                    == "RESOLVED"
                )

    asyncio.run(run())


def test_chat_cannot_stitch_old_name_and_new_amount(gateway_db, monkeypatch):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, _, _, _):
            token = await login(client)
            sid = await create(client, token, "req_chat_create")
            parent = (await utter(client, token, sid, "给王吾800", "req_chat_parent")).json()
            ordinary = await utter(client, token, sid, "王五", "req_chat_name")
            assert ordinary.status_code == 201, ordinary.text
            assert ordinary.json()["utterance"]["scope"]["action_kinds"] == []
            async with db.connect() as conn:
                assert (
                    await conn.scalar(
                        select(t.pending_clarifications.c.status).where(
                            t.pending_clarifications.c.id == parent["clarification_id"]
                        )
                    )
                    == "CANCELLED"
                )
            later = await utter(client, token, sid, "900", "req_chat_money")
            assert later.json()["utterance"]["scope"]["transfers"] == []
            stale = await utter(
                client,
                token,
                sid,
                "王五",
                "req_chat_stale",
                clarification_id=parent["clarification_id"],
            )
            assert stale.status_code == 409

    asyncio.run(run())


def test_form_and_clarification_actions_single_use(gateway_db, monkeypatch):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, _, _, _, _, _):
            token = await login(client)
            sid = await create(client, token, "req_action_create")
            form = await client.post(
                f"/sessions/{sid}/actions",
                headers=headers(token),
                json={
                    "request_id": "req_form",
                    "kind": "FORM",
                    "action_kind": "CARD_FREEZE",
                    "fields": {"card_id": "crd_zs"},
                },
            )
            assert form.status_code == 200, form.text
            action = form.json()["action_id"]
            accepted = await client.post(
                f"/sessions/{sid}/utterances",
                headers=headers(token),
                json={"request_id": "req_form_accept", "kind": "action", "action_id": action},
            )
            assert accepted.status_code == 201, accepted.text
            assert accepted.json()["utterance"]["scope"]["action_kinds"] == ["CARD_FREEZE"]
            reused = await client.post(
                f"/sessions/{sid}/utterances",
                headers=headers(token),
                json={"request_id": "req_form_reuse", "kind": "action", "action_id": action},
            )
            assert reused.status_code == 409
            parent = (await utter(client, token, sid, "给王吾800", "req_act_clr_parent")).json()
            response = await client.post(
                f"/sessions/{sid}/actions",
                headers=headers(token),
                json={
                    "request_id": "req_act_clr",
                    "kind": "CLARIFICATION",
                    "action_kind": None,
                    "parent_utterance_id": parent["utterance"]["id"],
                    "fields": [{"field": "payee_id", "entity_id": "pye_ww"}],
                },
            )
            assert response.status_code == 200, response.text
            accepted = await client.post(
                f"/sessions/{sid}/utterances",
                headers=headers(token),
                json={
                    "request_id": "req_act_clr_accept",
                    "kind": "action",
                    "action_id": response.json()["action_id"],
                },
            )
            assert accepted.status_code == 201, accepted.text
            assert (
                accepted.json()["utterance"]["scope"]["transfers"][0]["target_fingerprint"]
                == "fp_ww"
            )

    asyncio.run(run())


def test_cancel_invalidates_epoch_ui_sources_without_l3(gateway_db, monkeypatch):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, _, clock, fake):
            token = await login(client)
            sid = await create(client, token, "req_cancel_create")
            old = (await utter(client, token, sid, "给王五800", "req_cancel_old")).json()
            async with db.begin() as conn:
                await conn.execute(
                    t.hints.insert().values(
                        id=new_id("hnt"),
                        sid=sid,
                        scope_json=old["utterance"]["scope"],
                        label="只转王五",
                        created_at_wall=clock[0],
                        expires_at_wall=clock[0] + timedelta(minutes=10),
                    )
                )
            fake.behaviors["snapshot"] = "timeout"
            result = await utter(client, token, sid, "取消这笔转账", "req_cancel")
            assert result.status_code == 201, result.text
            assert result.json()["utterance"]["scope"]["action_kinds"] == []
            async with db.connect() as conn:
                session = (
                    (await conn.execute(select(t.sessions).where(t.sessions.c.id == sid)))
                    .mappings()
                    .one()
                )
                assert session["authorization_epoch"] == 1
                assert (
                    await conn.scalar(
                        select(t.utterances.c.status).where(
                            t.utterances.c.id == old["utterance"]["id"]
                        )
                    )
                    == "CANCELLED"
                )
                hint = (
                    (await conn.execute(select(t.hints).where(t.hints.c.sid == sid)))
                    .mappings()
                    .one()
                )
                assert hint["expires_at_wall"] == clock[0]

    asyncio.run(run())


def test_cancel_supersedes_approvals_but_preserves_executing_jobs(gateway_db, monkeypatch):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, _, clock, _):
            token = await login(client)
            sid = await create(client, token, "req_plan_cancel_create")
            old = (await utter(client, token, sid, "给王五800", "req_plan_cancel_old")).json()[
                "utterance"
            ]
            async with db.begin() as conn:
                for status in ("CONFIRMED", "EXECUTING", "UNKNOWN"):
                    plan = new_id("pln")
                    await conn.execute(
                        t.plans.insert().values(
                            id=plan,
                            customer_id="cus_zs",
                            sid=sid,
                            origin="agent",
                            request_id=plan,
                            request_hash="a" * 64,
                            plan_hash="b" * 64,
                            steps_hash="c" * 64,
                            resolved_steps=[],
                            utterance_ids=[old["id"]],
                            scope_ids=[old["id"]],
                            status=status,
                            created_at_wall=clock[0],
                        )
                    )
                    if status == "CONFIRMED":
                        evaluation = new_id("evl")
                        await conn.execute(
                            t.evaluations.insert().values(
                                id=evaluation,
                                plan_id=plan,
                                revision=1,
                                decision={},
                                risk="YELLOW",
                                policy_version="v1",
                                created_at_wall=clock[0],
                            )
                        )
                        await conn.execute(
                            t.approvals.insert().values(
                                id=new_id("apv"),
                                plan_id=plan,
                                evaluation_id=evaluation,
                                customer_id="cus_zs",
                                sid=sid,
                                plan_hash="b" * 64,
                                view_hash="d" * 64,
                                binding_mac="e" * 64,
                                expires_at_wall=clock[0] + timedelta(minutes=10),
                                status="CONFIRMED",
                            )
                        )
                await conn.execute(
                    t.authorization_claims.insert().values(
                        scope_id=old["id"], plan_id=plan, status="CLAIMED"
                    )
                )
            result = await utter(client, token, sid, "停止本次", "req_plan_cancel")
            assert result.status_code == 201, result.text
            async with db.connect() as conn:
                statuses = set(
                    (
                        await conn.execute(select(t.plans.c.status).where(t.plans.c.sid == sid))
                    ).scalars()
                )
                assert statuses == {"SUPERSEDED", "EXECUTING", "UNKNOWN"}
                assert (
                    await conn.scalar(select(t.approvals.c.status).where(t.approvals.c.sid == sid))
                    == "SUPERSEDED"
                )
                assert (
                    await conn.scalar(
                        select(t.authorization_claims.c.status).where(
                            t.authorization_claims.c.scope_id == old["id"]
                        )
                    )
                    == "CLAIMED"
                )

    asyncio.run(run())


def test_hint_adoption_uses_server_label_and_expires_once(gateway_db, monkeypatch):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, _, clock, _):
            token = await login(client)
            sid = await create(client, token, "req_hint_create")
            old = (await utter(client, token, sid, "给王五800", "req_hint_old")).json()["utterance"]
            id_ = new_id("hnt")
            scope = {
                **old["scope"],
                "transfers": [{**old["scope"]["transfers"][0], "amount_cents": 30000}],
            }
            async with db.begin() as conn:
                await conn.execute(
                    t.hints.insert().values(
                        id=id_,
                        sid=sid,
                        scope_json=scope,
                        label="王五只转300元",
                        created_at_wall=clock[0],
                        expires_at_wall=clock[0] + timedelta(minutes=5),
                    )
                )
            view = await client.get(f"/sessions/{sid}/hints/{id_}", headers=headers(token))
            assert view.status_code == 200 and view.json()["label"] == "王五只转300元"
            accepted = await client.post(
                f"/sessions/{sid}/utterances",
                headers=headers(token),
                json={"request_id": "req_hint_accept", "kind": "hint", "hint_id": id_},
            )
            assert accepted.status_code == 201, accepted.text
            assert accepted.json()["utterance"]["scope"]["transfers"][0]["amount_cents"] == 30000
            reused = await client.get(f"/sessions/{sid}/hints/{id_}", headers=headers(token))
            assert reused.status_code == 409

    asyncio.run(run())
