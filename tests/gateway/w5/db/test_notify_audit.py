"""G11 / G12 / G13 / G16 on a real temporary PostgreSQL.

Audit rows are written through the real hash chain; every read is scoped to the
caller's customer, and secrets in audit payloads never leave the gateway.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.pool import NullPool

from services.gateway.app import tables as t
from services.gateway.app.audit import chain
from services.gateway.app.db import engine
from tests.gateway.w4b2b.db.conftest import WALL, _runtime_db, _seed_auth, build_app


async def _seed_audit(app, customer, n, *, plan_id=None, payload=None):
    db = _runtime_db(app)
    async with db.begin() as conn:
        for i in range(n):
            await chain.append(
                conn,
                customer_id=customer,
                event_type="PLAN_EVALUATED",
                payload=payload or {"i": i, "code": "OK", "message": f"m{i}"},
                event_id=f"evt_{customer}_{plan_id}_{i}",
                plan_id=plan_id,
                now_wall=WALL + timedelta(minutes=i),
            )
    await db.dispose()


async def _seed_notification(app, nid, customer, *, read=False):
    admin = engine(app.admin_url, poolclass=NullPool)
    async with admin.begin() as conn:
        await conn.execute(
            t.notifications.insert().values(
                id=nid,
                customer_id=customer,
                event_id="evt_" + nid,
                type="PLAN_EXECUTED",
                payload={"title": "转账已完成", "body": "100.00 元", "related_plan_id": "pln_1"},
                created_at=WALL,
                read_at_wall=WALL if read else None,
            )
        )
    await admin.dispose()


async def _app(app_config):
    app = await build_app(app_config)
    db = _runtime_db(app)
    async with db.begin() as conn:
        await _seed_auth(conn)
    await db.dispose()
    return app


def test_customer_audit_is_scoped_ordered_and_paginated(app_config):
    async def run():
        app = await _app(app_config)
        await _seed_audit(app, "cus_zs", 3)
        await _seed_audit(app, "cus_ls", 2)
        first = await app.client.get("/audit", params={"limit": 2}, headers=app.auth_headers())
        assert first.status_code == 200
        page = first.json()
        assert [i["payload"]["i"] for i in page["items"]] == [2, 1]
        assert page["next_cursor"] is not None
        rest = await app.client.get(
            "/audit",
            params={"limit": 2, "cursor": page["next_cursor"]},
            headers=app.auth_headers(),
        )
        tail = rest.json()
        assert [i["payload"]["i"] for i in tail["items"]] == [0]
        assert tail["next_cursor"] is None
        ids = {i["id"] for i in page["items"] + tail["items"]}
        assert all("cus_zs" in i for i in ids)
        item = page["items"][0]
        assert item["prev_hash"] and item["hash"] and item["created_at_wall"]

    asyncio.run(run())


def test_customer_audit_time_window_and_bad_input(app_config):
    async def run():
        app = await _app(app_config)
        await _seed_audit(app, "cus_zs", 3)
        start = (WALL + timedelta(minutes=1)).isoformat()
        resp = await app.client.get(
            "/audit", params={"from_at_wall": start}, headers=app.auth_headers()
        )
        assert [i["payload"]["i"] for i in resp.json()["items"]] == [2, 1]
        for params in ({"from_at_wall": "yesterday"}, {"to_at_wall": "2026-10-15T09:00:00"}):
            bad = await app.client.get("/audit", params=params, headers=app.auth_headers())
            assert bad.status_code == 422
        bad = await app.client.get("/audit", params={"cursor": "x"}, headers=app.auth_headers())
        assert bad.status_code == 422

    asyncio.run(run())


def test_audit_payload_secrets_are_redacted(app_config):
    async def run():
        app = await _app(app_config)
        payload = {"code": "OTP_SENT", "otp_code": "123456", "nested": {"api_token": "t", "ok": 1}}
        await _seed_audit(app, "cus_zs", 1, payload=payload)
        item = (await app.client.get("/audit", headers=app.auth_headers())).json()["items"][0]
        assert item["payload"] == {"code": "OTP_SENT", "nested": {"ok": 1}}
        assert "123456" not in str(item)

    asyncio.run(run())


def test_plan_audit_only_returns_that_plan_for_the_caller(app_config):
    async def run():
        app = await _app(app_config)
        await _seed_audit(app, "cus_zs", 2, plan_id="pln_mine")
        await _seed_audit(app, "cus_zs", 1, plan_id="pln_other")
        await _seed_audit(app, "cus_ls", 2, plan_id="pln_theirs")
        mine = await app.client.get("/plans/pln_mine/audit", headers=app.auth_headers())
        assert mine.status_code == 200
        assert {i["plan_id"] for i in mine.json()["items"]} == {"pln_mine"}
        assert len(mine.json()["items"]) == 2
        theirs = await app.client.get("/plans/pln_theirs/audit", headers=app.auth_headers())
        assert theirs.status_code == 200
        assert theirs.json()["items"] == []

    asyncio.run(run())


def test_notifications_list_unread_count_and_scope(app_config):
    async def run():
        app = await _app(app_config)
        await _seed_notification(app, "ntf_1", "cus_zs")
        await _seed_notification(app, "ntf_2", "cus_zs", read=True)
        await _seed_notification(app, "ntf_3", "cus_ls")
        resp = await app.client.get("/notifications", headers=app.auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert {i["id"] for i in data["items"]} == {"ntf_1", "ntf_2"}
        assert data["unread_count"] == 1
        item = next(i for i in data["items"] if i["id"] == "ntf_1")
        assert item["title"] == "转账已完成" and item["related_plan_id"] == "pln_1"
        assert item["read_at_wall"] is None
        unread = await app.client.get(
            "/notifications", params={"unread_only": "true"}, headers=app.auth_headers()
        )
        assert [i["id"] for i in unread.json()["items"]] == ["ntf_1"]

    asyncio.run(run())


def test_mark_read_is_idempotent_audited_once_and_owner_only(app_config):
    async def run():
        app = await _app(app_config)
        await _seed_notification(app, "ntf_1", "cus_zs")
        await _seed_notification(app, "ntf_3", "cus_ls")
        body = {"request_id": "req_read_1"}
        first = await app.client.post(
            "/notifications/ntf_1/read", json=body, headers=app.auth_headers()
        )
        assert first.status_code == 200
        assert first.json()["id"] == "ntf_1" and first.json()["read_at_wall"]
        again = await app.client.post(
            "/notifications/ntf_1/read", json=body, headers=app.auth_headers()
        )
        assert again.status_code == 200 and again.json() == first.json()
        data = (await app.client.get("/notifications", headers=app.auth_headers())).json()
        assert data["unread_count"] == 0
        db = _runtime_db(app)
        async with db.connect() as conn:
            count = await conn.scalar(
                select(func.count())
                .select_from(t.audit_events)
                .where(t.audit_events.c.event_type == "NOTIFICATION_READ")
            )
            other = await conn.scalar(
                select(t.notifications.c.read_at_wall).where(t.notifications.c.id == "ntf_3")
            )
        await db.dispose()
        assert count == 1
        foreign = await app.client.post(
            "/notifications/ntf_3/read", json={"request_id": "req_x"}, headers=app.auth_headers()
        )
        assert foreign.status_code == 404
        assert other is None

    asyncio.run(run())


def test_endpoints_require_app_caller(app_config):
    async def run():
        app = await _app(app_config)
        for path in ("/audit", "/notifications", "/plans/pln_1/audit"):
            resp = await app.client.get(path)
            assert resp.status_code == 401

    asyncio.run(run())
