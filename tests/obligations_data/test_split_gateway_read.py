"""Real L3 PostgreSQL split payment read through G2 version/ownership fences."""

import asyncio

import httpx
import pytest
from app import models as m
from safebank_contracts.codes import Code
from sqlalchemy import select

from services.gateway.app.api.tools import fetch_read
from services.gateway.app.errors import GatewayError
from services.gateway.app.l3.client import L3Client
from tests.obligations_data.test_demo_api import SERVICE_TOKEN, clean_database  # noqa: F401
from tests.obligations_data.test_demo_event_flow import _app, _execute, _register
from tests.obligations_data.test_split_exec import _prepare_execute, _split_steps


def test_g2_by_id_returns_committed_progress_and_rejects_foreign_customer(obligations_db):
    async def run():
        app, admin, runtime = await _app(obligations_db)
        gateway_l3 = L3Client("http://bank", SERVICE_TOKEN, transport=httpx.ASGITransport(app=app))
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://bank"
            ) as client:
                await _prepare_execute(
                    client,
                    "pln_g2_split",
                    "hash_g2_split",
                    _split_steps(),
                    expected_versions={"cus_zs": 0},
                )
                async with admin.connect() as conn:
                    item = (
                        (
                            await conn.execute(
                                select(m.split_items).order_by(m.split_items.c.amount_cents.desc())
                            )
                        )
                        .mappings()
                        .first()
                    )
                registered, _ = await _register(
                    client,
                    "SPLIT_PAYMENT",
                    split_item_id=item["id"],
                    amount_cents=item["amount_cents"],
                )
                assert registered.status_code == 200
                executed = await _execute(client, registered.json()["event_id"])
                assert executed.status_code == 200 and executed.json()["status"] == "SUCCESS"
                args = {"request_id": item["request_id"], "cursor": None, "limit": 50}
                envelope = await fetch_read(gateway_l3, "cus_zs", "get_split_status", args)
                assert len(envelope.data["items"]) == 1
                split = envelope.data["items"][0]
                assert split["id"] == item["request_id"]
                assert split["received_cents"] == item["amount_cents"]
                assert (
                    split["outstanding_cents"]
                    == split["total_cents"] - split["self_share_cents"] - item["amount_cents"]
                )
                with pytest.raises(GatewayError) as error:
                    await fetch_read(gateway_l3, "cus_ls", "get_split_status", args)
                assert error.value.status == 404 and error.value.code is Code.NOT_FOUND
        finally:
            await gateway_l3.close()
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(run())
