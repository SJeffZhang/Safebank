import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import insert, update
from sqlalchemy.pool import NullPool

from services.gateway.app.db import engine
from services.gateway.app.pipeline.provenance import ProvenanceStage
from services.gateway.app.schemas.scope import ScopeTransfer
from services.gateway.app.tables import form_actions, hints, read_records, sessions
from services.gateway.app.taint.records import load_records
from tests.bankcore.fixtures import NOW
from tests.contracts.samples import step
from tests.gateway.w2.test_contract_entity import context
from tests.gateway.w2.test_provenance import authorized, source


def test_action_and_hint_are_server_values_scoped_unexpired_and_not_reusable(gateway_db):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        now = datetime.now(UTC)
        s = step(amount_cents=10000)
        ctx = authorized(
            context([s]),
            transfers=(ScopeTransfer(target_fingerprint="pye_bfp", amount_cents=10000),),
        )
        ctx.session_id = "sid_evidence_w2"
        ctx.authorization = ctx.authorization.model_copy(
            update={"sid": ctx.session_id, "scope_ids": ("act_w2", "hnt_w2")}
        )
        async with db.begin() as conn:
            await conn.execute(
                insert(sessions).values(id=ctx.session_id, customer_id="cus_a", created_at_wall=now)
            )
            await conn.execute(
                insert(form_actions).values(
                    id="act_w2",
                    sid=ctx.session_id,
                    kind="FORM",
                    action_kind="TRANSFER_EXTERNAL",
                    normalized_fields={"amount_cents": 10000},
                    created_at_wall=now,
                    expires_at_wall=now + timedelta(minutes=10),
                )
            )
            await conn.execute(
                insert(hints).values(
                    id="hnt_w2",
                    sid=ctx.session_id,
                    scope_json=ctx.authorization.utterances["utt_1"].scope.model_dump(mode="json"),
                    label="转账100元",
                    created_at_wall=now,
                    expires_at_wall=now + timedelta(minutes=10),
                )
            )
        for kind, ref in (("action", "act_w2"), ("hint", "hnt_w2")):
            s["arg_sources"] = [source("amount_cents", kind, ref=ref)]
            from safebank_contracts.plan import PlanDraft

            from tests.gateway.w2.test_contract_entity import draft

            ctx.plan_draft = PlanDraft.model_validate(draft([s]))
            async with db.connect() as conn:
                ctx.conn = conn
                await ProvenanceStage().run(ctx)
            assert ctx.provenance.arg_sources["s1"]["amount_cents"]["trust"] == "user"
        async with db.begin() as conn:
            await conn.execute(
                update(hints).where(hints.c.id == "hnt_w2").values(used_by="pln_other")
            )
        async with db.connect() as conn:
            ctx.conn = conn
            await ProvenanceStage().run(ctx)
        assert ctx.provenance.arg_sources["s1"]["amount_cents"]["trust"] == "unknown"
        await db.dispose()

    asyncio.run(run())


def test_reset_revokes_session_and_blocks_identical_timestamp_record_replay(gateway_db):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        now = datetime.now(UTC)
        async with db.begin() as conn:
            await conn.execute(
                insert(sessions).values(id="sid_reset_w2", customer_id="cus_a", created_at_wall=now)
            )
            await conn.execute(
                insert(read_records).values(
                    id="src_reset_w2",
                    sid="sid_reset_w2",
                    tool="get_accounts",
                    canonical_args={"history_days": 0},
                    response={"accounts": [{"id": "acc_a"}]},
                    untrusted_paths=[],
                    versions={"cus_a": 0},
                    observed_at=NOW,
                    created_at_wall=now,
                )
            )
        async with db.connect() as conn:
            assert "src_reset_w2" in await load_records(conn, "sid_reset_w2", "cus_a")
        async with db.begin() as conn:
            await conn.execute(
                update(sessions).where(sessions.c.id == "sid_reset_w2").values(revoked_at_wall=now)
            )
        async with db.connect() as conn:
            assert await load_records(conn, "sid_reset_w2", "cus_a") == {}
        # W5's reset must revoke the session before a repeated sim_now is usable.
        await db.dispose()

    asyncio.run(run())
