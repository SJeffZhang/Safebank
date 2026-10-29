"""W5-sms D0/D5 demo-console fixtures.

Self-contained real-clock + demo-mode variant of the W4b2b tmpfs-PG fixture.
The fixed W4b2b ``WALL`` clock would expire the D0 console JWT instantly and
break the OTP AEAD ``wall_now`` round-trip, so the gateway clock is real UTC.
``demo_mode=True`` mounts the D0/D5 routes; the demo-console secret, the D
service token and ``DEMO_CONSOLE_TOKEN`` are all the same >=32-byte value.
"""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from safebank_contracts.enums import Risk
from sqlalchemy import text
from sqlalchemy.pool import NullPool

from services.gateway.app import tables as t
from services.gateway.app.config import Settings
from services.gateway.app.db import engine
from services.gateway.app.l3.client import L3Client
from services.gateway.app.main import build_public_app
from services.gateway.app.runtime import GatewayRuntime, _key_pair, _validate_approval_keys
from tests.gateway.db.conftest import gateway_db  # noqa: F401
from tests.gateway.w4b2b.db.conftest import (  # noqa: F401
    HMAC_KEY,
    OTP_KEY,
    SERVICE_TOKEN,
    TestApp,
    _seed_full,
    app_config,
)
from tests.gateway.w4b2c.db.conftest import _seed_otp_challenge  # noqa: F401

DEMO_TOKEN = "test-demo-console-token-32bytes-!"


def _real_now() -> datetime:
    return datetime.now(UTC)


async def build_demo_app(config, *, demo_mode: bool = True, truncate: bool = True) -> TestApp:
    """Build the gateway test app with demo routes mounted and a real wall clock."""
    gw_db = config.gateway_db
    private_pem = config.private_pem
    public_pem = config.public_pem

    tmpdir = tempfile.mkdtemp(prefix="sbg-w5sms-")
    private_path = Path(tmpdir) / "jwt-private.pem"
    public_path = Path(tmpdir) / "jwt-public.pem"
    private_path.write_bytes(private_pem)
    public_path.write_bytes(public_pem)
    os.chmod(private_path, 0o600)
    os.chmod(public_path, 0o644)

    settings = Settings(
        demo_mode=demo_mode,
        eval_mode=False,
        profile="B2",
        demo_console_token=DEMO_TOKEN if demo_mode else None,
        public_bind="127.0.0.1",
        internal_bind="127.0.0.1",
        max_executions=8,
        database_url=gw_db.admin_url,
        l3_base_url="http://localhost:1",
        gateway_service_token="test-gateway-service-token-32b!",
        service_tokens={
            "F": SERVICE_TOKEN,
            "A": "test-agent-service-token-32bytes",
            "S": "test-scheduler-token-32bytes-xxx",
            "D": DEMO_TOKEN if demo_mode else "",
        },
        jwt_private_key_path=str(private_path),
        jwt_public_key_path=str(public_path),
        demo_pins_file="/nonexistent/test-pins",
        approval_hmac_key=HMAC_KEY.decode(),
        otp_hmac_key=OTP_KEY.decode(),
    )
    settings.validate()

    test_env = {
        "FRONTEND_SERVICE_TOKEN": SERVICE_TOKEN,
        "AGENT_SERVICE_TOKEN": "test-agent-service-token-32bytes",
        "SCHEDULER_SERVICE_TOKEN": "test-scheduler-token-32bytes-xxx",
        "DEMO_CONSOLE_TOKEN": DEMO_TOKEN if demo_mode else "",
    }

    private_bytes, public_bytes = _key_pair(settings)
    approval_key_bytes, otp_key_bytes = _validate_approval_keys(settings)

    db = engine(settings.database_url, poolclass=NullPool)
    l3 = L3Client(settings.l3_base_url, settings.gateway_service_token)

    rt = GatewayRuntime(
        settings=settings,
        db=db,
        l3=l3,
        wall_now=_real_now,
        jwt_private_key=private_bytes,
        jwt_public_key=public_bytes,
        pin_hashes={},
        approval_hmac_key=approval_key_bytes,
        otp_hmac_key=otp_key_bytes,
    )

    app = build_public_app(settings, env=test_env, public_key=public_pem, runtime=rt)
    app.state.wall_now = _real_now

    if truncate:
        async with db.begin() as conn:
            names = ",".join(t.metadata.tables)
            await conn.execute(text(f"TRUNCATE {names} CASCADE"))

    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")

    return TestApp(
        client=client,
        db_url=gw_db.admin_url,
        admin_url=gw_db.admin_url,
        public_key_pem=public_pem,
        private_key_pem=private_pem,
        wall_now_fn=_real_now,
    )


async def seed_red_chain(
    conn,
    *,
    now: datetime,
    sid: str = "ses_1",
    customer: str = "cus_zs",
    approval_id: str = "apv_1",
    plan_id: str = "pln_1",
    evaluation_id: str = "evl_1",
    view_hash: str = "b" * 64,
    jti: str = "jti-w5-f",
) -> None:
    """Seed a RED PENDING-APPROVAL chain anchored to real time.

    The W4b2b ``_seed_full`` seeds auth_sessions with a fixed (now-past) WALL
    expiry, so afterwards we re-anchor the auth row to real now.  The approval
    expiry is seeded directly from *now* and its binding MAC is recomputed by
    ``_seed_full`` for the same value.
    """
    await _seed_full(
        conn,
        sid=sid,
        customer=customer,
        plan_id=plan_id,
        evaluation_id=evaluation_id,
        approval_id=approval_id,
        plan_status="PENDING_APPROVAL",
        approval_status="PENDING",
        risk=Risk.RED,
        view_hash=view_hash,
        view_json={"plan_id": plan_id, "risk_ack_required": True, "otp_required": True},
        expires_at_wall=now + timedelta(minutes=5),
        jti=jti,
        hmac_key=HMAC_KEY,
    )
    await conn.execute(
        text("UPDATE auth_sessions SET expires_at_wall = :e WHERE jti = :j"),
        {"e": now + timedelta(hours=1), "j": jti},
    )
