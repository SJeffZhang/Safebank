"""Real bank PG + real L3 snapshot HTTP → default ProvenanceStage ③ reserve.

Proves the production ``ProvenanceStage`` consumes the real ``projected_shortfall``
on a complete ``BankState.obligations`` snapshot (no fake reserve, no zero guess).
The gateway DB only supplies the read ledger; L3 is the real HTTP service.
"""

import asyncio
import socket
import threading
from datetime import UTC, datetime, timedelta

import httpx
import uvicorn
from app import models as m
from app.db import engine as bank_engine
from app.main import create_app
from safebank_contracts.enums import Risk
from safebank_contracts.plan import (
    Execute,
    InteractiveEvidence,
    PlanBody,
    PlanDraft,
    Prepare,
    SnapshotRequest,
)
from safebank_contracts.state import BankState
from sqlalchemy import insert, update
from sqlalchemy.pool import NullPool

from services.gateway.app import tables as gt
from services.gateway.app.db import engine as gateway_engine
from services.gateway.app.l3.client import L3Client
from services.gateway.app.pipeline.context import EvalContext
from services.gateway.app.pipeline.provenance import ProvenanceStage
from tests.contracts.samples import step
from tests.gateway.w2.test_provenance import source
from tests.obligations_data.fixtures import KEY, NOW, install_core, install_obligations

SERVICE_TOKEN = "obligations-test-token"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _serve(app, port):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server


def _transfer_steps(amount_cents):
    return PlanBody(
        intents=(),
        steps=[
            step(
                from_account_id="acc_zs_chk",
                payee_id="pye_zs_ww",
                to_account_id=None,
                amount_cents=amount_cents,
            )
        ],
        rationale="test",
    ).steps


def _evidence(plan_hash):
    return InteractiveEvidence(
        type="INTERACTIVE",
        origin="agent",
        scope_ids=("utt_1",),
        utterance_ids=("utt_1",),
        evaluation_id="evl_1",
        approved_evaluation_id=None,
        plan_hash=plan_hash,
        approval_id=None,
        confirmed_view_hash=None,
        final_view_hash=plan_hash,
        risk=Risk.GREEN,
        confirmed_at_wall=None,
        authorization_epoch=0,
    )


def subscribe_plan(amount_cents):
    body = {
        "request_id": "req_w2b",
        "session_id": "ses_w2b",
        "utterance_ids": ["utt_w2b"],
        "intents": [],
        "rationale": "",
        "steps": [
            {
                "step_id": "s1",
                "tool": "subscribe_product",
                "args": {
                    "account_id": "acc_zs_chk",
                    "product_id": "prd_x",
                    "amount_cents": amount_cents,
                },
                "depends_on": [],
                "arg_sources": [
                    source(
                        "amount_cents",
                        "derived",
                        template="available_less_7d_charges",
                        inputs=[{"kind": "read", "ref": "src_acct", "pointer": "/accounts/0/id"}],
                    )
                ],
            }
        ],
    }
    return PlanDraft.model_validate(body)


async def _transfer(client, amount_cents, *, plan_id, expected, clock_version):
    steps = _transfer_steps(amount_cents)
    plan_hash = "hash_" + plan_id
    evidence = _evidence(plan_hash)
    deadline = "2099-01-01T00:00:00Z"
    prepared = await client.prepare(
        Prepare(
            customer_id="cus_zs",
            plan_id=plan_id,
            attempt=1,
            plan_hash=plan_hash,
            steps=steps,
            deadline_wall=deadline,
            deadline_cap_wall=deadline,
            authorization_evidence=evidence,
        ).model_dump(mode="json")
    )
    assert prepared["status"] == "PREPARED", prepared
    executed = await client.execute(
        Execute(
            customer_id="cus_zs",
            plan_id=plan_id,
            attempt=1,
            plan_hash=plan_hash,
            steps=steps,
            expected_versions=expected,
            clock_version=clock_version,
            authorization_evidence=evidence,
        ).model_dump(mode="json")
    )
    assert executed["status"] == "EXECUTED", executed


def test_real_s3_snapshot_feeds_default_reserve(gateway_db, obligations_db):
    async def run():
        admin = bank_engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = bank_engine(obligations_db.runtime_url, poolclass=NullPool)
        try:
            async with admin.begin() as conn:
                await install_core(conn)
                await install_obligations(conn)

            app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
            port = free_port()
            _serve(app, port)
            base = f"http://127.0.0.1:{port}"
            l3 = L3Client(base, SERVICE_TOKEN)

            async with httpx.AsyncClient(base_url=base) as client:
                for _ in range(40):
                    try:
                        probe = await client.get(
                            "/readyz", headers={"X-Service-Token": SERVICE_TOKEN}, timeout=2.0
                        )
                        break
                    except httpx.TransportError:
                        await asyncio.sleep(0.2)
                else:
                    raise RuntimeError("uvicorn listener never became ready")
                assert probe.status_code == 200

            # Execute a 300000 transfer so available=300000 and reserve=4500.
            await _transfer(
                l3,
                300000,
                plan_id="pln_w2b_1",
                expected={"cus_zs": 0, "cus_ww": 0},
                clock_version=1,
            )

            # The subscribe plan's snapshot scopes only cus_zs.
            plan = subscribe_plan(295500)
            snapshot = await l3.snapshot(
                SnapshotRequest(
                    customer_id="cus_zs", steps=list(plan.steps), event_id=None
                ).model_dump(mode="json")
            )
            state = BankState.model_validate(snapshot)
            assert state.accounts["acc_zs_chk"].available_cents == 300000

            gdb = gateway_engine(gateway_db.runtime_url, poolclass=NullPool)
            now = datetime.now(UTC)
            try:
                async with gdb.begin() as conn:
                    await conn.execute(
                        insert(gt.sessions).values(
                            id="ses_w2b", customer_id="cus_zs", created_at_wall=now
                        )
                    )
                    await conn.execute(
                        insert(gt.read_records).values(
                            id="src_acct",
                            sid="ses_w2b",
                            tool="get_accounts",
                            canonical_args={"history_days": 0},
                            response={"accounts": [{"id": "acc_zs_chk"}]},
                            untrusted_paths=[],
                            versions=dict(state.versions),
                            observed_at=state.sim_now,
                            created_at_wall=now,
                        )
                    )

                async def trust_for(amount_cents):
                    ctx = EvalContext(
                        plan_draft=subscribe_plan(amount_cents),
                        plan_id="pln_w2b_sub",
                        origin="agent",
                        customer_id="cus_zs",
                        session_id="ses_w2b",
                        l3=l3,
                    )
                    ctx.wall_now = lambda: datetime.now(UTC)
                    async with gdb.connect() as conn:
                        ctx.conn = conn
                        await ProvenanceStage().run(ctx)
                    return ctx.provenance.arg_sources["s1"]["amount_cents"]["trust"]

                assert await trust_for(295500) == "derived"
                assert await trust_for(295501) == "unknown"

                # A second transfer bumps cus_zs version; the old read ledger is rejected.
                await _transfer(
                    l3,
                    1,
                    plan_id="pln_w2b_2",
                    expected={"cus_zs": 1, "cus_ww": 1},
                    clock_version=1,
                )
                assert await trust_for(295500) == "unknown"

                # Advancing the simulated clock also rejects the stale read ledger.
                async with admin.begin() as conn:
                    await conn.execute(
                        update(m.clock)
                        .where(m.clock.c.id == 1)
                        .values(sim_now=NOW + timedelta(days=1), version=2)
                    )
                assert await trust_for(295500) == "unknown"
            finally:
                await gdb.dispose()
        finally:
            await admin.dispose()
            await runtime.dispose()
            await l3.close()

    asyncio.run(run())
