import asyncio
from datetime import UTC, datetime

import httpx
from sqlalchemy import insert, select
from sqlalchemy.pool import NullPool

from services.gateway.app.auth.tokens import ASSISTANT_AUDIENCE, sign_jwt
from services.gateway.app.config import from_env
from services.gateway.app.db import engine
from services.gateway.app.l3.client import L3Client
from services.gateway.app.l3.fake import FakeL3
from services.gateway.app.main import build_public_app
from services.gateway.app.pipeline.entity import EntityStage
from services.gateway.app.tables import read_records, sessions
from tests.bankcore.test_banking import banking_state
from tests.contracts.samples import step
from tests.gateway.w2.test_contract_entity import context


def test_g2_authenticated_http_persists_scoped_ledger_and_entity_consumes(
    gateway_db, keys, monkeypatch
):
    from services.gateway.app.auth import tokens

    async def fixture_revocation(conn, caller, *, now=None):
        # W1 has not merged: this fixture isolates G2 HTTP/SQL persistence only.
        pass

    monkeypatch.setattr(tokens, "check_revocation", fixture_revocation, raising=False)

    async def run():
        private, public = keys
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        fake = FakeL3()
        fake.set_snapshot(banking_state())
        l3 = L3Client("http://l3", fake.token, transport=fake.transport())
        env = {"AGENT_SERVICE_TOKEN": "a-token", "FRONTEND_SERVICE_TOKEN": "f-token"}
        app = build_public_app(from_env(env), env=env, public_key=public)
        app.state.db, app.state.l3 = db, l3
        app.state.wall_now = lambda: datetime.now(UTC)
        token = sign_jwt(
            private,
            sub="cus_a",
            aud=ASSISTANT_AUDIENCE,
            scope=["assistant"],
            sid="sid_w2",
            utts=["utt_1"],
            jti="jti_w2",
        )
        headers = {"X-Service-Token": "a-token", "Authorization": "Bearer " + token}
        try:
            async with db.begin() as conn:
                await conn.execute(
                    insert(sessions),
                    [
                        {
                            "id": "sid_w2",
                            "customer_id": "cus_a",
                            "created_at_wall": datetime.now(UTC),
                        },
                        {
                            "id": "sid_foreign_w2",
                            "customer_id": "cus_b",
                            "created_at_wall": datetime.now(UTC),
                        },
                    ],
                )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://gw"
            ) as client:
                response = await client.post(
                    "/tools/read/get_accounts",
                    headers=headers,
                    json={"session_id": "sid_w2", "args": {"history_days": 0}},
                )
                assert response.status_code == 200, response.text
                data = response.json()
                async with db.connect() as conn:
                    row = (
                        (
                            await conn.execute(
                                select(read_records).where(read_records.c.id == data["source_ref"])
                            )
                        )
                        .mappings()
                        .one()
                    )
                    assert row["sid"] == "sid_w2"
                    assert row["versions"] == data["versions"]
                    assert row["canonical_args"] == {"history_days": 0}
                before = len(fake.calls)
                for sid, args in [
                    ("sid_foreign_w2", {"history_days": 0}),
                    ("sid_w2", {"history_days": 0, "customer_id": "cus_b"}),
                ]:
                    r = await client.post(
                        "/tools/read/get_accounts",
                        headers=headers,
                        json={"session_id": sid, "args": args},
                    )
                    assert r.status_code in (404, 422)
                assert len(fake.calls) == before
                r = await client.post(
                    "/tools/read/get_cards",
                    headers=headers,
                    json={"session_id": "sid_w2", "args": {"account_id": "acc_b"}},
                )
                assert r.status_code == 404
            # A genuine saved ledger proves own IDs; a different session cannot borrow it.
            async with db.begin() as conn:
                await conn.execute(
                    insert(read_records).values(
                        id="src_owned_w2",
                        sid="sid_w2",
                        tool="list_payees",
                        canonical_args={},
                        response={"items": [{"id": "pye_b"}]},
                        untrusted_paths=[],
                        versions={"cus_a": 0, "cus_b": 0},
                        observed_at=datetime.now(UTC),
                        created_at_wall=datetime.now(UTC),
                    )
                )
            ctx = context([step(payee_id="pye_b", amount_cents=100)])
            ctx.session_id = "sid_w2"
            async with db.connect() as conn:
                ctx.conn = conn
                await EntityStage().run(ctx)
            # FakeL3 intentionally returns empty accounts, so add exact account visibility
            # separately: positive/negative ledger behavior uses real persisted evidence.
            assert any(f.code.value == "ENTITY_UNSEEN" for f in ctx.findings)
            assert not any("list_payees" in f.evidence_refs for f in ctx.findings)
            ctx = context()
            ctx.session_id = "sid_foreign_w2"
            async with db.connect() as conn:
                ctx.conn = conn
                await EntityStage().run(ctx)
            assert any("list_payees" in f.evidence_refs for f in ctx.findings)
        finally:
            await l3.close()
            await db.dispose()

    asyncio.run(run())
