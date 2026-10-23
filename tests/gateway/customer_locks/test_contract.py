import asyncio

import pytest
from safebank_contracts.codes import Code

from services.gateway.app import locks
from services.gateway.app.errors import GatewayError


def test_unconfigured_lock_fails_closed(monkeypatch):
    monkeypatch.setattr(locks, "_engine", None, raising=False)
    monkeypatch.setattr(locks, "_slots", None, raising=False)

    async def run():
        with pytest.raises(GatewayError) as error:
            async with locks.customer_lock(["cus_unconfigured"]):
                pytest.fail("unconfigured lock yielded")
        assert error.value.code == Code.DEPENDENCY_UNAVAILABLE
        assert error.value.status == 503

    asyncio.run(run())


@pytest.mark.parametrize("value", [0, -1, True, False, 1.5, "2", None])
def test_invalid_max_executions(value):
    with pytest.raises(ValueError):
        locks.configure(None, value)


@pytest.mark.parametrize(
    "values", [[], [""], [" "], [None], [1], [True], "cus_one", b"cus_one", ["a\x00b"], ["a" * 257]]
)
def test_invalid_customer_ids(values):
    async def run():
        with pytest.raises(ValueError):
            async with locks.customer_lock(values):
                pytest.fail("invalid IDs yielded")

    asyncio.run(run())
