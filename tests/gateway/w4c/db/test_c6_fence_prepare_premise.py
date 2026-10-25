"""Task 0 / C6 premise: real L3 accepts a same-attempt prepare with an
already-expired ``deadline_wall`` (the ADR-008 fence prepare).

This is the empirical prerequisite for the STATE_CHANGED barrier (W4c task book
§6): ``acknowledge_fence`` writes ``deadline_wall = FENCE_AT =
1970-01-01T00:00:00Z`` and the barrier must re-prepare the *same attempt* with
that expired deadline to prove L3 has not committed a new intent.  If real L3
rejected the expired deadline here, the whole barrier design would be
unworkable and W4c would stop and report (ruling C6).
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
from safebank_contracts.plan import Prepare
from sqlalchemy.pool import NullPool

from services.gateway.app.l3.client import L3Client
from tests.contracts.samples import step
from tests.obligations_data.fixtures import KEY, install_core, install_obligations

SERVICE_TOKEN = "obligations-test-token"
PLAN_HASH = "0" * 64
FENCE_AT = datetime(1970, 1, 1, tzinfo=UTC)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _serve(app, port):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()


async def _truncate(admin):
    from sqlalchemy import text

    names = ",".join(m.metadata.tables)
    async with admin.begin() as conn:
        await conn.execute(text(f"TRUNCATE {names} CASCADE"))


async def _serve_l3(obligations_db):
    admin = bank_engine(obligations_db.admin_url, poolclass=NullPool)
    runtime = bank_engine(obligations_db.runtime_url, poolclass=NullPool)
    await _truncate(admin)
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
    return admin, runtime, l3


def _self_transfer(amount_cents):
    return [
        step(
            from_account_id="acc_zs_chk",
            payee_id=None,
            to_account_id="acc_zs_sav",
            amount_cents=amount_cents,
            step_id="s1",
        )
    ]


def _evidence():
    return {
        "type": "INTERACTIVE",
        "origin": "app_form",
        "scope_ids": ["scp_w4c"],
        "utterance_ids": ["utt_w4c"],
        "evaluation_id": "evl_w4c",
        "approved_evaluation_id": None,
        "plan_hash": PLAN_HASH,
        "approval_id": None,
        "confirmed_view_hash": None,
        "final_view_hash": PLAN_HASH,
        "risk": "GREEN",
        "confirmed_at_wall": None,
        "authorization_epoch": 0,
    }


def test_same_attempt_prepare_accepts_expired_deadline(obligations_db):
    async def run():
        admin, runtime, l3 = await _serve_l3(obligations_db)
        try:
            steps = _self_transfer(100)
            evidence = _evidence()
            now = datetime.now(UTC)
            base = dict(
                customer_id="cus_zs",
                plan_id="pln_c6_fence",
                attempt=1,
                plan_hash=PLAN_HASH,
                steps=steps,
                authorization_evidence=evidence,
                deadline_cap_wall=now + timedelta(minutes=10),
            )
            first = Prepare(**base, deadline_wall=now + timedelta(minutes=5))
            result = await l3.prepare(first.model_dump(mode="json"))
            assert result["status"] == "PREPARED"
            assert result["attempt"] == 1

            # Same attempt, same evidence/steps/plan_hash/deadline_cap_wall,
            # but deadline already expired (FENCE_AT).  The ADR-008 fence
            # prepare must not be rejected.
            second = Prepare(**base, deadline_wall=FENCE_AT)
            result2 = await l3.prepare(second.model_dump(mode="json"))
            assert result2["status"] == "PREPARED"
            assert result2["attempt"] == 1
        finally:
            await admin.dispose()
            await runtime.dispose()
            await l3.close()

    asyncio.run(run())
