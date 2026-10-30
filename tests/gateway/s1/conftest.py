"""S1 `/internal/scheduled/authorize` DB fixtures.

Builds the internal bank-net FastAPI app wired to a tmpfs PostgreSQL so HTTP
tests exercise the real S service-token auth and persistence, with L3 replaced
by the stateful :class:`FakeL3` double (wrapped in a real :class:`L3Client`
transport so the handler's L3 calls flow through the normal client).

The app is built inside each test's ``asyncio.run()`` (not in the fixture) so
the DB engine and the request share the same event loop.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.pool import NullPool

from services.gateway.app import tables as t
from services.gateway.app.config import Settings
from services.gateway.app.db import engine
from services.gateway.app.l3.client import L3Client
from services.gateway.app.l3.fake import FakeL3
from services.gateway.app.main import build_internal_app
from services.gateway.app.runtime import GatewayRuntime, _key_pair, _validate_approval_keys
from tests.gateway.db.conftest import gateway_db  # noqa: F401

WALL = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone(timedelta(hours=8)))
HMAC_KEY = b"s1-approval-hmac-key-at-least-32-bytes-long!"
OTP_KEY = b"s1-otp-hmac-key-at-least-32-bytes-long-now!"
SCHEDULER_TOKEN = "test-scheduler-token-32bytes-xxx"


def _generate_key_pair() -> tuple[bytes, bytes]:
    private = ed25519.Ed25519PrivateKey.generate()
    public = private.public_key()
    private_pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = public.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


@dataclass
class TestApp:
    client: httpx.AsyncClient
    db_url: str
    admin_url: str
    public_key_pem: bytes
    private_key_pem: bytes
    fake: FakeL3
    l3: L3Client

    def scheduler_headers(self) -> dict[str, str]:
        return {"X-Service-Token": SCHEDULER_TOKEN}


@dataclass
class AppConfig:
    gateway_db: any
    private_pem: bytes
    public_pem: bytes


async def build_app(
    config: AppConfig, *, truncate: bool = True, demo_mode: bool = False
) -> TestApp:
    """Build the internal app wired to the tmpfs PG. MUST be called in asyncio.run()."""
    gw_db = config.gateway_db
    private_pem = config.private_pem
    public_pem = config.public_pem

    tmpdir = tempfile.mkdtemp(prefix="sbg-s1-")
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
        demo_console_token=None,
        public_bind="127.0.0.1",
        internal_bind="127.0.0.1",
        max_executions=8,
        database_url=gw_db.admin_url,
        l3_base_url="http://localhost:1",
        gateway_service_token="test-gateway-service-token-32b!",
        service_tokens={
            "F": "test-frontend-service-token-32b",
            "A": "test-agent-service-token-32bytes",
            "S": SCHEDULER_TOKEN,
            "D": "",
        },
        jwt_private_key_path=str(private_path),
        jwt_public_key_path=str(public_path),
        demo_pins_file="/nonexistent/test-pins",
        approval_hmac_key=HMAC_KEY.decode(),
        otp_hmac_key=OTP_KEY.decode(),
    )
    settings.validate()

    test_env = {
        "FRONTEND_SERVICE_TOKEN": "test-frontend-service-token-32b",
        "AGENT_SERVICE_TOKEN": "test-agent-service-token-32bytes",
        "SCHEDULER_SERVICE_TOKEN": SCHEDULER_TOKEN,
        "DEMO_CONSOLE_TOKEN": "",
    }

    private_bytes, public_bytes = _key_pair(settings)
    approval_key_bytes, otp_key_bytes = _validate_approval_keys(settings)

    db = engine(settings.database_url, poolclass=NullPool)
    fake = FakeL3(token=settings.gateway_service_token)
    l3 = L3Client(settings.l3_base_url, settings.gateway_service_token, transport=fake.transport())

    rt = GatewayRuntime(
        settings=settings,
        db=db,
        l3=l3,
        wall_now=lambda: WALL,
        jwt_private_key=private_bytes,
        jwt_public_key=public_bytes,
        pin_hashes={},
        approval_hmac_key=approval_key_bytes,
        otp_hmac_key=otp_key_bytes,
    )

    app = build_internal_app(settings, env=test_env, public_key=public_pem, runtime=rt)

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
        fake=fake,
        l3=l3,
    )


@pytest.fixture
def app_config(gateway_db):  # noqa: F811
    private_pem, public_pem = _generate_key_pair()
    return AppConfig(gateway_db=gateway_db, private_pem=private_pem, public_pem=public_pem)


# ── Seed helpers ──────────────────────────────────────────────────────────────


async def _seed_event_decision(
    conn,
    *,
    event_id: str,
    status: str,
    code: str | None,
    decided_at: datetime | None = None,
) -> None:
    _at = decided_at if decided_at is not None else WALL - timedelta(minutes=5)
    await conn.execute(
        pg_insert(t.event_decisions)
        .values(event_id=event_id, status=status, code=code, decided_at_wall=_at)
        .on_conflict_do_nothing()
    )


async def _seed_grant(
    conn,
    *,
    instruction_id: str,
    customer_id: str = "cus_a",
    source_plan_id: str = "plan_sched_1",
    scope_json: dict | None = None,
    scope_hash: str = "s" * 64,
    approved_risk: str = "YELLOW",
    status: str = "ACTIVE",
    created_at: datetime | None = None,
    revoked_at: datetime | None = None,
) -> None:
    _created = created_at if created_at is not None else WALL - timedelta(days=1)
    _scope = scope_json if scope_json is not None else {"transfers": []}
    await conn.execute(
        pg_insert(t.scheduled_grants)
        .values(
            instruction_id=instruction_id,
            customer_id=customer_id,
            source_plan_id=source_plan_id,
            scope_json=_scope,
            scope_hash=scope_hash,
            approved_risk=approved_risk,
            status=status,
            created_at=_created,
            revoked_at=revoked_at,
        )
        .on_conflict_do_nothing()
    )


def make_event_def(
    *,
    event_id: str,
    customer_id: str = "cus_a",
    account_id: str = "acc_a",
    to_account_id: str | None = None,
    payee_id: str | None = None,
    amount_cents: int = 80000,
    source_id: str = "ins_sched_1",
    source_plan_id: str = "plan_sched_1",
    grant_scope_hash: str = "s" * 64,
    payload_hash: str = "p" * 64,
    due_at: str | None = None,
    status: str = "DUE",
    kind: str = "scheduled_transfer",
) -> dict:
    """A fake immutable L3 event definition (progress-file wire shape)."""
    return {
        "event_id": event_id,
        "kind": kind,
        "customer_id": customer_id,
        "account_id": account_id,
        "to_account_id": to_account_id,
        "payee_id": payee_id,
        "amount_cents": amount_cents,
        "source_id": source_id,
        "source_plan_id": source_plan_id,
        "grant_scope_hash": grant_scope_hash,
        "payload_hash": payload_hash,
        "due_at": due_at if due_at is not None else WALL.isoformat(),
        "status": status,
    }
