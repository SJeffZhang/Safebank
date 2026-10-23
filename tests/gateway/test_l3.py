import asyncio

import httpx
import pytest
from safebank_contracts.plan import STEP_MODELS, Execute, InteractiveEvidence, Prepare

from services.gateway.app.l3.client import L3Client, L3Timeout, L3Unavailable
from services.gateway.app.l3.fake import DropConnection, FakeL3

TS = "2026-10-15T09:00:00+08:00"
TS2 = "2026-10-15T10:00:00+08:00"
SNAPSHOT_OK = {"customer_id": "cus_1", "steps": [], "event_id": None}


def _transfer_step():
    return STEP_MODELS["transfer"](
        step_id="st_1",
        depends_on=(),
        arg_sources=(),
        tool="transfer",
        args={
            "from_account_id": "acc_zs",
            "payee_id": "pye_ww",
            "to_account_id": None,
            "amount_cents": 100,
            "memo": None,
        },
    )


def _evidence():
    return InteractiveEvidence(
        type="INTERACTIVE",
        origin="agent",
        scope_ids=("utt_1",),
        utterance_ids=("utt_1",),
        evaluation_id="evl_1",
        approved_evaluation_id=None,
        plan_hash="abc",
        approval_id=None,
        confirmed_view_hash=None,
        final_view_hash="abc",
        risk="GREEN",
        confirmed_at_wall=None,
        authorization_epoch=0,
    )


def valid_prepare_body():
    return Prepare(
        customer_id="cus_zs",
        plan_id="pln_1",
        attempt=1,
        plan_hash="abc",
        steps=(_transfer_step(),),
        deadline_wall=TS,
        deadline_cap_wall=TS2,
        authorization_evidence=_evidence(),
    ).model_dump(mode="json")


def valid_execute_body():
    return Execute(
        customer_id="cus_zs",
        plan_id="pln_1",
        attempt=1,
        plan_hash="abc",
        steps=(_transfer_step(),),
        expected_versions={"cus_zs": 0},
        clock_version=0,
        authorization_evidence=_evidence(),
    ).model_dump(mode="json")


def _client(app, **kwargs):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://l3", **kwargs)


def test_fake_snapshot_validates_and_returns_bank_state():
    async def run():
        fake = FakeL3()
        async with _client(fake.app()) as client:
            ok = await client.post("/internal/snapshot", json=SNAPSHOT_OK)
            assert ok.status_code == 200
            assert ok.json()["clock_version"] == 0
            bad = await client.post(
                "/internal/snapshot", json={"customer_id": "cus_1", "steps": "not-a-list"}
            )
            assert bad.status_code == 422
            assert bad.json()["error"]["code"] == "CONTRACT_INVALID"

    asyncio.run(run())


def test_fake_read_validates_args_and_returns_minimal_result():
    async def run():
        fake = FakeL3()
        async with _client(fake.app()) as client:
            ok = await client.post(
                "/internal/read/get_accounts",
                json={"customer_id": "cus_1", "args": {"history_days": 30}},
            )
            assert ok.status_code == 200
            assert ok.json()["accounts"] == []
            bad = await client.post(
                "/internal/read/get_accounts",
                json={"customer_id": "cus_1", "args": {"history_days": "nope"}},
            )
            assert bad.status_code == 422

    asyncio.run(run())


def test_fake_prepare_and_execute_success_shapes():
    async def run():
        fake = FakeL3()
        async with _client(fake.app()) as client:
            prepared = await client.post("/internal/plans/prepare", json=valid_prepare_body())
            assert prepared.status_code == 200
            assert prepared.json()["status"] == "PREPARED"
            executed = await client.post("/internal/plans/execute", json=valid_execute_body())
            assert executed.status_code == 200
            assert executed.json()["status"] == "EXECUTED"
            assert executed.json()["plan_id"] == "pln_1"

    asyncio.run(run())


def test_fake_rejects_invalid_prepare():
    async def run():
        fake = FakeL3()
        async with _client(fake.app()) as client:
            resp = await client.post("/internal/plans/prepare", json={"customer_id": "cus_1"})
            assert resp.status_code == 422

    asyncio.run(run())


def test_fake_state_changed_behavior():
    async def run():
        fake = FakeL3()
        fake.behaviors["execute"] = "state_changed"
        async with _client(fake.app()) as client:
            resp = await client.post("/internal/plans/execute", json=valid_execute_body())
            assert resp.status_code == 409
            assert resp.json()["error"]["code"] == "STATE_CHANGED"

    asyncio.run(run())


def test_fake_timeout_behavior_sleeps_past_deadline():
    async def run():
        import time

        fake = FakeL3()
        fake.behaviors["snapshot"] = "timeout"
        fake.timeout_delay = 0.3
        async with _client(fake.app()) as client:
            start = time.monotonic()
            await client.post("/internal/snapshot", json=SNAPSHOT_OK)
            assert time.monotonic() - start >= 0.3

    asyncio.run(run())


def test_fake_drop_behavior():
    async def run():
        fake = FakeL3()
        fake.behaviors["snapshot"] = "drop"
        async with _client(fake.app()) as client:
            with pytest.raises(DropConnection):
                await client.post("/internal/snapshot", json=SNAPSHOT_OK)

    asyncio.run(run())


def test_client_sends_token_and_uses_endpoint_paths():
    seen = {}

    async def handler(request: httpx.Request):
        seen["token"] = request.headers.get("X-Service-Token")
        seen["path"] = request.url.path
        return httpx.Response(200, json={"ok": True})

    async def run():
        client = L3Client("http://l3", "gateway-token", transport=httpx.MockTransport(handler))
        await client.prepare({})
        await client.close()

    asyncio.run(run())
    assert seen == {"token": "gateway-token", "path": "/internal/plans/prepare"}


def test_client_timeout_values_are_fixed():
    client = L3Client("http://l3", "t")
    assert client.timeouts == {"prepare": 3.0, "execute": 10.0, "default": 5.0}


def test_client_translates_timeout_and_drop():
    async def run():
        async def timeout_handler(request: httpx.Request):
            raise httpx.ReadTimeout("slow")

        client = L3Client("http://l3", "t", transport=httpx.MockTransport(timeout_handler))
        with pytest.raises(L3Timeout):
            await client.execute({})
        await client.close()

        async def drop_handler(request: httpx.Request):
            raise httpx.ConnectError("refused")

        client = L3Client("http://l3", "t", transport=httpx.MockTransport(drop_handler))
        with pytest.raises(L3Unavailable):
            await client.snapshot({})
        await client.close()

    asyncio.run(run())
