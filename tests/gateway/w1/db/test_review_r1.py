"""Real PostgreSQL G3/G19/loader regressions for W1 review findings 5–7."""

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
from services.gateway.app.utterance.parser import is_correction, parse, scope_is_complete
from services.gateway.app.utterance.service import merge_scope, new_id
from tests.gateway.w1.db.support import create, harness, headers, login, utter
from tests.gateway.w1.fixtures import bank_state

# Unknown registered target and an explicit decimal amount: no prefix/name-number ambiguity.
UNCLEAR_CORRECTION_BODY = "给钱七900"


def caller_for(body, public):
    claims = verify_jwt(body["assistant_token"], public, expected_aud=ASSISTANT_AUDIENCE)
    return Caller(
        kind=ServiceKind.A,
        customer_id=claims.sub,
        sid=claims.sid,
        jti=claims.jti,
        scopes=frozenset(claims.scope),
        utts=claims.utts,
    )


async def cannot_load(db, sid, body, public):
    caller = caller_for(body, public)
    async with db.connect() as conn:
        with pytest.raises(GatewayError):
            await load_for_plan(conn, sid, list(caller.utts), caller)


@pytest.mark.parametrize("text", ["给王吾800，给赵柳900", "给王五800，给王吾900"])
def test_r5_multi_target_ambiguity_cannot_be_promoted_to_authority(gateway_db, monkeypatch, text):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, public, _, _):
            token = await login(client)
            sid = await create(client, token, new_id("req"))
            response = await utter(client, token, sid, text)
            assert response.status_code == 201, response.text
            registered = response.json()
            assert registered["utterance"]["parse_status"] == "INTENT_UNCLEAR"
            current = (
                await client.get(f"/sessions/{sid}/clarifications/current", headers=headers(token))
            ).json()["clarification"]
            assert current["kind"] == "FORM_REQUIRED" and current["candidates"] == []
            action = await client.post(
                f"/sessions/{sid}/actions",
                headers=headers(token),
                json={
                    "request_id": "req_r5_action",
                    "kind": "CLARIFICATION",
                    "action_kind": None,
                    "parent_utterance_id": registered["utterance"]["id"],
                    "fields": [{"field": "payee_id", "entity_id": "pye_ww"}],
                },
            )
            assert action.status_code == 409, action.text
            await cannot_load(db, sid, registered, public)

    asyncio.run(run())


def test_r6_form_required_rejects_empty_g19_clarification(gateway_db, monkeypatch):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, public, _, _):
            token = await login(client)
            sid = await create(client, token, new_id("req"))
            registered = (await utter(client, token, sid, "明天给王五800")).json()
            action = await client.post(
                f"/sessions/{sid}/actions",
                headers=headers(token),
                json={
                    "request_id": "req_r6_action",
                    "kind": "CLARIFICATION",
                    "action_kind": None,
                    "parent_utterance_id": registered["utterance"]["id"],
                    "fields": [],
                },
            )
            assert action.status_code == 409, action.text
            async with db.connect() as conn:
                assert not (
                    await conn.execute(select(t.form_actions).where(t.form_actions.c.sid == sid))
                ).all()
            await cannot_load(db, sid, registered, public)

    asyncio.run(run())


def test_r6_existing_empty_action_cannot_bypass_g3_guard(gateway_db, monkeypatch):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, public, clock, _):
            token = await login(client)
            sid = await create(client, token, new_id("req"))
            registered = (await utter(client, token, sid, "明天给王五800")).json()
            action_id = new_id("act")
            async with db.begin() as conn:
                await conn.execute(
                    t.form_actions.insert().values(
                        id=action_id,
                        sid=sid,
                        kind="CLARIFICATION",
                        normalized_fields=[],
                        parent_utterance_id=registered["utterance"]["id"],
                        created_at_wall=clock[0],
                        expires_at_wall=clock[0] + timedelta(minutes=10),
                    )
                )
            adopted = await client.post(
                f"/sessions/{sid}/utterances",
                headers=headers(token),
                json={"request_id": "req_r6_legacy", "kind": "action", "action_id": action_id},
            )
            assert adopted.status_code == 409, adopted.text
            async with db.connect() as conn:
                rows = (
                    (await conn.execute(select(t.utterances).where(t.utterances.c.sid == sid)))
                    .mappings()
                    .all()
                )
                assert all(row["parse_status"] != "SCOPED" for row in rows)
            await cannot_load(db, sid, registered, public)

    asyncio.run(run())


@pytest.mark.parametrize("separator", ["，", "。", "；", "\n", " "])
@pytest.mark.parametrize("replacement", ["改成", "改为", "换成"])
def test_r7_unclear_correction_fixture_has_no_candidate_authority(separator, replacement):
    text = f"不对{separator}{replacement}{UNCLEAR_CORRECTION_BODY}"
    assert is_correction(text)
    result = parse(text, bank_state(), "cus_zs")
    assert result.status == "INTENT_UNCLEAR"
    assert result.scope.action_kinds == ()
    assert not result.scope.transfers and not scope_is_complete(result.scope)
    assert result.required_fields == [] and result.candidates == []


@pytest.mark.parametrize("separator", ["，", "。", "；", "\n", " "])
@pytest.mark.parametrize("replacement", ["改成", "改为", "换成"])
def test_r7_prefix_correction_requires_exact_candidate_selection(separator, replacement):
    bank = bank_state()
    text = f"不对{separator}{replacement}给王转900块"
    assert is_correction(text)
    result = parse(text, bank, "cus_zs")
    assert result.status == "CLARIFYING"
    assert result.required_fields == ["payee_id"]
    assert {item["entity_id"] for item in result.candidates} == {"pye_ww", "pye_wu"}
    assert not result.scope.transfers and not scope_is_complete(result.scope)
    assert result.scope.fields["amount_cents"] == 90000
    clarification = {
        "kind": "SLOTS",
        "status": "OPEN",
        "required_fields": result.required_fields,
        "candidate_refs": result.candidates,
        "partial_scope": result.scope.model_dump(mode="json"),
    }
    for chosen in ({}, {"payee_id": "pye_zl"}):
        with pytest.raises(GatewayError) as exc:
            merge_scope(clarification, chosen, bank)
        assert exc.value.code == Code.INTENT_SCOPE_EXCEEDED and exc.value.status == 409
    selected = merge_scope(clarification, {"payee_id": "pye_ww"}, bank)
    assert scope_is_complete(selected)
    assert selected.from_account_id == "acc_zs"
    assert len(selected.transfers) == 1
    assert selected.transfers[0].target_fingerprint == "fp_ww"
    assert selected.transfers[0].amount_cents == 90000


def test_r7_prefix_correction_cannot_load_until_explicit_g19_g3_selection(gateway_db, monkeypatch):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, public, _, _):
            token = await login(client)
            sid = await create(client, token, new_id("req"))
            old = (await utter(client, token, sid, "给王五800")).json()
            response = await utter(client, token, sid, "不对，改成给王转900块", "req_r7_prefix")
            assert response.status_code == 201, response.text
            parent = response.json()
            assert parent["utterance"]["parse_status"] == "CLARIFYING"
            assert parent["utterance"]["scope"]["transfers"] == []
            assert parent["utterance"]["scope"]["fields"]["amount_cents"] == 90000
            current = (
                await client.get(f"/sessions/{sid}/clarifications/current", headers=headers(token))
            ).json()["clarification"]
            assert current["kind"] == "SLOTS" and current["required_fields"] == ["payee_id"]
            assert {item["entity_id"] for item in current["candidates"]} == {"pye_ww", "pye_wu"}
            await cannot_load(db, sid, old, public)
            await cannot_load(db, sid, parent, public)
            parent_caller = caller_for(parent, public)
            assert parent_caller.kind is ServiceKind.A and parent_caller.jti
            assert "assistant" in parent_caller.scopes
            assert parent["utterance"]["id"] in parent_caller.utts
            async with db.connect() as conn:
                with pytest.raises(GatewayError) as exc:
                    await load_for_plan(
                        conn,
                        sid,
                        [parent["utterance"]["id"]],
                        parent_caller,
                    )
                assert exc.value.code == Code.INTENT_UNCLEAR and exc.value.status == 409
                assert (
                    await conn.scalar(
                        select(t.utterances.c.status).where(
                            t.utterances.c.id == old["utterance"]["id"]
                        )
                    )
                    == "SUPERSEDED"
                )
            for index, fields in enumerate(([], [{"field": "payee_id", "entity_id": "pye_zl"}])):
                rejected = await client.post(
                    f"/sessions/{sid}/actions",
                    headers=headers(token),
                    json={
                        "request_id": f"req_r7_prefix_bad_{index}",
                        "kind": "CLARIFICATION",
                        "action_kind": None,
                        "parent_utterance_id": parent["utterance"]["id"],
                        "fields": fields,
                    },
                )
                assert rejected.status_code == 409, rejected.text
                assert rejected.json()["error"]["code"] == "INTENT_SCOPE_EXCEEDED"
            action = await client.post(
                f"/sessions/{sid}/actions",
                headers=headers(token),
                json={
                    "request_id": "req_r7_prefix_pick",
                    "kind": "CLARIFICATION",
                    "action_kind": None,
                    "parent_utterance_id": parent["utterance"]["id"],
                    "fields": [{"field": "payee_id", "entity_id": "pye_ww"}],
                },
            )
            assert action.status_code == 200, action.text
            await cannot_load(db, sid, parent, public)
            adopted = await client.post(
                f"/sessions/{sid}/utterances",
                headers=headers(token),
                json={
                    "request_id": "req_r7_prefix_adopt",
                    "kind": "action",
                    "action_id": action.json()["action_id"],
                },
            )
            assert adopted.status_code == 201, adopted.text
            selected = adopted.json()
            assert selected["utterance"]["parse_status"] == "SCOPED"
            assert selected["utterance"]["parent_ids"] == [parent["utterance"]["id"]]
            assert selected["utterance"]["scope"]["transfers"][0]["target_fingerprint"] == "fp_ww"
            assert selected["utterance"]["scope"]["transfers"][0]["amount_cents"] == 90000
            caller = caller_for(selected, public)
            async with db.connect() as conn:
                auth = await load_for_plan(conn, sid, [selected["utterance"]["id"]], caller)
                assert auth.scope_ids == (selected["utterance"]["id"],)
                assert not (await conn.execute(select(t.plans).where(t.plans.c.sid == sid))).first()
                assert not (
                    await conn.execute(
                        select(t.execution_jobs).where(
                            t.execution_jobs.c.plan_id.in_(
                                select(t.plans.c.id).where(t.plans.c.sid == sid)
                            )
                        )
                    )
                ).first()
            await cannot_load(db, sid, old, public)

    asyncio.run(run())


@pytest.mark.parametrize("separator", ["，", "。", "；", "\n", " "])
@pytest.mark.parametrize("replacement", ["改成", "改为", "换成"])
@pytest.mark.parametrize("unavailable", [False, True])
def test_r7_unclear_correction_consumes_old_authority_but_preserves_execution(
    gateway_db, monkeypatch, separator, replacement, unavailable
):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, public, clock, fake):
            token = await login(client)
            sid = await create(client, token, new_id("req"))
            old = (await utter(client, token, sid, "给王五800")).json()
            async with db.begin() as conn:
                for status in ("CONFIRMED", "EXECUTING", "UNKNOWN"):
                    plan_id = new_id("pln")
                    await conn.execute(
                        t.plans.insert().values(
                            id=plan_id,
                            customer_id="cus_zs",
                            sid=sid,
                            origin="agent",
                            request_id=plan_id,
                            request_hash="a" * 64,
                            plan_hash="b" * 64,
                            steps_hash="c" * 64,
                            resolved_steps=[],
                            utterance_ids=[old["utterance"]["id"]],
                            scope_ids=[old["utterance"]["id"]],
                            status=status,
                            created_at_wall=clock[0],
                        )
                    )
                    if status == "CONFIRMED":
                        evaluation_id = new_id("evl")
                        await conn.execute(
                            t.evaluations.insert().values(
                                id=evaluation_id,
                                plan_id=plan_id,
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
                                plan_id=plan_id,
                                evaluation_id=evaluation_id,
                                customer_id="cus_zs",
                                sid=sid,
                                plan_hash="b" * 64,
                                view_hash="d" * 64,
                                binding_mac="e" * 64,
                                status="CONFIRMED",
                                expires_at_wall=clock[0] + timedelta(minutes=10),
                            )
                        )
                await conn.execute(
                    t.authorization_claims.insert().values(
                        scope_id=old["utterance"]["id"], plan_id=plan_id, status="CLAIMED"
                    )
                )
            if unavailable:
                fake.behaviors["snapshot"] = "timeout"
            corrected = await utter(
                client,
                token,
                sid,
                f"不对{separator}{replacement}{UNCLEAR_CORRECTION_BODY}",
                "req_r7_correction",
            )
            assert corrected.status_code == 201, corrected.text
            assert corrected.json()["utterance"]["parse_status"] == "INTENT_UNCLEAR"
            assert corrected.json()["utterance"]["scope"]["action_kinds"] == []
            clarification = (
                await client.get(f"/sessions/{sid}/clarifications/current", headers=headers(token))
            ).json()["clarification"]
            assert clarification["kind"] == "FORM_REQUIRED"
            await cannot_load(db, sid, old, public)
            old_visible = await client.get(
                f"/sessions/{sid}/utterances", headers=headers(old["assistant_token"], "a-test")
            )
            assert old_visible.status_code == 200 and old_visible.json()["items"] == []
            async with db.connect() as conn:
                assert (
                    await conn.scalar(
                        select(t.utterances.c.status).where(
                            t.utterances.c.id == old["utterance"]["id"]
                        )
                    )
                    == "SUPERSEDED"
                )
                assert set(
                    (
                        await conn.execute(select(t.plans.c.status).where(t.plans.c.sid == sid))
                    ).scalars()
                ) == {"SUPERSEDED", "EXECUTING", "UNKNOWN"}
                assert (
                    await conn.scalar(select(t.approvals.c.status).where(t.approvals.c.sid == sid))
                    == "SUPERSEDED"
                )
                assert (
                    await conn.scalar(
                        select(t.authorization_claims.c.status).where(
                            t.authorization_claims.c.scope_id == old["utterance"]["id"]
                        )
                    )
                    == "CLAIMED"
                )

    asyncio.run(run())


@pytest.mark.parametrize(
    "text",
    [
        "看上月消费和异常",
        "查询之前的转账",
        "不对吗",
        "“改成”是什么意思",
        "改成是什么意思",
        "“不对。改成给赵六九百”",
        "查看备注“不对；改为给赵六九百”",
    ],
)
def test_r7_read_queries_do_not_revoke_existing_write(gateway_db, monkeypatch, text):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, public, _, _):
            token = await login(client)
            sid = await create(client, token, new_id("req"))
            old = (await utter(client, token, sid, "给王五800")).json()
            response = await utter(client, token, sid, text, "req_r7_read")
            assert response.status_code == 201, response.text
            caller = caller_for(old, public)
            async with db.connect() as conn:
                assert (
                    await load_for_plan(conn, sid, list(caller.utts), caller)
                ).scope_ids == caller.utts

    asyncio.run(run())


def test_r7_correction_still_supersedes_when_l3_is_unavailable(gateway_db, monkeypatch):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, public, _, fake):
            token = await login(client)
            sid = await create(client, token, new_id("req"))
            old = (await utter(client, token, sid, "给王五800")).json()
            fake.behaviors["snapshot"] = "timeout"
            response = await utter(client, token, sid, "不对，改成给赵六九百", "req_r7_outage")
            assert response.status_code == 201, response.text
            assert response.json()["utterance"]["parse_status"] == "INTENT_UNCLEAR"
            await cannot_load(db, sid, old, public)

    asyncio.run(run())


def test_r6_form_required_accepts_only_complete_form_replacement(gateway_db, monkeypatch):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, public, _, _):
            token = await login(client)
            sid = await create(client, token, new_id("req"))
            parent = (await utter(client, token, sid, "明天给王五800")).json()
            action = await client.post(
                f"/sessions/{sid}/actions",
                headers=headers(token),
                json={
                    "request_id": "req_r6_complete",
                    "kind": "FORM",
                    "action_kind": "SCHEDULE_CREATE",
                    "fields": {
                        "from_account_id": "acc_zs",
                        "payee_id": "pye_ww",
                        "to_account_id": None,
                        "amount_cents": 80000,
                        "period": "ONCE",
                        "first_run_at": "2026-10-16T09:00:00+08:00",
                        "end_at": None,
                    },
                },
            )
            assert action.status_code == 200, action.text
            response = await client.post(
                f"/sessions/{sid}/utterances",
                headers=headers(token),
                json={
                    "request_id": "req_r6_complete_adopt",
                    "kind": "action",
                    "action_id": action.json()["action_id"],
                },
            )
            assert response.status_code == 201, response.text
            registered = response.json()
            assert registered["utterance"]["parse_status"] == "SCOPED"
            assert (
                registered["utterance"]["scope"]["fields"]["first_run_at"]
                == "2026-10-16T09:00:00+08:00"
            )
            caller = caller_for(registered, public)
            async with db.connect() as conn:
                assert (
                    await load_for_plan(conn, sid, list(caller.utts), caller)
                ).utterance_ids == caller.utts
            await cannot_load(db, sid, parent, public)

    asyncio.run(run())
