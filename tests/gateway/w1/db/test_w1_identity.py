import asyncio
from datetime import timedelta

from sqlalchemy import select

from services.gateway.app import tables as t
from services.gateway.app.auth.pin import HASHER, authenticate
from services.gateway.app.auth.tokens import APP_AUDIENCE, ASSISTANT_AUDIENCE, verify_jwt
from tests.gateway.w1.db.support import create, harness, headers, login, utter


def test_login_pin_lock_and_ip_window(gateway_db):
    from services.gateway.app.db import engine
    from tests.gateway.w1.fixtures import NOW

    async def run():
        db = engine(gateway_db.runtime_url)
        hashes = {"cus_pin": HASHER.hash("123456")}
        now = NOW
        for i in range(5):
            async with db.begin() as conn:
                code = await authenticate(conn, "cus_pin", "bad", "pin-test", now, hashes)
            assert code.value == ("UNAUTHENTICATED" if i < 4 else "RATE_LIMITED")
        async with db.begin() as conn:
            assert (
                await authenticate(conn, "cus_pin", "123456", "pin-test", now, hashes)
            ).value == "RATE_LIMITED"
        async with db.begin() as conn:
            assert (
                await authenticate(
                    conn, "cus_pin", "123456", "pin-test", now + timedelta(minutes=15), hashes
                )
                is None
            )
        for i in range(11):
            async with db.begin() as conn:
                code = await authenticate(conn, "cus_ip", "bad", "ip-test", now, {})
            assert code is not None
            if i == 10:
                assert code.value == "RATE_LIMITED"
        async with db.connect() as conn:
            row = (
                (await conn.execute(select(t.ip_ratelimit).where(t.ip_ratelimit.c.ip == "ip-test")))
                .mappings()
                .one()
            )
            assert row["count"] == 10
        await db.dispose()

    asyncio.run(run())


def test_auth_session_jwt_renew_and_logout(gateway_db, monkeypatch):
    async def run():
        async with harness(gateway_db, monkeypatch) as (_, client, database, _, public, clock, _):
            token = await login(client)
            claims = verify_jwt(token, public, expected_aud=APP_AUDIENCE)
            assert claims.scope == ("app", "approve")
            assert claims.exp - claims.nbf == 7200
            sid = await create(client, token, "req_auth_create")
            result = await utter(client, token, sid, "给王五800", "req_auth_utter")
            assert result.status_code == 201, result.text
            body = result.json()
            utterance = body["utterance"]
            assistant = verify_jwt(
                body["assistant_token"], public, expected_aud=ASSISTANT_AUDIENCE, require_sid=True
            )
            assert assistant.sid == sid and assistant.utts == (utterance["id"],)
            assert assistant.exp - assistant.nbf <= 600
            old_expiry = utterance["expires_at_wall"]
            renew = await client.post(
                f"/sessions/{sid}/assistant-token",
                headers=headers(token),
                json={"request_id": "req_auth_renew"},
            )
            assert renew.status_code == 200
            renewed = verify_jwt(
                renew.json()["assistant_token"], public, expected_aud=ASSISTANT_AUDIENCE
            )
            assert renewed.utts == ()
            async with database.connect() as conn:
                expiry = await conn.scalar(
                    select(t.utterances.c.expires_at_wall).where(
                        t.utterances.c.id == utterance["id"]
                    )
                )
                assert expiry.isoformat() == old_expiry
            out = await client.post(
                "/auth/logout", headers=headers(token), json={"request_id": "req_auth_out"}
            )
            assert out.status_code == 200, out.text
            denied = await client.get(f"/sessions/{sid}/utterances", headers=headers(token))
            assert denied.status_code == 401
            denied = await client.get(
                f"/sessions/{sid}/utterances", headers=headers(body["assistant_token"], "a-test")
            )
            assert denied.status_code == 401
            async with database.connect() as conn:
                row = (
                    (await conn.execute(select(t.sessions).where(t.sessions.c.id == sid)))
                    .mappings()
                    .one()
                )
                assert row["revoked_at_wall"] == clock[0]
                assert row["authorization_epoch"] == 1

    asyncio.run(run())


def test_session_ownership_idempotency_and_g22(gateway_db, monkeypatch):
    async def run():
        async with harness(gateway_db, monkeypatch) as (app, client, _, _, _, _, _):
            token = await login(client)
            other = await login(client, "cus_ls")
            sid = await create(client, token, "req_owner_create")
            assert await create(client, token, "req_owner_create") == sid
            for method, path, body in [
                ("GET", f"/sessions/{sid}/utterances", None),
                ("GET", f"/sessions/{sid}/clarifications/current", None),
                ("GET", f"/sessions/{sid}/hints/hnt_x", None),
                ("POST", f"/sessions/{sid}/assistant-token", {"request_id": "req_other_renew"}),
                (
                    "POST",
                    f"/sessions/{sid}/utterances",
                    {"request_id": "req_other_utt", "kind": "typed", "text": "给王五800"},
                ),
                (
                    "POST",
                    f"/sessions/{sid}/actions",
                    {
                        "request_id": "req_other_act",
                        "kind": "FORM",
                        "action_kind": "CARD_FREEZE",
                        "fields": {"card_id": "crd_zs"},
                    },
                ),
            ]:
                response = await client.request(method, path, headers=headers(other), json=body)
                assert response.status_code == 404, response.text
            response = await client.get("/auth/demo-customers", headers=headers())
            assert response.status_code == 200
            assert all(
                set(item) == {"customer_id", "display_name"} for item in response.json()["items"]
            )
            from services.gateway.app.config import from_env

            app.state.settings = from_env({})
            assert (await client.get("/auth/demo-customers", headers=headers())).status_code == 404

    asyncio.run(run())


def test_concurrent_pin_failures_lock_without_lost_counts(gateway_db):
    from services.gateway.app.db import engine
    from tests.gateway.w1.fixtures import NOW

    async def run():
        db = engine(gateway_db.runtime_url)
        hashes = {"cus_pin_race": HASHER.hash("123456")}

        async def attempt(index):
            async with db.begin() as conn:
                return await authenticate(conn, "cus_pin_race", "bad", f"race-{index}", NOW, hashes)

        result = await asyncio.gather(*(attempt(i) for i in range(8)))
        assert sum(code.value == "UNAUTHENTICATED" for code in result) == 4
        assert sum(code.value == "RATE_LIMITED" for code in result) == 4
        async with db.connect() as conn:
            row = (
                (
                    await conn.execute(
                        select(t.pin_failures).where(t.pin_failures.c.customer_id == "cus_pin_race")
                    )
                )
                .mappings()
                .one()
            )
            assert row["failed_count"] == 5
            assert row["locked_until_wall"] == NOW + timedelta(minutes=15)
        await db.dispose()

    asyncio.run(run())
