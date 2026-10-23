"""Real W1 G3/G15/G19 → load_for_plan → W2 stages ②③④ end to end.

These tests exercise the production identity path: PIN login, session creation,
utterance/action registration, a genuine assistant JWT, ``load_for_plan`` and the
persistent source proof, not a cross-directory substitute. L3 stays a contract
fake because P1 implements only a 5/18 read and 7/14 write backend subset, so
these source-lineage tests do not claim full L3 integration.
"""

import asyncio
from datetime import timedelta

import pytest
from safebank_contracts.codes import Code
from safebank_contracts.plan import PlanDraft
from sqlalchemy import select, update

from services.gateway.app import tables as t
from services.gateway.app.auth.tokens import ASSISTANT_AUDIENCE, verify_jwt
from services.gateway.app.errors import GatewayError
from services.gateway.app.l3.client import L3Client
from services.gateway.app.pipeline.context import EvalContext
from services.gateway.app.pipeline.entity import EntityStage
from services.gateway.app.pipeline.intent import IntentStage
from services.gateway.app.pipeline.provenance import ProvenanceStage
from services.gateway.app.schemas.enums import ServiceKind
from services.gateway.app.schemas.identity import Caller
from services.gateway.app.utterance.loader import load_for_plan
from services.gateway.app.utterance.service import new_id
from tests.contracts.samples import step
from tests.gateway.w1.db.support import create, harness, headers, login, utter
from tests.gateway.w2.test_provenance import source


def l3_client(fake):
    return L3Client("http://bank", fake.token, transport=fake.transport())


def caller_from(public, assistant_token):
    claims = verify_jwt(assistant_token, public, expected_aud=ASSISTANT_AUDIENCE)
    return Caller(
        kind=ServiceKind.A,
        customer_id=claims.sub,
        sid=claims.sid,
        jti=claims.jti,
        scopes=frozenset(claims.scope),
        utts=claims.utts,
    )


async def authorization_for(db, public, assistant_token, sid):
    caller = caller_from(public, assistant_token)
    async with db.connect() as conn:
        return caller, await load_for_plan(conn, sid, list(caller.utts), caller)


def transfer_step(amount_cents, *, payee_id="pye_ww", sources):
    s = step(from_account_id="acc_zs", payee_id=payee_id, amount_cents=amount_cents)
    s["arg_sources"] = sources
    return s


async def run_stages(db, fake, clock, auth, caller, sid, steps, *, origin="agent"):
    body = {
        "request_id": "req_plan",
        "session_id": sid,
        "utterance_ids": list(auth.utterance_ids),
        "intents": [],
        "rationale": "",
        "steps": steps,
    }
    ctx = EvalContext(
        plan_draft=PlanDraft.model_validate(body),
        identity=caller,
        authorization=auth,
        plan_id="pln_" + sid,
        origin=origin,
        customer_id=caller.customer_id,
        session_id=sid,
        l3=l3_client(fake),
    )
    ctx.wall_now = lambda: clock[0]
    async with db.connect() as conn:
        ctx.conn = conn
        await EntityStage().run(ctx)
        await ProvenanceStage().run(ctx)
        await IntentStage().run(ctx)
    return ctx


def test_typed_transfer_flows_real_g3_to_load_for_plan_to_stages(gateway_db, monkeypatch):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, public, clock, fake):
            token = await login(client)
            sid = await create(client, token, "req_typed_create")
            body = (await utter(client, token, sid, "给王五800", "req_typed_1")).json()
            utterance = body["utterance"]
            assert utterance["parse_status"] == "SCOPED"
            assert utterance["scope"]["transfers"][0]["amount_cents"] == 80000
            caller, auth = await authorization_for(db, public, body["assistant_token"], sid)
            assert auth.scope_ids == auth.utterance_ids == caller.utts
            step_ = transfer_step(
                80000,
                sources=[
                    source("payee_id", ref=utterance["id"], span=[1, 3]),
                    source("amount_cents", ref=utterance["id"], span=[3, 6]),
                ],
            )
            ctx = await run_stages(db, fake, clock, auth, caller, sid, [step_])
            assert ctx.provenance.arg_sources["s1"]["amount_cents"]["trust"] == "user"
            assert ctx.provenance.arg_sources["s1"]["payee_id"]["trust"] == "user"
            assert ctx.intent.matched is True
            assert any(
                s.tool == "transfer" and s.action_kind == "TRANSFER_EXTERNAL" for s in ctx.steps
            )

    asyncio.run(run())


def test_clarification_action_slot_merges_and_both_sources_resolve(gateway_db, monkeypatch):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, public, clock, fake):
            token = await login(client)
            sid = await create(client, token, "req_clr_merge_create")
            parent = (await utter(client, token, sid, "给王吾800", "req_clr_parent")).json()
            assert parent["utterance"]["parse_status"] == "CLARIFYING"
            action = await client.post(
                f"/sessions/{sid}/actions",
                headers=headers(token),
                json={
                    "request_id": "req_clr_action",
                    "kind": "CLARIFICATION",
                    "action_kind": None,
                    "parent_utterance_id": parent["utterance"]["id"],
                    "fields": [{"field": "payee_id", "entity_id": "pye_ww"}],
                },
            )
            assert action.status_code == 200, action.text
            act_id = action.json()["action_id"]
            merged = (
                await utter(
                    client,
                    token,
                    sid,
                    None,
                    "req_clr_accept",
                    kind="action",
                    action_id=act_id,
                )
            ).json()
            root = merged["utterance"]
            assert root["scope"]["transfers"][0]["target_fingerprint"] == "fp_ww"
            assert root["parent_ids"] == [parent["utterance"]["id"]]
            caller, auth = await authorization_for(db, public, merged["assistant_token"], sid)
            assert list(auth.utterance_ids) == [root["id"]]
            # Root is the sole claim; the action id and parent are evidence, not claims.
            assert list(auth.scope_ids) == [root["id"]]
            assert len(auth.utterances) == 1
            step_ = transfer_step(
                80000,
                sources=[
                    source("payee_id", "action", ref=act_id),
                    source("amount_cents", ref=parent["utterance"]["id"], span=[3, 6]),
                ],
            )
            ctx = await run_stages(db, fake, clock, auth, caller, sid, [step_])
            assert ctx.provenance.arg_sources["s1"]["amount_cents"]["trust"] == "user"
            assert ctx.provenance.arg_sources["s1"]["payee_id"]["trust"] == "user"
            assert ctx.intent.matched is True

    asyncio.run(run())


def test_adopted_hint_used_by_resolves_and_unadopted_is_rejected(gateway_db, monkeypatch):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, public, clock, fake):
            token = await login(client)
            sid = await create(client, token, "req_hint_create")
            typed = (await utter(client, token, sid, "给王五800", "req_hint_typed")).json()
            scope = {
                **typed["utterance"]["scope"],
                "transfers": [
                    {**typed["utterance"]["scope"]["transfers"][0], "amount_cents": 30000}
                ],
            }
            hint_id = new_id("hnt")
            async with db.begin() as conn:
                await conn.execute(
                    t.hints.insert().values(
                        id=hint_id,
                        sid=sid,
                        scope_json=scope,
                        label="王五只转300元",
                        created_at_wall=clock[0],
                        expires_at_wall=clock[0] + timedelta(minutes=5),
                    )
                )
            adopted = (
                await utter(
                    client,
                    token,
                    sid,
                    None,
                    "req_hint_adopt",
                    kind="hint",
                    hint_id=hint_id,
                )
            ).json()
            assert adopted["utterance"]["scope"]["transfers"][0]["amount_cents"] == 30000
            caller, auth = await authorization_for(db, public, adopted["assistant_token"], sid)
            step_ = transfer_step(
                30000,
                sources=[source("amount_cents", "hint", ref=hint_id)],
            )
            ctx = await run_stages(db, fake, clock, auth, caller, sid, [step_])
            assert ctx.provenance.arg_sources["s1"]["amount_cents"]["trust"] == "user"
            assert ctx.intent.matched is True

            # A different, never-adopted hint cannot supply the amount.
            stray = new_id("hnt")
            async with db.begin() as conn:
                await conn.execute(
                    t.hints.insert().values(
                        id=stray,
                        sid=sid,
                        scope_json=scope,
                        label="未采纳提示",
                        created_at_wall=clock[0],
                        expires_at_wall=clock[0] + timedelta(minutes=5),
                    )
                )
            step_ = transfer_step(
                30000,
                sources=[source("amount_cents", "hint", ref=stray)],
            )
            ctx = await run_stages(db, fake, clock, auth, caller, sid, [step_])
            assert ctx.provenance.arg_sources["s1"]["amount_cents"]["trust"] == "unknown"
            assert any(f.code is Code.UNKNOWN_SENSITIVE_ARG for f in ctx.findings)

    asyncio.run(run())


def test_wrong_used_by_hint_is_not_accepted(gateway_db, monkeypatch):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, public, clock, fake):
            token = await login(client)
            sid = await create(client, token, "req_usedby_create")
            typed = (await utter(client, token, sid, "给王五800", "req_usedby_typed")).json()
            scope = typed["utterance"]["scope"]
            hint_id = new_id("hnt")
            async with db.begin() as conn:
                await conn.execute(
                    t.hints.insert().values(
                        id=hint_id,
                        sid=sid,
                        scope_json=scope,
                        label="转账800元",
                        used_by="utt_unrelated",
                        created_at_wall=clock[0],
                        expires_at_wall=clock[0] + timedelta(minutes=5),
                    )
                )
            caller, auth = await authorization_for(db, public, typed["assistant_token"], sid)
            step_ = transfer_step(
                80000,
                sources=[source("amount_cents", "hint", ref=hint_id)],
            )
            ctx = await run_stages(db, fake, clock, auth, caller, sid, [step_])
            assert ctx.provenance.arg_sources["s1"]["amount_cents"]["trust"] == "unknown"

    asyncio.run(run())


def test_cross_sid_and_cross_customer_authorization_is_rejected(gateway_db, monkeypatch):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, public, _, _):
            token = await login(client)
            sid = await create(client, token, "req_cross_create")
            typed = (await utter(client, token, sid, "给王五800", "req_cross_1")).json()
            caller = caller_from(public, typed["assistant_token"])
            other_token = await login(client, "cus_ls")
            other_sid = await create(client, other_token, "req_cross_other")

            # Same assistant token against a different session is not allowed.
            async with db.connect() as conn:
                with pytest.raises(GatewayError) as exc:
                    await load_for_plan(conn, other_sid, list(caller.utts), caller)
                assert exc.value.status == 404

            # Another customer's assistant token cannot load this customer's session.
            other_utter = (
                await utter(client, other_token, other_sid, "你好", "req_cross_other_utter")
            ).json()
            other_caller = caller_from(public, other_utter["assistant_token"])
            async with db.connect() as conn:
                with pytest.raises(GatewayError) as exc:
                    await load_for_plan(conn, sid, list(other_caller.utts), other_caller)
                assert exc.value.status == 404

    asyncio.run(run())


def test_correction_supersedes_old_utts(gateway_db, monkeypatch):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, public, _, _):
            token = await login(client)
            sid = await create(client, token, "req_correction_create")
            old = (await utter(client, token, sid, "给王五800", "req_corr_old")).json()
            caller = caller_from(public, old["assistant_token"])
            changed = await utter(client, token, sid, "不对，改成给赵六600", "req_corr_new")
            assert changed.status_code == 201, changed.text
            async with db.connect() as conn:
                with pytest.raises(GatewayError) as exc:
                    await load_for_plan(conn, sid, list(caller.utts), caller)
                assert exc.value.code is Code.AUTHORIZATION_CONSUMED
            row = None
            async with db.connect() as conn:
                row = (
                    await conn.execute(
                        select(t.utterances.c.status).where(
                            t.utterances.c.id == old["utterance"]["id"]
                        )
                    )
                ).scalar()
            assert row == "SUPERSEDED"

    asyncio.run(run())


def test_logout_revokes_read_and_plan_loading(gateway_db, monkeypatch):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, public, _, _):
            token = await login(client)
            sid = await create(client, token, "req_logout_create")
            typed = (await utter(client, token, sid, "给王五800", "req_logout_1")).json()
            caller = caller_from(public, typed["assistant_token"])
            # Logout revokes the login and every session; loading the plan then fails.
            logout = await client.post(
                "/auth/logout", headers=headers(token), json={"request_id": "req_logout"}
            )
            assert logout.status_code == 200, logout.text
            async with db.connect() as conn:
                with pytest.raises(GatewayError) as exc:
                    await load_for_plan(conn, sid, list(caller.utts), caller)
                assert exc.value.status == 401
                assert exc.value.code is Code.UNAUTHENTICATED

    asyncio.run(run())


def test_expired_parent_invalidates_source_proof(gateway_db, monkeypatch):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, db, _, public, clock, fake):
            token = await login(client)
            sid = await create(client, token, "req_parent_create")
            parent = (await utter(client, token, sid, "给王吾800", "req_parent_1")).json()
            action = await client.post(
                f"/sessions/{sid}/actions",
                headers=headers(token),
                json={
                    "request_id": "req_parent_action",
                    "kind": "CLARIFICATION",
                    "action_kind": None,
                    "parent_utterance_id": parent["utterance"]["id"],
                    "fields": [{"field": "payee_id", "entity_id": "pye_ww"}],
                },
            )
            merged = (
                await utter(
                    client,
                    token,
                    sid,
                    None,
                    "req_parent_accept",
                    kind="action",
                    action_id=action.json()["action_id"],
                )
            ).json()
            # Expire the parent only; the child must stop trusting its text span.
            async with db.begin() as conn:
                await conn.execute(
                    update(t.utterances)
                    .where(t.utterances.c.id == parent["utterance"]["id"])
                    .values(expires_at_wall=clock[0] - timedelta(seconds=1))
                )
            caller, auth = await authorization_for(db, public, merged["assistant_token"], sid)
            step_ = transfer_step(
                80000,
                sources=[
                    source("payee_id", "action", ref=action.json()["action_id"]),
                    source("amount_cents", ref=parent["utterance"]["id"], span=[3, 6]),
                ],
            )
            ctx = await run_stages(db, fake, clock, auth, caller, sid, [step_])
            assert ctx.provenance.arg_sources["s1"]["amount_cents"]["trust"] == "unknown"
            assert ctx.intent.matched is False

    asyncio.run(run())
