"""S1 result producers persist the actual customer notification in the same tx."""

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.pool import NullPool

from services.gateway.app import tables as t
from services.gateway.app.db import engine
from tests.gateway.s1.conftest import build_app, make_event_def


@pytest.mark.parametrize(
    "kind,status,notification",
    [
        ("FUND_CONFIRM", "SUCCESS", "FUND_RESULT"),
        ("FUND_SETTLE", "SUCCESS", "FUND_RESULT"),
        ("FUND_EXPIRY", "FAILED_INSUFFICIENT", "FUND_RESULT"),
        ("SCHEDULED_TRANSFER", "BLOCKED", "SCHEDULE_RESULT"),
        ("MERCHANT_CHARGE", "BLOCKED", "CHARGE_BLOCKED"),
        ("REMINDER", "SUCCESS", "CHARGE_UPCOMING"),
    ],
)
def test_s1_recovers_committed_event_and_emits_once(app_config, kind, status, notification):
    async def run():
        app = await build_app(app_config)
        eid = "evt_notification_" + kind
        execution = {"status": status, "result": {"order_status": "SETTLED"}}
        app.fake.set_event(make_event_def(event_id=eid, kind=kind) | {"execution": execution})
        app.fake.event_executions[eid] = execution
        response = await app.client.post(
            "/internal/scheduled/authorize", json={"event_id": eid}, headers=app.scheduler_headers()
        )
        assert response.status_code == 200 and response.json()["status"] == status, response.text
        replay = await app.client.post(
            "/internal/scheduled/authorize", json={"event_id": eid}, headers=app.scheduler_headers()
        )
        assert replay.status_code == 200
        db = engine(app.db_url, poolclass=NullPool)
        async with db.connect() as conn:
            notifications = (
                (
                    await conn.execute(
                        select(t.notifications).where(t.notifications.c.event_id == eid)
                    )
                )
                .mappings()
                .all()
            )
            assert len(notifications) == 1
            assert (
                notifications[0]["type"] == notification
                and notifications[0]["customer_id"] == "cus_a"
            )
        await db.dispose()

    asyncio.run(run())
