import asyncio

from sqlalchemy import select

from services.gateway.app import tables as t
from services.gateway.app.auth.tokens import ASSISTANT_AUDIENCE, verify_jwt
from tests.gateway.w1.db.support import create, harness, headers, login, utter


def test_g2_real_auth_persists_scoped_ledger_and_logout_revokes(gateway_db, monkeypatch):
    """G2 reads through the real W1 runtime: PIN login, assistant JWT, real check_revocation.

    No ``check_revocation`` substitute is installed; the login/session rows must be
    genuinely persisted for the read to pass, and logout must genuinely revoke them.
    """

    async def run():
        async with harness(gateway_db, monkeypatch) as (app, client, db, _, public, _, _):
            token = await login(client)
            sid = await create(client, token, "req_g2_create")
            utterance = await utter(client, token, sid, "给王五800", "req_g2_utter")
            assert utterance.status_code == 201, utterance.text
            assistant = utterance.json()["assistant_token"]
            claims = verify_jwt(assistant, public, expected_aud=ASSISTANT_AUDIENCE)
            assert claims.sid == sid and claims.sub == "cus_zs"
            assistant_headers = headers(assistant, service="a-test")

            # Real G2 read with a real assistant JWT: 200 + persisted ledger row.
            response = await client.post(
                "/tools/read/get_accounts",
                headers=assistant_headers,
                json={"session_id": sid, "args": {"history_days": 0}},
            )
            assert response.status_code == 200, response.text
            data = response.json()
            async with db.connect() as conn:
                row = (
                    (
                        await conn.execute(
                            select(t.read_records).where(t.read_records.c.id == data["source_ref"])
                        )
                    )
                    .mappings()
                    .one()
                )
                assert row["sid"] == sid
                assert row["versions"] == data["versions"]
                assert row["canonical_args"] == {"history_days": 0}

            # The assistant token is bound to its own session; a foreign session is 404.
            other_token = await login(client, "cus_ls")
            other_sid = await create(client, other_token, "req_g2_other_create")
            foreign = await client.post(
                "/tools/read/get_accounts",
                headers=assistant_headers,
                json={"session_id": other_sid, "args": {"history_days": 0}},
            )
            assert foreign.status_code == 404, foreign.text

            # An assistant token cannot mint a cross-customer read argument set.
            bad_args = await client.post(
                "/tools/read/get_accounts",
                headers=assistant_headers,
                json={"session_id": sid, "args": {"history_days": 0, "customer_id": "cus_ls"}},
            )
            assert bad_args.status_code == 422, bad_args.text

            # Logout revokes the login and its sessions; the same read now 401s via
            # the real check_revocation path rather than silently succeeding.
            logout = await client.post(
                "/auth/logout", headers=headers(token), json={"request_id": "req_g2_logout"}
            )
            assert logout.status_code == 200, logout.text
            revoked = await client.post(
                "/tools/read/get_accounts",
                headers=assistant_headers,
                json={"session_id": sid, "args": {"history_days": 0}},
            )
            assert revoked.status_code == 401, revoked.text
            assert revoked.json()["error"]["code"] == "UNAUTHENTICATED"

    asyncio.run(run())
