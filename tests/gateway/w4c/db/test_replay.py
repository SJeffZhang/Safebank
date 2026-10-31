"""Red tests for the W4c ``replay()`` entry point (PRD §7.10.4).

``services/gateway/app/execution/replay.py`` must re-run the evaluation pipeline
in ``EvalMode.EXECUTE_REPLAY`` on a fresh snapshot before execute. These tests
pin that contract by monkeypatching ``pipeline.evaluate`` to capture the call;
the snapshot itself always comes from a real L3 (the w3a ``_serve_l3`` pattern).

All three currently fail because ``replay`` raises ``NotImplementedError``; they
turn green once W4c implements it (the fake ``evaluate`` records mode / context
/ Decision and the real L3 proves the fresh snapshot).
"""

import asyncio
import socket
import threading

import httpx
import uvicorn
from app import models as m
from app.db import engine as bank_engine
from app.main import create_app
from app.state import persist
from safebank_bankcore.ledger import post_transfer
from safebank_contracts.enums import Risk
from safebank_contracts.plan import PlanDraft, SnapshotRequest
from safebank_contracts.state import BankState, LedgerTransfer
from sqlalchemy.pool import NullPool

from services.gateway.app.execution.replay import replay
from services.gateway.app.l3.client import L3Client
from services.gateway.app.pipeline.context import (
    EvalContext,
    EvalMode,
    PreflightResult,
    ProvenanceResult,
    WorkflowResult,
)
from services.gateway.app.schemas.decision import Decision, DecisionValue
from tests.contracts.samples import step
from tests.obligations_data.fixtures import KEY, install_core, install_obligations

SERVICE_TOKEN = "obligations-test-token"
OLD_BALANCE = 600_000
NEW_BALANCE = 555_000


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


def _ctx(l3):
    steps = [
        step(
            from_account_id="acc_zs_chk",
            payee_id=None,
            to_account_id="acc_zs_sav",
            amount_cents=1,
            step_id="s1",
        )
    ]
    plan = PlanDraft.model_validate(
        {
            "request_id": "req_replay",
            "session_id": "ses_replay",
            "utterance_ids": ["utt_replay"],
            "intents": [],
            "rationale": "",
            "steps": steps,
        }
    )
    return EvalContext(
        plan_draft=plan,
        plan_id="pln_replay",
        origin="agent",
        customer_id="cus_zs",
        session_id="ses_replay",
        l3=l3,
    )


def _decision():
    return Decision(
        plan_id="pln_replay",
        evaluation_id="evl_replay",
        revision=1,
        decision=DecisionValue.ALLOW,
        risk=Risk.GREEN,
    )


async def _snapshot(l3):
    resp = await l3.snapshot(
        SnapshotRequest(customer_id="cus_zs", steps=[], event_id=None).model_dump(mode="json")
    )
    return BankState.model_validate(resp)


async def _mutate_balance(admin, l3):
    """Change acc_zs_chk's balance through the same persist path as the fixture."""
    state = await _snapshot(l3)
    cmd = LedgerTransfer(
        id="trf_w4c_replay",
        debit_account_id="acc_zs_chk",
        credit_account_id="acc_zs_sav",
        amount_cents=OLD_BALANCE - NEW_BALANCE,
        code="SEED",
        phase="POSTED",
        pending_id=None,
        plan_id="pln_w4c_replay",
        step_id="s1",
        occurred_at=state.sim_now,
        expires_at=None,
    )
    _, effects, errors = post_transfer(state, cmd, state.sim_now)
    if errors:
        raise RuntimeError(f"mutate failed: {errors}")
    async with admin.begin() as conn:
        await persist(conn, effects)


def test_replay_uses_execute_replay_mode(obligations_db, monkeypatch):
    async def run():
        admin, runtime, l3 = await _serve_l3(obligations_db)
        try:
            ctx = _ctx(l3)
            expected = _decision()
            captured = {}

            async def fake_evaluate(ctx_arg, *, stages=None, mode=None):
                captured["mode"] = mode
                captured["ctx"] = ctx_arg
                ctx_arg.decision = expected
                return ctx_arg

            monkeypatch.setattr("services.gateway.app.pipeline.evaluate", fake_evaluate)
            decision = await replay(ctx)
            assert captured["mode"] is EvalMode.EXECUTE_REPLAY
            assert decision == expected
        finally:
            await admin.dispose()
            await runtime.dispose()
            await l3.close()

    asyncio.run(run())


def test_replay_fetches_fresh_snapshot(obligations_db, monkeypatch):
    async def run():
        admin, runtime, l3 = await _serve_l3(obligations_db)
        try:
            ctx = _ctx(l3)
            old = await ctx.bank_state()
            assert old.accounts["acc_zs_chk"].available_cents == OLD_BALANCE
            await _mutate_balance(admin, l3)
            assert (await _snapshot(l3)).accounts["acc_zs_chk"].available_cents == NEW_BALANCE

            captured = {}

            async def fake_evaluate(ctx_arg, *, stages=None, mode=None):
                fresh = await ctx_arg.bank_state()
                captured["balance"] = fresh.accounts["acc_zs_chk"].available_cents
                ctx_arg.decision = _decision()
                return ctx_arg

            monkeypatch.setattr("services.gateway.app.pipeline.evaluate", fake_evaluate)
            await replay(ctx)
            assert captured["balance"] == NEW_BALANCE
        finally:
            await admin.dispose()
            await runtime.dispose()
            await l3.close()

    asyncio.run(run())


def test_replay_uses_fresh_context(obligations_db, monkeypatch):
    async def run():
        admin, runtime, l3 = await _serve_l3(obligations_db)
        try:
            ctx = _ctx(l3)
            ctx.snapshot = await ctx.bank_state()
            ctx.provenance = ProvenanceResult(arg_sources={"s1": "stale"})
            ctx.preflight = PreflightResult(executable=False)
            ctx.workflow = WorkflowResult(findings=({"stale": True},))
            ctx.decision = _decision()

            async def fake_evaluate(ctx_arg, *, stages=None, mode=None):
                assert ctx_arg.provenance is None
                assert ctx_arg.snapshot is None
                assert ctx_arg.preflight is None
                assert ctx_arg.workflow is None
                assert ctx_arg.decision is None
                ctx_arg.decision = _decision()
                return ctx_arg

            monkeypatch.setattr("services.gateway.app.pipeline.evaluate", fake_evaluate)
            await replay(ctx)
        finally:
            await admin.dispose()
            await runtime.dispose()
            await l3.close()

    asyncio.run(run())
