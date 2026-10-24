"""W4b2b DB fixtures: isolated temporary PostgreSQL with test FastAPI app.

Builds a minimal FastAPI app wired to the tmpfs PostgreSQL so HTTP endpoint
tests exercise the real auth middleware, state transitions, and audit.

The app is built inside each test's ``asyncio.run()`` (not in the fixture) so
the DB engine and the request share the same event loop — asyncpg connections
do not survive cross-loop, even with NullPool.
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
from safebank_contracts.enums import Risk
from sqlalchemy import text
from sqlalchemy.pool import NullPool

from services.gateway.app import tables as t
from services.gateway.app.approval.service import ApprovalBinding, binding_mac
from services.gateway.app.auth.tokens import APP_AUDIENCE, sign_jwt
from services.gateway.app.config import Settings
from services.gateway.app.db import engine
from services.gateway.app.l3.client import L3Client
from services.gateway.app.main import build_public_app
from services.gateway.app.runtime import GatewayRuntime, _key_pair, _validate_approval_keys
from services.gateway.app.schemas.decision import DecisionValue
from tests.gateway.db.conftest import gateway_db  # noqa: F401

# ── Test constants ────────────────────────────────────────────────────────────

WALL = datetime(2026, 10, 15, 9, 0, 0, tzinfo=timezone(timedelta(hours=8)))
HMAC_KEY = b"w4b2b-approval-hmac-key-at-least-32-bytes-long"
OTP_KEY = b"w4b2b-otp-hmac-key-at-least-32-bytes-long-now"
SERVICE_TOKEN = "test-frontend-service-token-32b"
AGENT_TOKEN = "test-agent-service-token-32bytes"
SCHEDULER_TOKEN = "test-scheduler-token-32bytes-xxx"

_PREFLIGHT_JSON = {
    "executable": True,
    "steps": [],
    "balances": [],
    "holdings": [],
    "scheduled_reserve_cents": 0,
}
_FINDINGS_JSON: list = []
_RESOLVED_STEPS: list = []
_UTTERANCE_IDS: list = ["utt_1"]
_SCOPE_IDS: list = ["utt_1"]
_VERSIONS: dict = {"cus_zs": 1}


# ── Ed25519 key pair ──────────────────────────────────────────────────────────


def _generate_key_pair() -> tuple[bytes, bytes]:
    """Generate a fresh Ed25519 key pair for tests (never read from disk)."""
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


# ── Test FastAPI app ──────────────────────────────────────────────────────────


@dataclass
class TestApp:
    """Bundled test application with client, keys, and seed helpers."""

    client: httpx.AsyncClient
    db_url: str  # admin URL (postgres role, same as what the app connects with)
    admin_url: str  # admin URL for TRUNCATE
    public_key_pem: bytes
    private_key_pem: bytes
    wall_now_fn: any

    def jwt(
        self, *, customer_id: str = "cus_zs", scope: list[str] | None = None, jti: str = "test-jti"
    ) -> str:
        """Generate an app JWT for a test customer (deterministic jti by default)."""
        scopes = scope if scope is not None else ["app", "approve"]
        return sign_jwt(
            self.private_key_pem,
            sub=customer_id,
            aud=APP_AUDIENCE,
            scope=scopes,
            jti=jti,
        )

    def auth_headers(
        self,
        *,
        customer_id: str = "cus_zs",
        scope: list[str] | None = None,
        jti: str | None = None,
    ) -> dict:
        """HTTP headers for an authenticated F (frontend) caller."""
        _jti = jti if jti is not None else "test-jti"
        return {
            "X-Service-Token": SERVICE_TOKEN,
            "Authorization": f"Bearer {self.jwt(customer_id=customer_id, scope=scope, jti=_jti)}",
        }


@dataclass
class AppConfig:
    """Pre-built configuration that :func:`build_app` turns into a :class:`TestApp`.

    This exists so the FastAPI app can be built inside the test's own
    ``asyncio.run()`` — asyncpg connections do not survive across event loops.
    """

    gateway_db: any
    private_pem: bytes
    public_pem: bytes


async def build_app(config: AppConfig, *, truncate: bool = True) -> TestApp:
    """Build a FastAPI test app.

    When *truncate* is True (default), truncates all gateway tables so each
    test starts with a clean slate.  Tests seed their own data via
    ``_seed_full`` or the individual helpers.

    MUST be called inside ``asyncio.run()`` so the DB engine and the ASGI
    request processing share the same event loop.
    """
    gw_db = config.gateway_db
    private_pem = config.private_pem
    public_pem = config.public_pem

    # Write PEM files so _key_pair can load them from disk.
    tmpdir = tempfile.mkdtemp(prefix="sbg-w4b2b-")
    private_path = Path(tmpdir) / "jwt-private.pem"
    public_path = Path(tmpdir) / "jwt-public.pem"
    private_path.write_bytes(private_pem)
    public_path.write_bytes(public_pem)
    os.chmod(private_path, 0o600)
    os.chmod(public_path, 0o644)

    settings = Settings(
        demo_mode=False,
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
            "F": SERVICE_TOKEN,
            "A": AGENT_TOKEN,
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
        "FRONTEND_SERVICE_TOKEN": SERVICE_TOKEN,
        "AGENT_SERVICE_TOKEN": AGENT_TOKEN,
        "SCHEDULER_SERVICE_TOKEN": SCHEDULER_TOKEN,
        "DEMO_CONSOLE_TOKEN": "",
    }

    private_bytes, public_bytes = _key_pair(settings)
    approval_key_bytes, otp_key_bytes = _validate_approval_keys(settings)

    db = engine(settings.database_url, poolclass=NullPool)
    l3 = L3Client(settings.l3_base_url, settings.gateway_service_token)

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

    app = build_public_app(settings, env=test_env, public_key=public_pem, runtime=rt)

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
        wall_now_fn=lambda: WALL,
    )


@pytest.fixture
def app_config(gateway_db):  # noqa: F811
    """Return pre-built config; tests call ``await build_app(config)`` in their
    own ``asyncio.run()`` so the app shares the test's event loop."""
    private_pem, public_pem = _generate_key_pair()
    return AppConfig(
        gateway_db=gateway_db,
        private_pem=private_pem,
        public_pem=public_pem,
    )


# ── Seed helpers ─────────────────────────────────────────────────────────────


async def _seed_session(conn, *, sid="ses_1", customer="cus_zs", revoked=False):
    """Seed a sessions row for the test JWT.

    Uses ON CONFLICT DO NOTHING so explicit ``_seed_session(revoked=True)``
    calls before ``_seed_full`` are preserved rather than overwritten by the
    full-chain defaults.
    """
    from sqlalchemy.dialects.postgresql import insert

    await conn.execute(
        insert(t.sessions)
        .values(
            id=sid,
            customer_id=customer,
            authorization_epoch=0,
            created_at_wall=WALL,
            revoked_at_wall=WALL if revoked else None,
        )
        .on_conflict_do_nothing()
    )


async def _seed_auth(conn, *, jti="test-jti", customer="cus_zs"):
    """Seed an auth_sessions row so check_revocation accepts the test JWT.

    Uses ON CONFLICT DO NOTHING so the default row survives re-seeding by
    ``_seed_full`` after the autouse fixture already inserted it.
    """
    from sqlalchemy.dialects.postgresql import insert

    await conn.execute(
        insert(t.auth_sessions)
        .values(
            jti=jti,
            customer_id=customer,
            created_at_wall=WALL,
            expires_at_wall=WALL + timedelta(hours=2),
        )
        .on_conflict_do_nothing()
    )


async def _seed_plan(
    conn,
    *,
    plan_id="pln_1",
    sid="ses_1",
    customer="cus_zs",
    origin="agent",
    status="EVALUATED",
):
    """Seed a plans row.

    Uses ON CONFLICT DO UPDATE so that tests calling ``_seed_full`` with
    custom parameters (e.g. ``plan_status=CONFIRMED``) override the default
    row seeded by ``_clean_gateway``.
    """
    from sqlalchemy.dialects.postgresql import insert

    await conn.execute(
        insert(t.plans)
        .values(
            id=plan_id,
            customer_id=customer,
            sid=sid,
            origin=origin,
            request_id=f"req_{plan_id}",
            request_hash="r",
            plan_hash="a" * 64,
            steps_hash="s",
            resolved_steps=_RESOLVED_STEPS,
            utterance_ids=_UTTERANCE_IDS,
            scope_ids=_SCOPE_IDS,
            status=status,
            created_at_wall=WALL,
        )
        .on_conflict_do_update(
            index_elements=["id"],
            set_={
                "customer_id": customer,
                "sid": sid,
                "origin": origin,
                "request_id": f"req_{plan_id}",
                "request_hash": "r",
                "plan_hash": "a" * 64,
                "steps_hash": "s",
                "resolved_steps": _RESOLVED_STEPS,
                "utterance_ids": _UTTERANCE_IDS,
                "scope_ids": _SCOPE_IDS,
                "status": status,
                "created_at_wall": WALL,
            },
        )
    )


async def _seed_evaluation(
    conn,
    *,
    plan_id="pln_1",
    evaluation_id="evl_1",
    decision=DecisionValue.REQUIRE_APPROVAL,
    risk=Risk.YELLOW,
    policy_version="sha256:test",
    clock_version=1,
    versions=None,
    revision=1,
    view_json=None,
):
    """Seed an evaluations row.

    Uses ON CONFLICT DO UPDATE so that tests calling ``_seed_full`` with
    custom parameters (e.g. ``risk=RED``, ``view_json=...``) override the
    default row seeded by ``_clean_gateway``.
    """
    from sqlalchemy.dialects.postgresql import insert

    _versions = versions if versions is not None else _VERSIONS

    await conn.execute(
        insert(t.evaluations)
        .values(
            id=evaluation_id,
            plan_id=plan_id,
            revision=revision,
            decision={"decision": decision.value},
            risk=risk.value,
            findings=_FINDINGS_JSON,
            preflight=_PREFLIGHT_JSON,
            versions=_versions,
            policy_version=policy_version,
            clock_version=clock_version,
            view_json=view_json,
            created_at_wall=WALL,
        )
        .on_conflict_do_update(
            index_elements=["id"],
            set_={
                "plan_id": plan_id,
                "revision": revision,
                "decision": {"decision": decision.value},
                "risk": risk.value,
                "findings": _FINDINGS_JSON,
                "preflight": _PREFLIGHT_JSON,
                "versions": _versions,
                "policy_version": policy_version,
                "clock_version": clock_version,
                "view_json": view_json,
                "created_at_wall": WALL,
            },
        )
    )


def _compute_binding(
    *,
    customer_id="cus_zs",
    sid="ses_1",
    plan_id="pln_1",
    plan_hash="a" * 64,
    evaluation_id="evl_1",
    versions=None,
    clock_version=1,
    policy_version="sha256:test",
    profile="B2",
    view_hash,
    expires_at_wall,
) -> str:
    """Compute a valid binding_mac for test seed data."""
    binding = ApprovalBinding(
        customer_id=customer_id,
        sid=sid,
        plan_id=plan_id,
        plan_hash=plan_hash,
        evaluation_id=evaluation_id,
        versions=versions if versions is not None else {"cus_zs": 1},
        clock_version=clock_version,
        policy_version=policy_version,
        profile=profile,
        view_hash=view_hash,
        expires_at_wall=expires_at_wall,
    )
    return binding_mac(HMAC_KEY, binding)


async def _seed_approval(
    conn,
    *,
    approval_id="apv_1",
    plan_id="pln_1",
    evaluation_id="evl_1",
    sid="ses_1",
    customer="cus_zs",
    status="PENDING",
    view_hash="b" * 64,
    expires_at_wall=None,
):
    """Seed an approvals row.

    Uses ON CONFLICT DO UPDATE so that tests calling ``_seed_full`` with
    custom parameters (e.g. ``approval_status=CONFIRMED``,
    ``expires_at_wall=...``) override the default row seeded by
    ``_clean_gateway``.
    """
    from sqlalchemy.dialects.postgresql import insert

    expiry = expires_at_wall if expires_at_wall is not None else WALL + timedelta(minutes=5)
    mac = _compute_binding(
        plan_id=plan_id,
        evaluation_id=evaluation_id,
        sid=sid,
        view_hash=view_hash,
        expires_at_wall=expiry,
    )
    await conn.execute(
        insert(t.approvals)
        .values(
            id=approval_id,
            plan_id=plan_id,
            evaluation_id=evaluation_id,
            customer_id=customer,
            sid=sid,
            plan_hash="a" * 64,
            view_hash=view_hash,
            binding_mac=mac,
            expires_at_wall=expiry,
            status=status,
            risk_ack=False,
        )
        .on_conflict_do_update(
            index_elements=["id"],
            set_={
                "plan_id": plan_id,
                "evaluation_id": evaluation_id,
                "customer_id": customer,
                "sid": sid,
                "plan_hash": "a" * 64,
                "view_hash": view_hash,
                "binding_mac": mac,
                "expires_at_wall": expiry,
                "status": status,
                "risk_ack": False,
            },
        )
    )


async def _seed_full(
    conn,
    *,
    sid="ses_1",
    customer="cus_zs",
    plan_id="pln_1",
    evaluation_id="evl_1",
    approval_id="apv_1",
    origin="agent",
    plan_status="PENDING_APPROVAL",
    approval_status="PENDING",
    decision=DecisionValue.REQUIRE_APPROVAL,
    risk=Risk.YELLOW,
    view_hash="b" * 64,
    view_json=None,
    expires_at_wall=None,
    jti="test-jti",
):
    """Seed a complete auth → session → plan → evaluation → approval chain."""
    _view = view_json if view_json is not None else {"plan_id": plan_id, "risk_ack_required": False}
    await _seed_auth(conn, jti=jti, customer=customer)
    await _seed_session(conn, sid=sid, customer=customer)
    await _seed_plan(
        conn,
        plan_id=plan_id,
        sid=sid,
        customer=customer,
        origin=origin,
        status=plan_status,
    )
    await _seed_evaluation(
        conn,
        plan_id=plan_id,
        evaluation_id=evaluation_id,
        decision=decision,
        risk=risk,
        view_json=_view,
    )
    await _seed_approval(
        conn,
        approval_id=approval_id,
        plan_id=plan_id,
        evaluation_id=evaluation_id,
        sid=sid,
        customer=customer,
        status=approval_status,
        view_hash=view_hash,
        expires_at_wall=expires_at_wall,
    )


def _runtime_db(test_app):
    """Return a SQLAlchemy engine for the runtime (gateway_app) role."""
    return engine(test_app.db_url, poolclass=NullPool)
