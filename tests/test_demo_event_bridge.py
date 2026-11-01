"""D4 registration must reach the same immutable event applier as S1."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from app.events import EventService, _demo_data
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from services.gateway.app.api import demo
from services.gateway.app.errors import GatewayError
from tests.bankcore.test_external import NOW, _split_state, _state


def _row(payload, event_id="evt_one"):
    return {"id": event_id, "due_at": NOW, "immutable_payload": payload}


@pytest.mark.parametrize("kind", ["EXTERNAL_CREDIT", "EXTERNAL_DEBIT"])
def test_registered_uppercase_payload_is_executable(kind):
    payload = {"kind": kind, "customer_id": "cus_a", "account_id": "acc_a", "amount_cents": 100}
    try:
        after, effects, errors = EventService()._apply_demo(_state(), _row(payload))
    except Exception as exc:
        pytest.fail(f"registered payload rejected: {type(exc).__name__}")
    assert not errors and effects
    assert after.accounts["acc_a"].balance_cents == 600_000 + (
        100 if kind == "EXTERNAL_CREDIT" else -100
    )


def test_registered_split_item_resolves_owner_and_pays_exact_amount():
    state = _split_state()
    payload = {
        "kind": "SPLIT_PAYMENT",
        "customer_id": "cus_a",
        "account_id": "acc_a",
        "amount_cents": 3334,
        "split_item_id": "spi_1",
    }
    try:
        after, effects, errors = EventService()._apply_demo(state, _row(payload))
    except Exception as exc:
        pytest.fail(f"registered AA payment rejected: {type(exc).__name__}")
    assert effects and not errors
    assert after.splits[0].received_cents == 3334
    assert after.splits[0].items[0].status == "PAID"


def test_two_external_events_have_distinct_ledger_identity():
    payload = {"kind": "credit", "customer_id": "cus_a", "account_id": "acc_a", "amount_cents": 100}
    wrapped = {"payload": json.dumps(payload)}
    first, _, errors = EventService()._apply_demo(_state(), _row(wrapped, "evt_one"))
    assert not errors
    second, effects, errors = EventService()._apply_demo(first, _row(wrapped, "evt_two"))
    assert not errors and effects
    assert second.accounts["acc_a"].balance_cents == 600200


def test_flat_and_legacy_wrapped_data_are_supported():
    payload = {"kind": "EXTERNAL_CREDIT", "customer_id": "cus_a"}
    assert _demo_data(payload) == payload
    assert _demo_data({"payload": json.dumps(payload)}) == payload


def test_d4_endpoint_registers_and_authorizes_via_existing_gateway_handler(monkeypatch):
    async def run():
        l3 = SimpleNamespace(
            snapshot=AsyncMock(return_value={"sim_now": NOW.isoformat()}),
            demo_event=AsyncMock(return_value={"event_id": "evt_d4"}),
        )
        app = FastAPI()
        app.include_router(demo.router)
        app.state.l3 = l3
        app.state.settings = SimpleNamespace(demo_mode=True, eval_mode=False)
        app.state.db = object()
        from services.gateway.app.schemas.enums import ServiceKind

        @app.middleware("http")
        async def caller(request, call_next):
            request.state.caller = SimpleNamespace(
                kind=ServiceKind.D, scopes={"console"}, jti="console1"
            )
            return await call_next(request)

        app.state.wall_now = lambda: NOW

        @app.exception_handler(GatewayError)
        async def fail(request, exc):
            return JSONResponse(exc.body("trace"), status_code=exc.status)

        monkeypatch.setattr(demo, "append_tx", AsyncMock())
        # A real S1 invocation is substituted at the boundary, never an L3 direct execute.
        import services.gateway.app.internal.scheduled as scheduled

        authorize = AsyncMock(
            return_value={"event_id": "evt_d4", "status": "SUCCESS", "code": None}
        )
        monkeypatch.setattr(scheduled, "authorize_event", authorize, raising=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.post(
                "/demo/events",
                json={
                    "request_id": "request1",
                    "kind": "EXTERNAL_CREDIT",
                    "customer_id": "cus_a",
                    "account_id": "acc_a",
                    "amount_cents": 100,
                },
            )
        assert r.status_code == 200, r.text
        assert r.json()["event_id"] == "evt_d4"
        assert r.json()["result"]["status"] == "SUCCESS"
        assert l3.demo_event.await_count == 1
        assert authorize.await_count == 1

    asyncio.run(run())


@pytest.mark.parametrize("amount", [True, 1.0, "100"])
def test_registration_rejects_coerced_money(amount):
    from app.demo import DemoEventRequest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        DemoEventRequest(
            kind="EXTERNAL_CREDIT",
            customer_id="cus_a",
            account_id="acc_a",
            amount_cents=amount,
            due_at=NOW,
        )


def test_registered_split_wrong_account_cannot_credit_another_split():
    state = _split_state()
    payload = {
        "kind": "SPLIT_PAYMENT",
        "customer_id": "cus_a",
        "account_id": "acc_s",
        "amount_cents": 3334,
        "split_item_id": "spi_1",
    }
    try:
        after, effects, errors = EventService()._apply_demo(state, _row(payload))
    except Exception as exc:
        pytest.fail(f"expected deterministic rejection, got {type(exc).__name__}")
    assert errors and not effects and after == state


@pytest.mark.parametrize("channel", ["OVERSEAS", "ATM", "QUICKPASS"])
def test_registered_card_spend_honors_disabled_channels(channel):
    state = _state()
    payload = {
        "kind": "EXTERNAL_DEBIT",
        "customer_id": "cus_a",
        "account_id": "acc_a",
        "amount_cents": 100,
        "card_id": "crd_a",
        "channel": channel,
    }
    after, effects, errors = EventService()._apply_demo(state, _row(payload))
    assert errors and not effects and after == state


def test_bad_payment_amount_is_not_silently_accepted():
    state = _split_state()
    payload = {
        "kind": "SPLIT_PAYMENT",
        "customer_id": "cus_a",
        "account_id": "acc_a",
        "amount_cents": 1,
        "split_item_id": "spi_1",
    }
    after, effects, errors = EventService()._apply_demo(state, _row(payload))
    assert errors and not effects and after == state
