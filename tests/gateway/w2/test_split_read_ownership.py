"""G2 split-by-ID uses the authoritative split account's current owner."""

import asyncio

import pytest
from safebank_contracts.codes import Code

from services.gateway.app.api.tools import fetch_read
from services.gateway.app.errors import GatewayError
from tests.bankcore.test_external import _split_state


class SplitReadL3:
    """In-process contract double; no HTTP, DB or provider is used."""

    def __init__(self, state):
        self.state = state
        self.reads = []
        self.snapshots = 0

    async def snapshot(self, payload):
        self.snapshots += 1
        return self.state.model_dump(mode="json")

    async def read(self, tool, payload):
        self.reads.append((tool, payload))
        assert tool == "get_split_status"
        return {
            "items": [s.model_dump(mode="json") for s in self.state.splits],
            "next_cursor": "next_page" if payload["args"]["cursor"] else None,
        }


def test_g2_owned_split_id_returns_actual_progress_after_two_snapshot_fences():
    async def run():
        l3 = SplitReadL3(_split_state())
        result = await fetch_read(
            l3, "cus_a", "get_split_status", {"request_id": "spl_x", "cursor": None, "limit": 50}
        )
        assert result.data["items"][0]["id"] == "spl_x"
        assert result.data["items"][0]["received_cents"] == 0
        assert result.data["items"][0]["outstanding_cents"] == 10000
        assert len(l3.reads) == 1 and l3.snapshots == 2

    asyncio.run(run())


@pytest.mark.parametrize("mutation", ["unknown", "other_owner", "missing_account"])
def test_g2_unknown_or_unowned_split_id_remains_404_without_reading_l3(mutation):
    async def run():
        state = _split_state()
        request_id = "spl_missing" if mutation == "unknown" else "spl_x"
        if mutation == "other_owner":
            accounts = dict(state.accounts)
            accounts["acc_a"] = accounts["acc_a"].model_copy(update={"customer_id": "cus_other"})
            state = state.model_copy(update={"accounts": accounts})
        elif mutation == "missing_account":
            state = state.model_copy(
                update={
                    "splits": (state.splits[0].model_copy(update={"account_id": "acc_missing"}),)
                }
            )
        l3 = SplitReadL3(state)
        with pytest.raises(GatewayError) as error:
            await fetch_read(
                l3,
                "cus_a",
                "get_split_status",
                {"request_id": request_id, "cursor": None, "limit": 50},
            )
        assert error.value.status == 404 and error.value.code is Code.NOT_FOUND
        assert not l3.reads

    asyncio.run(run())


@pytest.mark.parametrize("cursor", [None, "prior_page"])
def test_g2_none_request_id_keeps_list_and_pagination_arguments(cursor):
    async def run():
        l3 = SplitReadL3(_split_state())
        args = {"request_id": None, "cursor": cursor, "limit": 1}
        result = await fetch_read(l3, "cus_a", "get_split_status", args)
        assert len(result.data["items"]) == 1
        assert result.data["next_cursor"] == ("next_page" if cursor else None)
        assert l3.reads == [("get_split_status", {"customer_id": "cus_a", "args": args})]
        assert l3.snapshots == 2

    asyncio.run(run())
