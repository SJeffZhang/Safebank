import asyncio

import pytest
from safebank_contracts.codes import Code
from sqlalchemy.pool import NullPool

from services.gateway.app.db import engine
from services.gateway.app.errors import GatewayError
from services.gateway.app.idempotency import request_idempotent


def _runtime(gateway_db):
    return engine(gateway_db.runtime_url, poolclass=NullPool)


async def _produce(status=200, body=None):
    return status, body or {"ok": True}


def test_same_body_replays(gateway_db):
    async def run():
        db = _runtime(gateway_db)
        async with db.begin() as conn:
            status, body, replayed = await request_idempotent(
                conn,
                customer_id="cus_1",
                route="POST /plans/evaluate",
                request_id="req_1",
                body={"steps": []},
                produce=lambda: _produce(),
            )
            assert replayed is False
            assert body == {"ok": True}
        async with db.begin() as conn:
            status, body, replayed = await request_idempotent(
                conn,
                customer_id="cus_1",
                route="POST /plans/evaluate",
                request_id="req_1",
                body={"steps": []},
                produce=lambda: _produce(),
            )
            assert replayed is True
            assert body == {"ok": True}
        await db.dispose()

    asyncio.run(run())


def test_different_body_conflicts(gateway_db):
    async def run():
        db = _runtime(gateway_db)
        async with db.begin() as conn:
            await request_idempotent(
                conn,
                customer_id="cus_2",
                route="POST /plans/evaluate",
                request_id="req_2",
                body={"a": 1},
                produce=lambda: _produce(),
            )
        async with db.begin() as conn:
            with pytest.raises(GatewayError) as exc:
                await request_idempotent(
                    conn,
                    customer_id="cus_2",
                    route="POST /plans/evaluate",
                    request_id="req_2",
                    body={"a": 2},
                    produce=lambda: _produce(),
                )
            assert exc.value.code is Code.IDEMPOTENCY_CONFLICT
        await db.dispose()

    asyncio.run(run())


def test_concurrent_same_id_single_execution(gateway_db):
    async def run():
        db = _runtime(gateway_db)
        produced = []

        async def produce():
            produced.append(1)
            return 200, {"ok": True}

        async def one():
            async with db.begin() as conn:
                return await request_idempotent(
                    conn,
                    customer_id="cus_3",
                    route="POST /plans/evaluate",
                    request_id="req_3",
                    body={"steps": []},
                    produce=produce,
                )

        results = await asyncio.gather(one(), one(), return_exceptions=True)
        assert len(produced) == 1
        assert any(r == (200, {"ok": True}, False) for r in results if not isinstance(r, Exception))
        await db.dispose()

    asyncio.run(run())
