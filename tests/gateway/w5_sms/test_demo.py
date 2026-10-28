"""D0 / D5 demo-console endpoint tests (real tmpfs PostgreSQL)."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.pool import NullPool

from services.gateway.app import tables as t
from services.gateway.app.auth.matrix import CONSOLE_AUD
from services.gateway.app.auth.tokens import APP_AUDIENCE, sign_jwt
from services.gateway.app.db import engine
from tests.gateway.w4b2c.db.conftest import _seed_otp_challenge
from tests.gateway.w5_sms.conftest import (
    DEMO_TOKEN,
    SERVICE_TOKEN,
    _real_now,
    build_demo_app,
    seed_red_chain,
)

VIEW_HASH = "b" * 64


def _d_headers(token: str | None = None) -> dict:
    headers = {"X-Service-Token": DEMO_TOKEN}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def test_demo_routes_404_when_demo_off(app_config):
    async def go():
        app = await build_demo_app(app_config, demo_mode=False)
        r0 = await app.client.post(
            "/demo/auth/login", json={"secret": DEMO_TOKEN}, headers=_d_headers()
        )
        r5 = await app.client.get(
            "/demo/sms", params={"approval_id": "apv_1"}, headers=_d_headers()
        )
        assert r0.status_code == 404
        assert r5.status_code == 404

    asyncio.run(go())


def test_d0_login_ok(app_config):
    async def go():
        app = await build_demo_app(app_config)
        r = await app.client.post(
            "/demo/auth/login", json={"secret": DEMO_TOKEN}, headers=_d_headers()
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["token"]
        assert body["expires_at_wall"]

    asyncio.run(go())


def test_d0_login_wrong_secret_401(app_config):
    async def go():
        app = await build_demo_app(app_config)
        r = await app.client.post(
            "/demo/auth/login", json={"secret": "wrong-secret-xxxxxxxx"}, headers=_d_headers()
        )
        assert r.status_code == 401

    asyncio.run(go())


def test_d5_no_token_401(app_config):
    async def go():
        app = await build_demo_app(app_config)
        r = await app.client.get(
            "/demo/sms", params={"approval_id": "apv_1"}, headers=_d_headers()
        )
        assert r.status_code == 401

    asyncio.run(go())


def test_d5_wrong_aud_401(app_config):
    async def go():
        app = await build_demo_app(app_config)
        jwt = sign_jwt(
            app.private_key_pem, sub="cus_zs", aud=APP_AUDIENCE, scope=["app"], jti="jti-x"
        )
        r = await app.client.get(
            "/demo/sms", params={"approval_id": "apv_1"}, headers=_d_headers(jwt)
        )
        assert r.status_code == 401

    asyncio.run(go())


def test_d5_expired_token_401(app_config):
    async def go():
        app = await build_demo_app(app_config)
        now = _real_now()
        jwt = sign_jwt(
            app.private_key_pem,
            sub="demo-console",
            aud=CONSOLE_AUD,
            scope=["console"],
            now=int((now - timedelta(hours=2)).timestamp()),
            ttl=60,
        )
        r = await app.client.get(
            "/demo/sms", params={"approval_id": "apv_1"}, headers=_d_headers(jwt)
        )
        assert r.status_code == 401

    asyncio.run(go())


def test_d5_expired_challenge_404(app_config):
    async def go():
        app = await build_demo_app(app_config)
        now = _real_now()
        db = engine(app.db_url, poolclass=NullPool)
        async with db.begin() as conn:
            await seed_red_chain(conn, now=now, view_hash=VIEW_HASH)
            await _seed_otp_challenge(
                conn,
                approval_id="apv_1",
                view_hash=VIEW_HASH,
                expires_at_wall=now - timedelta(minutes=1),
                last_sent_at_wall=now,
            )
        await db.dispose()

        d0 = await app.client.post(
            "/demo/auth/login", json={"secret": DEMO_TOKEN}, headers=_d_headers()
        )
        token = d0.json()["token"]
        r = await app.client.get(
            "/demo/sms", params={"approval_id": "apv_1"}, headers=_d_headers(token)
        )
        assert r.status_code == 404

    asyncio.run(go())


def test_g10_d5_g6_full_flow(app_config, caplog):
    async def go():
        app = await build_demo_app(app_config)
        now = _real_now()
        db = engine(app.db_url, poolclass=NullPool)
        async with db.begin() as conn:
            await seed_red_chain(conn, now=now, jti="jti-w5-f", view_hash=VIEW_HASH)
        await db.dispose()

        f_jwt = sign_jwt(
            app.private_key_pem,
            sub="cus_zs",
            aud=APP_AUDIENCE,
            scope=["app", "approve"],
            jti="jti-w5-f",
        )
        f_headers = {"X-Service-Token": SERVICE_TOKEN, "Authorization": f"Bearer {f_jwt}"}

        g10 = await app.client.post(
            "/approvals/apv_1/otp",
            json={"request_id": "req-otp-1", "view_hash": VIEW_HASH},
            headers=f_headers,
        )
        assert g10.status_code == 200, g10.text

        d0 = await app.client.post(
            "/demo/auth/login", json={"secret": DEMO_TOKEN}, headers=_d_headers()
        )
        assert d0.status_code == 200, d0.text
        console_token = d0.json()["token"]

        caplog.set_level(logging.DEBUG)
        caplog.clear()
        d5 = await app.client.get(
            "/demo/sms", params={"approval_id": "apv_1"}, headers=_d_headers(console_token)
        )
        assert d5.status_code == 200, d5.text
        code = d5.json()["code"]
        assert code.isdigit() and len(code) == 6

        db2 = engine(app.db_url, poolclass=NullPool)
        async with db2.connect() as conn:
            rows = (
                await conn.execute(
                    select(t.audit_events.c.payload).where(t.audit_events.c.customer_id == "cus_zs")
                )
            ).scalars().all()
        await db2.dispose()
        for payload in rows:
            assert code not in json.dumps(payload, ensure_ascii=False)
        assert code not in caplog.text

        g6 = await app.client.post(
            "/approvals/apv_1/confirm",
            json={"request_id": "req-g6-1", "view_hash": VIEW_HASH, "risk_ack": True, "otp": code},
            headers=f_headers,
        )
        assert g6.status_code == 200, g6.text
        assert g6.json()["status"] == "CONFIRMED"

        d5_after = await app.client.get(
            "/demo/sms", params={"approval_id": "apv_1"}, headers=_d_headers(console_token)
        )
        assert d5_after.status_code == 404

    asyncio.run(go())
