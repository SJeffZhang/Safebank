"""W4b2b DB fixtures: isolated temporary PostgreSQL with test FastAPI app.

Builds a minimal FastAPI app wired to the tmpfs PostgreSQL so HTTP endpoint
tests exercise the real auth middleware, state transitions, and audit.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from safebank_contracts.codes import Code
from safebank_contracts.enums import Risk
from sqlalchemy import text
from sqlalchemy.pool import NullPool

from services.gateway.app import tables as t
from services.gateway.app.api.approvals import ConfirmRejectRequest, ConfirmRequest
from services.gateway.app.approval import service as approval_service
from services.gateway.app.approval.planview import build_view
from services.gateway.app.approval.service import ApprovalBinding, binding_mac
from services.gateway.app.auth.tokens import APP_AUDIENCE, sign_jwt
from services.gateway.app.config import Settings
from services.gateway.app.db import engine
from services.gateway.app.main import build_public_app
from services.gateway.app.runtime import GatewayRuntime, gateway_runtime
from services.gateway.app.schemas.decision import Decision, DecisionValue, PreflightView
from services.gateway.app.schemas.enums import ApprovalStatus, PlanStatus
from services.gateway.app.schemas.scope import (
    AuthorizationInputs,
    NormalizedScope,
    UtteranceRef,
)
from tests.gateway.db.conftest import gateway_db  # noqa: F401
from tests.gateway.w4b1.conftest import (
    configure_approval,
    state,
    transfer_step,
)

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
    db_url: str  # runtime (gateway_app) URL
    admin_url: str  # admin URL for TRUNCATE
    public_key_pem: bytes
    private_key_pem: bytes
    wall_now_fn: any

    def jwt(self, *, customer_id: str = "cus_zs", scope: list[str] | None = None) -> str:
        """Generate an app JWT for a test customer."""
        scopes = scope if scope is not None else ["app", "approve"]
        return sign_jwt(
            self.private_key_pem,
            sub=customer_id,
            aud=APP_AUDIENCE,
            scope=scopes,
        )

    def auth_headers(self, *, customer_id: str = "cus_zs", scope: list[str] | None = None) -> dict:
        """HTTP headers for an authenticated F (frontend) caller."""
        return {
            "X-Service-Token": SERVICE_TOKEN,
            "Authorization": f"Bearer {self.jwt(customer_id=customer_id, scope=scope)}",
        }


@pytest.fixture(scope="session")
def test_app(gateway_db):
    """Build a FastAPI test app wired to the tmpfs PostgreSQL.

    Session-scoped: one app for all tests in this session.
    """
    private_pem, public_pem = _generate_key_pair()

    settings = Settings(
        demo_mode=False,
        eval_mode=False,
        profile="B2",
        demo_console_token=None,
        public_bind="127.0.0.1",
        internal_bind="127.0.0.1",
        max_executions=8,
        database_url=gateway_db.runtime_url,
        l3_base_url="http://localhost:1",  # unreachable — endpoints under test don't call L3
        gateway_service_token="test-gateway-service-token-32b!",
        service_tokens={
            "F": SERVICE_TOKEN,
            "A": AGENT_TOKEN,
            "S": SCHEDULER_TOKEN,
            "D": "",
        },
        jwt_private_key_path="/nonexistent/test-private.pem",  # not read — we pass keys directly
        jwt_public_key_path="/nonexistent/test-public.pem",
        demo_pins_file="/nonexistent/test-pins",
        approval_hmac_key=HMAC_KEY.decode(),
        otp_hmac_key=OTP_KEY.decode(),
    )
    settings.validate()

    async def _build():
        async with gateway_runtime(
            settings,
            db_factory=lambda url: engine(url, poolclass=NullPool),
            wall_now=lambda: WALL,
            pin_hashes={},
        ) as rt:
            app = build_public_app(settings, public_key=public_pem, runtime=rt)
            # approval_service.configure() for tests that call library directly
            approval_service.configure(HMAC_KEY, profile="B2", wall_now=lambda: WALL)
            transport = httpx.ASGITransport(app=app)
            client = httpx.AsyncClient(transport=transport, base_url="http://test")
            return TestApp(
                client=client,
                db_url=gateway_db.runtime_url,
                admin_url=gateway_db.admin_url,
                public_key_pem=public_pem,
                private_key_pem=private_pem,
                wall_now_fn=lambda: WALL,
            )

    app = asyncio.run(_build())
    yield app
    asyncio.run(app.client.aclose())


# ── Per-test cleanup ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_gateway(test_app):
    """Truncate all gateway tables before each test so seed IDs never collide."""
    async def truncate():
        admin_db = engine(test_app.admin_url, poolclass=NullPool)
        async with admin_db.begin() as conn:
            names = ",".join(t.metadata.tables)
            await conn.execute(text(f"TRUNCATE {names} CASCADE"))
        await admin_db.dispose()

    asyncio.run(truncate())
    # Reconfigure approval_service after truncate (global state was reset)
    approval_service.configure(HMAC_KEY, profile="B2", wall_now=lambda: WALL)


# ── Seed helpers (mirror W4b1 but compatible with W4b2b needs) ────────────────


async def _seed_session(conn, *, sid="ses_1", customer="cus_zs", revoked=False):
    await conn.execute(
        t.sessions.insert().values(
            id=sid,
            customer_id=customer,
            authorization_epoch=0,
            created_at_wall=WALL,
            revoked_at_wall=WALL if revoked else None,
        )
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
    await conn.execute(
        t.plans.insert().values(
            id=plan_id,
            customer_id=customer,
            sid=sid,
            origin=origin,
            request_id=f"req_{plan_id}",
            request_hash="r",
            plan_hash="a" * 64,
            steps_hash="s",
            resolved_steps=json.dumps([]),
            utterance_ids=json.dumps(["utt_1"]),
            scope_ids=json.dumps(["utt_1"]),
            status=status,
            created_at_wall=WALL,
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
    await conn.execute(
        t.evaluations.insert().values(
            id=evaluation_id,
            plan_id=plan_id,
            revision=revision,
            decision={"decision": decision.value},
            risk=risk.value,
            findings=json.dumps(_FINDINGS_JSON),
            preflight=json.dumps(_PREFLIGHT_JSON),
            versions=json.dumps(versions if versions is not None else {"cus_zs": 1}),
            policy_version=policy_version,
            clock_version=clock_version,
            view_json=json.dumps(view_json) if view_json else None,
            created_at_wall=WALL,
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
    expiry = expires_at_wall if expires_at_wall is not None else WALL + timedelta(minutes=5)
    mac = _compute_binding(view_hash=view_hash, expires_at_wall=expiry)
    await conn.execute(
        t.approvals.insert().values(
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
    plan_status="EVALUATED",
    approval_status="PENDING",
    decision=DecisionValue.REQUIRE_APPROVAL,
    risk=Risk.YELLOW,
    view_hash="b" * 64,
    view_json=None,
    expires_at_wall=None,
):
    """Seed a complete session → plan → evaluation → approval chain."""
    await _seed_session(conn, sid=sid, customer=customer)
    await _seed_plan(conn, plan_id=plan_id, sid=sid, customer=customer, origin=origin, status=plan_status)
    await _seed_evaluation(
        conn,
        plan_id=plan_id,
        evaluation_id=evaluation_id,
        decision=decision,
        risk=risk,
        view_json=view_json,
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