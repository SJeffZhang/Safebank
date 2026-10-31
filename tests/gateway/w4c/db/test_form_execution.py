"""Actual G19/G4/G8 FORM flow against real temporary bank and gateway PG."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from app import models as m
from sqlalchemy import func, select
from sqlalchemy.pool import NullPool

from services.gateway.app import locks
from services.gateway.app import tables as t
from services.gateway.app.db import engine
from tests.gateway.w3a.db.test_real_preflight import _serve_l3
from tests.gateway.w3b.db.conftest import SERVICE_TOKEN, _seed_auth, _seed_session, build_app
from tests.gateway.w4c.db.support import _anchor_to_real_time, _use_real_clock


@asynccontextmanager
async def form_runtime(app_config, obligations_db):
    app = await build_app(app_config)
    fast = app.client._transport.app
    admin, runtime, l3 = await _serve_l3(obligations_db)
    fast.state.l3 = l3
    locks.configure(fast.state.db, max_executions=4)
    _use_real_clock(fast)
    db = engine(app.db_url, poolclass=NullPool)
    async with db.begin() as conn:
        await _seed_session(conn, sid="ses_direct_form", customer="cus_zs")
        await _seed_auth(conn, jti="jti-direct-form", customer="cus_zs")
        await _anchor_to_real_time(conn, sid="ses_direct_form")
    h = {
        "X-Service-Token": SERVICE_TOKEN,
        "Authorization": "Bearer " + app.app_jwt(customer_id="cus_zs", jti="jti-direct-form"),
    }
    try:
        yield app, db, admin, h
    finally:
        await db.dispose()
        await runtime.dispose()
        await admin.dispose()


@pytest.mark.parametrize("mode", ["normal", "lost_reply", "fence"])
def test_form_green_execution_consumes_bound_action_once(app_config, obligations_db, mode):
    async def run():
        async with form_runtime(app_config, obligations_db) as (app, db, admin, h):
            action = await app.client.post(
                "/sessions/ses_direct_form/actions",
                json={
                    "request_id": "req_form_action",
                    "kind": "FORM",
                    "action_kind": "CARD_FREEZE",
                    "fields": {"card_id": "crd_zs_1"},
                },
                headers=h,
            )
            assert action.status_code == 200, action.text
            act = action.json()["action_id"]
            ev = await app.client.post(
                "/plans/evaluate",
                json={
                    "request_id": "req_form_eval",
                    "session_id": "ses_direct_form",
                    "form_action_ids": [act],
                },
                headers=h,
            )
            assert ev.status_code == 200, ev.text
            result = ev.json()
            assert result["decision"] == "ALLOW", result
            pid = result["plan_id"]
            url = f"/plans/{pid}/execute"
            body = {"request_id": "req_form_execute"}
            l3 = app.client._transport.app.state.l3
            original_execute = l3.execute
            if mode != "normal":
                from sqlalchemy import text

                from services.gateway.app.l3.client import L3OutcomeUnknown

                async def interrupted(payload):
                    if mode == "fence":
                        async with admin.begin() as conn:
                            await conn.execute(
                                text(
                                    "UPDATE state_versions SET version=version+1 "
                                    "WHERE customer_id='cus_zs'"
                                )
                            )
                    value = await original_execute(payload)
                    if mode == "lost_reply":
                        raise L3OutcomeUnknown("reply lost")
                    return value

                l3.execute = interrupted
                first = await app.client.post(url, json=body, headers=h)
                assert first.status_code == (202 if mode == "lost_reply" else 409), first.text
                async with db.connect() as conn:
                    assert (
                        await conn.scalar(
                            select(t.form_actions.c.used_by).where(t.form_actions.c.id == act)
                        )
                        == pid
                    )
                    assert (
                        await conn.scalar(
                            select(t.authorization_claims.c.status).where(
                                t.authorization_claims.c.scope_id == act
                            )
                        )
                        == "CLAIMED"
                    )
                l3.execute = original_execute
                body = {"request_id": "req_form_resume"}
            response = await app.client.post(url, json=body, headers=h)
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "EXECUTED"
            repeated = await app.client.post(url, json=body, headers=h)
            assert repeated.status_code == 200, repeated.text
            assert {k: v for k, v in repeated.json().items() if k != "replayed"} == {
                k: v for k, v in response.json().items() if k != "replayed"
            }, repeated.text
            async with admin.connect() as conn:
                assert (
                    await conn.scalar(select(m.cards.c.status).where(m.cards.c.id == "crd_zs_1"))
                    == "FROZEN"
                )
                assert (
                    await conn.scalar(
                        select(func.count())
                        .select_from(m.plan_executions)
                        .where(m.plan_executions.c.plan_id == pid)
                    )
                    == 1
                )
            async with db.connect() as conn:
                assert (
                    await conn.scalar(
                        select(t.form_actions.c.used_by).where(t.form_actions.c.id == act)
                    )
                    == pid
                )
                assert (
                    await conn.scalar(
                        select(t.authorization_claims.c.status).where(
                            t.authorization_claims.c.scope_id == act
                        )
                    )
                    == "CONSUMED"
                )
                assert not (
                    await conn.execute(select(t.utterances).where(t.utterances.c.id == act))
                ).first()

    asyncio.run(run())


async def confirm_form(app, db, h, result, *, otp_required):
    from services.gateway.app.approval.otp import decrypt_otp_code, derive_otp_keys
    from tests.gateway.w3b.db.conftest import OTP_KEY

    aid = result["approval_id"]
    panel_response = await app.client.get(f"/approvals/{aid}", headers=h)
    assert panel_response.status_code == 200, panel_response.text
    panel = panel_response.json()
    assert panel["otp_required"] is otp_required
    body = {
        "request_id": "req_direct_confirm",
        "view_hash": panel["view_hash"],
        "risk_ack": panel["risk_ack_required"],
    }
    if otp_required:
        missing = await app.client.post(f"/approvals/{aid}/confirm", json=body, headers=h)
        assert missing.status_code in (409, 422), missing.text
        sent = await app.client.post(
            f"/approvals/{aid}/otp",
            json={"request_id": "req_direct_otp", "view_hash": panel["view_hash"]},
            headers=h,
        )
        assert sent.status_code == 200, sent.text
        async with db.connect() as conn:
            row = (
                (
                    await conn.execute(
                        select(t.otp_challenges).where(t.otp_challenges.c.approval_id == aid)
                    )
                )
                .mappings()
                .one()
            )
        code = decrypt_otp_code(
            row["code_ciphertext"],
            approval_id=aid,
            plan_id=result["plan_id"],
            view_hash=panel["view_hash"],
            wall_now=row["last_sent_at_wall"],
            key=derive_otp_keys(OTP_KEY)[1],
        )
        assert code is not None
        body["otp"] = code
    confirmation = await app.client.post(f"/approvals/{aid}/confirm", json=body, headers=h)
    assert confirmation.status_code == 200, confirmation.text


@pytest.mark.parametrize(
    "kind,fields,risk",
    [
        (
            "TRANSFER_EXTERNAL",
            {
                "from_account_id": "acc_zs_chk",
                "payee_id": "pye_zs_zl",
                "to_account_id": None,
                "amount_cents": 20000,
                "memo": None,
            },
            "YELLOW",
        ),
        ("CARD_LOST", {"card_id": "crd_zs_1", "replace": False}, "RED"),
    ],
)
def test_form_approval_and_real_execution(app_config, obligations_db, kind, fields, risk):
    from services.gateway.app.auth.tokens import APP_AUDIENCE, sign_jwt

    async def run():
        async with form_runtime(app_config, obligations_db) as (app, db, admin, h):
            if kind == "TRANSFER_EXTERNAL":
                from sqlalchemy import text

                async with admin.begin() as conn:
                    await conn.execute(
                        text("UPDATE payees SET created_at=created_at-interval '1 day'")
                    )
            h["Authorization"] = "Bearer " + sign_jwt(
                app.private_key_pem,
                sub="cus_zs",
                aud=APP_AUDIENCE,
                scope=["app", "approve"],
                jti="jti-direct-form",
            )
            action = await app.client.post(
                "/sessions/ses_direct_form/actions",
                json={
                    "request_id": "req_form_action",
                    "kind": "FORM",
                    "action_kind": kind,
                    "fields": fields,
                },
                headers=h,
            )
            assert action.status_code == 200, action.text
            act = action.json()["action_id"]
            ev = await app.client.post(
                "/plans/evaluate",
                json={
                    "request_id": "req_form_eval",
                    "session_id": "ses_direct_form",
                    "form_action_ids": [act],
                },
                headers=h,
            )
            assert ev.status_code == 200, ev.text
            result = ev.json()
            assert result["decision"] == "REQUIRE_APPROVAL" and result["risk"] == risk, result
            await confirm_form(app, db, h, result, otp_required=risk == "RED")
            pid = result["plan_id"]
            url = f"/plans/{pid}/execute"
            response = await app.client.post(url, json={"request_id": "req_direct_exec"}, headers=h)
            assert response.status_code == 200, response.text
            assert response.json()["status"] == "EXECUTED", response.text
            repeated = await app.client.post(url, json={"request_id": "req_direct_exec"}, headers=h)
            assert repeated.status_code == 200 and repeated.json() == response.json(), repeated.text
            async with admin.connect() as conn:
                assert (
                    await conn.scalar(
                        select(func.count())
                        .select_from(m.plan_executions)
                        .where(m.plan_executions.c.plan_id == pid)
                    )
                    == 1
                )
                if kind == "CARD_LOST":
                    assert (
                        await conn.scalar(
                            select(m.cards.c.status).where(m.cards.c.id == "crd_zs_1")
                        )
                        == "LOST"
                    )
                else:
                    assert (
                        await conn.scalar(
                            select(func.sum(m.transfers.c.amount_cents)).where(
                                m.transfers.c.plan_id == pid
                            )
                        )
                        == 20000
                    )
            async with db.connect() as conn:
                assert (
                    await conn.scalar(
                        select(t.form_actions.c.used_by).where(t.form_actions.c.id == act)
                    )
                    == pid
                )
                assert (
                    await conn.scalar(
                        select(t.authorization_claims.c.status).where(
                            t.authorization_claims.c.scope_id == act
                        )
                    )
                    == "CONSUMED"
                )

    asyncio.run(run())
