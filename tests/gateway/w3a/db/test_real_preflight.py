"""Real bank PG + real L3 snapshot HTTP → preflight stage (scene 2 / reserve).

Proves the production ``PreflightStage`` consumes the real ``BankState``
(snapshot once), the real 7-day reserve projection and the same
``bankcore.apply_plan`` L3 executes. This is not G4, approval or fund execution.
"""

import asyncio
import socket
import threading

import httpx
import uvicorn
from app import models as m
from app.db import engine as bank_engine
from app.main import create_app
from safebank_contracts.codes import Code
from safebank_contracts.plan import PlanDraft, SnapshotRequest
from safebank_contracts.state import BankState
from sqlalchemy.pool import NullPool

from services.gateway.app.l3.client import L3Client
from services.gateway.app.pipeline.context import EvalContext
from services.gateway.app.pipeline.entity import EntityStage
from services.gateway.app.pipeline.preflight import PreflightStage
from services.gateway.app.schemas.enums import FindingEffect
from tests.contracts.samples import step
from tests.obligations_data.fixtures import KEY, install_core, install_obligations

SERVICE_TOKEN = "obligations-test-token"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _serve(app, port):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()


def _plan(steps):
    return PlanDraft.model_validate(
        {
            "request_id": "req_preflight",
            "session_id": "ses_preflight",
            "utterance_ids": ["utt_preflight"],
            "intents": [],
            "rationale": "",
            "steps": steps,
        }
    )


def _scene2():
    a = step(
        from_account_id="acc_zs_chk",
        payee_id="pye_zs_ww",
        to_account_id=None,
        amount_cents=300000,
        step_id="s1",
    )
    b = step(
        from_account_id="acc_zs_chk",
        payee_id="pye_zs_zl",
        to_account_id=None,
        amount_cents=400000,
        step_id="s2",
    )
    b["depends_on"] = ["s1"]
    return [a, b]


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


async def _preflight(plan, l3):
    snapshot = await l3.snapshot(
        SnapshotRequest(customer_id="cus_zs", steps=list(plan.steps), event_id=None).model_dump(
            mode="json"
        )
    )
    state = BankState.model_validate(snapshot)
    ctx = EvalContext(
        plan_draft=plan,
        plan_id="pln_preflight",
        origin="app_form",
        customer_id="cus_zs",
        session_id="ses_preflight",
        l3=l3,
    )
    ctx._lazy_state = state
    ctx.snapshot = state
    await EntityStage().run(ctx)
    await PreflightStage().run(ctx)
    return ctx, state


def test_scene2_real_snapshot_replan_with_hint_295500(obligations_db):
    async def run():
        admin, runtime, l3 = await _serve_l3(obligations_db)
        try:
            ctx, state = await _preflight(_plan(_scene2()), l3)
            assert state.accounts["acc_zs_chk"].available_cents == 600000
            assert ctx.snapshot.clock_version == 1
            assert ctx.snapshot.versions["cus_zs"] == 0
            assert ctx.preflight.scheduled_reserve_cents == 4500
            insufficient = [f for f in ctx.findings if f.code is Code.INSUFFICIENT_FUNDS]
            assert len(insufficient) == 1
            assert insufficient[0].effect is FindingEffect.REPLAN
            assert insufficient[0].resolver == "user"
            assert ctx.preflight.executable is False
            assert ctx.preflight.hint_max_outflow["s2"] == 295500
        finally:
            await admin.dispose()
            await runtime.dispose()
            await l3.close()

    asyncio.run(run())


def test_self_transfer_dropping_below_reserve_is_shortfall(obligations_db):
    async def run():
        admin, runtime, l3 = await _serve_l3(obligations_db)
        try:
            ctx, state = await _preflight(_plan(_self_transfer(598000)), l3)
            assert state.accounts["acc_zs_chk"].available_cents == 600000
            assert any(
                f.code is Code.SCHEDULED_SHORTFALL and f.resolver == "user" for f in ctx.findings
            )
            assert ctx.preflight.shortfall_before_cents == 0
            assert ctx.preflight.shortfall_after_cents == 2500
        finally:
            await admin.dispose()
            await runtime.dispose()
            await l3.close()

    asyncio.run(run())


def test_protective_cancellation_snapshot_displays_before_reserve_without_bank_write(
    obligations_db,
):
    from services.agent.app.gateway.wire import GatewayPreflight
    from services.gateway.app.pipeline.decision import DecisionStage

    async def run():
        admin, runtime, l3 = await _serve_l3(obligations_db)
        try:
            plan = _plan([step("cancel_subscription", instruction_id="si_zs_music")])
            ctx, state = await _preflight(plan, l3)
            view = DecisionStage._preflight_view(ctx).model_dump(mode="json")
            assert ctx.preflight.executable
            assert not any(f.code is Code.SCHEDULED_SHORTFALL for f in ctx.findings)
            assert view["scheduled_reserve_cents"] == 4500
            assert view["reserve_projection_available"] is True
            assert GatewayPreflight.model_validate(view).reserve_projection_available is True
            request = SnapshotRequest(customer_id="cus_zs", steps=list(plan.steps), event_id=None)
            after = BankState.model_validate(await l3.snapshot(request.model_dump(mode="json")))
            assert after.model_dump(mode="json") == state.model_dump(mode="json")
            assert after.obligations.standing_instructions["si_zs_music"].status == "ACTIVE"
        finally:
            await admin.dispose()
            await runtime.dispose()
            await l3.close()

    asyncio.run(run())
