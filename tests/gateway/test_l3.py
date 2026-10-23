import asyncio

import httpx
import pytest
from safebank_contracts.plan import STEP_MODELS, Execute, InteractiveEvidence, Prepare

from services.gateway.app.l3.client import (
    L3BusinessError,
    L3Client,
    L3NotDelivered,
    L3OutcomeUnknown,
)
from services.gateway.app.l3.fake import FakeL3

TS = "2026-10-15T09:00:00+08:00"
TS2 = "2026-10-15T10:00:00+08:00"


def _step():
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
        steps=(_step(),),
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
        steps=(_step(),),
        expected_versions={"cus_zs": 0},
        clock_version=0,
        authorization_evidence=_evidence(),
    ).model_dump(mode="json")


def test_fake_requires_token():
    async def run():
        fake = FakeL3()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=fake.app()), base_url="http://l3"
        ) as client:
            resp = await client.post("/internal/snapshot", json={})
            assert resp.status_code == 401

    asyncio.run(run())


def test_fake_validates_request_shape():
    async def run():
        fake = FakeL3()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=fake.app()),
            base_url="http://l3",
            headers={"X-Service-Token": "gateway-token"},
        ) as client:
            bad = await client.post(
                "/internal/snapshot", json={"customer_id": "cus_1", "steps": "nope"}
            )
            assert bad.status_code == 422
            ok = await client.post(
                "/internal/snapshot",
                json={"customer_id": "cus_1", "steps": [], "event_id": None},
            )
            assert ok.status_code == 200
            assert ok.json()["clock_version"] == 0

    asyncio.run(run())


def test_client_distinguishes_not_delivered_and_unknown():
    async def run():
        async def connect_timeout(request: httpx.Request):
            raise httpx.ConnectTimeout("refused")

        client = L3Client("http://l3", "t", transport=httpx.MockTransport(connect_timeout))
        with pytest.raises(L3NotDelivered):
            await client.snapshot({})
        await client.close()

        async def read_timeout(request: httpx.Request):
            raise httpx.ReadTimeout("slow")

        client = L3Client("http://l3", "t", transport=httpx.MockTransport(read_timeout))
        with pytest.raises(L3OutcomeUnknown):
            await client.execute({})
        await client.close()

    asyncio.run(run())


def test_client_raises_business_error():
    async def run():
        def handler(request: httpx.Request):
            return httpx.Response(
                409,
                json={
                    "error": {"code": "STATE_CHANGED", "message": "STATE_CHANGED", "details": {}}
                },
            )

        client = L3Client("http://l3", "t", transport=httpx.MockTransport(handler))
        with pytest.raises(L3BusinessError) as exc:
            await client.execute({})
        assert exc.value.code == "STATE_CHANGED"
        await client.close()

    asyncio.run(run())


def test_client_default_timeouts_and_injection():
    client = L3Client("http://l3", "t")
    assert client.timeouts == {"prepare": 3.0, "execute": 10.0, "default": 5.0}
    client = L3Client("http://l3", "t", timeouts={"prepare": 0.1, "execute": 0.2, "default": 0.3})
    assert client.timeouts["prepare"] == 0.1


def test_fake_timeout_surfaces_as_outcome_unknown():
    async def run():
        fake = FakeL3()
        fake.behaviors["execute"] = "timeout"
        client = L3Client("http://l3", "gateway-token", transport=fake.transport())
        with pytest.raises(L3OutcomeUnknown):
            await client.execute(valid_execute_body())
        await client.close()

    asyncio.run(run())


def test_fake_drop_surfaces_as_outcome_unknown():
    async def run():
        fake = FakeL3()
        fake.behaviors["execute"] = "drop"
        client = L3Client("http://l3", "gateway-token", transport=fake.transport())
        with pytest.raises(L3OutcomeUnknown):
            await client.execute(valid_execute_body())
        await client.close()

    asyncio.run(run())


def test_fake_commit_then_drop_then_query_recovery():
    async def run():
        fake = FakeL3()
        fake.behaviors["execute"] = "commit_then_drop"
        client = L3Client("http://l3", "gateway-token", transport=fake.transport())
        await client.prepare(valid_prepare_body())
        with pytest.raises(L3OutcomeUnknown):
            await client.execute(valid_execute_body())
        status = await client.execution("cus_zs", "pln_1")
        assert status["status"] == "COMMITTED"
        assert status["result"]["plan_id"] == "pln_1"
        await client.close()

    asyncio.run(run())


def test_fake_state_changed_is_business_error():
    async def run():
        fake = FakeL3()
        fake.behaviors["execute"] = "state_changed"
        client = L3Client("http://l3", "gateway-token", transport=fake.transport())
        with pytest.raises(L3BusinessError) as exc:
            await client.execute(valid_execute_body())
        assert exc.value.code == "STATE_CHANGED"
        await client.close()

    asyncio.run(run())


def test_fake_custom_result_and_snapshot():
    async def run():
        fake = FakeL3()
        from safebank_contracts.plan import ExecutionResult

        fake.set_result(
            ExecutionResult(
                plan_id="pln_9",
                status="FAILED",
                steps=(),
                versions={},
                committed_at=TS,
                error_code="INSUFFICIENT_FUNDS",
                replayed=False,
            )
        )
        client = L3Client("http://l3", "gateway-token", transport=fake.transport())
        await client.prepare(valid_prepare_body())
        result = await client.execute(valid_execute_body())
        assert result["status"] == "FAILED"
        assert result["plan_id"] == "pln_9"
        await client.close()

    asyncio.run(run())
