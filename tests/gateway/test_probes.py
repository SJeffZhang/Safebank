import asyncio

import pytest
from safebank_contracts.state import BankState

from services.gateway.app import locks
from services.gateway.app.approval import planview, service
from services.gateway.app.evaluation import repo
from services.gateway.app.execution import replay
from services.gateway.app.l3.client import L3Client
from services.gateway.app.l3.fake import FakeL3
from services.gateway.app.pipeline.context import EvalContext


def test_bank_state_returns_and_caches_probe_b():
    async def run():
        fake = FakeL3()
        client = L3Client("http://l3", "gateway-token", transport=fake.transport())
        ctx = EvalContext(customer_id="cus_1", l3=client)
        first = await ctx.bank_state()
        second = await ctx.bank_state()
        assert isinstance(first, BankState)
        assert first is second
        assert [call["op"] for call in fake.calls].count("snapshot") == 1
        await client.close()

    asyncio.run(run())


def test_cross_interface_placeholders_exist_probe_d():
    for fn in (
        locks.customer_lock,
        service.create_for_evaluation,
        planview.build_view,
        repo.append_evaluation,
        replay.replay,
    ):
        assert callable(fn)
        assert fn.__doc__


def test_create_for_evaluation_is_not_implemented():
    async def run():
        with pytest.raises(NotImplementedError):
            await service.create_for_evaluation(None, plan=None, evaluation=None, view=None)

    asyncio.run(run())
