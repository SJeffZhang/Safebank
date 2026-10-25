"""W3b G4 evaluate DB fixtures: isolated temporary PostgreSQL with test FastAPI app.

Builds a minimal FastAPI app wired to the tmpfs PostgreSQL so HTTP endpoint
tests exercise the real auth middleware, pipeline stages, and persistence.

The app is built inside each test's ``asyncio.run()`` (not in the fixture) so
the DB engine and the request share the same event loop.  L3 is replaced with
FakeL3Client after build so pipeline stages run without a real bank‑backend.
"""

from __future__ import annotations

import json
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
from sqlalchemy.pool import NullPool

from services.gateway.app import tables as t
from services.gateway.app.approval import service as approval_service
from services.gateway.app.auth.tokens import APP_AUDIENCE, ASSISTANT_AUDIENCE, sign_jwt
from services.gateway.app.config import Settings
from services.gateway.app.db import engine
from services.gateway.app.l3.client import L3Client
from services.gateway.app.main import build_public_app
from services.gateway.app.runtime import GatewayRuntime, _key_pair, _validate_approval_keys
from tests.gateway.db.conftest import gateway_db  # noqa: F401

# ── Test constants ────────────────────────────────────────────────────────────

WALL = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone(timedelta(hours=8)))
HMAC_KEY = b"w3b-approval-hmac-key-at-least-32-bytes-long!"
OTP_KEY = b"w3b-otp-hmac-key-at-least-32-bytes-long-now!"
SERVICE_TOKEN = "test-frontend-service-token-32b"
AGENT_TOKEN = "test-agent-service-token-32bytes"
SCHEDULER_TOKEN = "test-scheduler-token-32bytes-xxx"


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


# ── Fake L3 client ────────────────────────────────────────────────────────────


class FakeL3Client:
    """A L3Client replacement that returns a canned BankState dict.

    Test coverage is limited to what a static seed can exercise; real L3
    coverage lives in end‑to‑end suites.
    """

    # pylint: disable=unused-argument

    def __init__(self, state_fn=None):
        self._state_fn = state_fn or _default_bank_state

    async def snapshot(self, payload: dict) -> dict:
        return self._state_fn()


def _default_bank_state() -> dict:
    """Minimal bank state for cus_a: checking account acc_a with 600,000 cents."""
    return {
        "sim_now": WALL.isoformat(),
        "clock_version": 1,
        "versions": {"cus_a": 1},
        "accounts": {
            "acc_a": {
                "id": "acc_a",
                "customer_id": "cus_a",
                "type": "CHECKING",
                "display_name": "活期",
                "account_no": "6222000000000001",
                "currency": "CNY",
                "ledger": "CNY",
                "current_card_id": "crd_zs_1",
                "single_limit_cents": 500000,
                "daily_limit_cents": 2000000,
                "flags": 1,
                "debits_posted_cents": 0,
                "credits_posted_cents": 600000,
                "debits_pending_cents": 0,
                "credits_pending_cents": 0,
            },
            "acc_external_clearing": {
                "id": "acc_external_clearing",
                "customer_id": None,
                "type": "CHECKING",
                "display_name": "External Clearing",
                "account_no": "EXTCLR000000001",
                "currency": "CNY",
                "ledger": "CNY",
                "current_card_id": None,
                "single_limit_cents": 100000000,
                "daily_limit_cents": 100000000,
                "flags": 0,
                "debits_posted_cents": 0,
                "credits_posted_cents": 0,
                "debits_pending_cents": 0,
                "credits_pending_cents": 0,
            },
        },
        "customers": {
            "cus_a": {
                "id": "cus_a",
                "display_name": "张三",
                "phone": "13800001111",
                "risk_level": 1,
                "risk_assessed_at": "2026-10-01T00:00:00+08:00",
                "risk_valid_until": None,
            }
        },
        "cards": {
            "crd_zs_1": {
                "id": "crd_zs_1",
                "account_id": "acc_a",
                "last4": "0001",
                "status": "ACTIVE",
                "single_limit_cents": 200000,
                "daily_limit_cents": 500000,
                "controls": {"online": True, "overseas": False, "atm": True, "quickpass": False},
                "replaces_id": None,
                "created_at": "2026-10-01T00:00:00+08:00",
            }
        },
        "payees": {
            "pye_ext": {
                "id": "pye_ext",
                "customer_id": "cus_a",
                "name": "External Payee",
                "alias": None,
                "phone": None,
                "bank_name": "中国银行",
                "account_no": "6200000000000001",
                "target_account_id": None,
                "target_fingerprint": "fp_ext",
                "created_at": "2026-10-01T00:00:00+08:00",
            }
        },
        "transactions": (
            {
                "id": "tx_prior_1",
                "transfer_id": "tfr_prior_1",
                "customer_id": "cus_a",
                "account_id": "acc_a",
                "direction": "OUT",
                "kind": "TRANSFER_EXTERNAL",
                "phase": "POSTED",
                "status": "POSTED",
                "amount_cents": 50000,
                "balance_after_cents": 550000,
                "available_after_cents": 550000,
                "counterparty": "External Payee",
                "category": "transfer",
                "memo": "prior transfer",
                "payee_id": "pye_ext",
                "card_id": None,
                "merchant_id": None,
                "channel": "app",
                "occurred_at": "2026-10-15T00:00:00+08:00",
                "sequence": 0,
            },
        ),
        "counters": {
            "daily_outflow_cents": {},
            "rolling_outflow_cents": {},
            "daily_used_cents": {},
        },
        "directory": {},
        "ops_7d": (),
        "instructions": (),
        "subscriptions": (),
        "obligations": {
            "covered_customer_ids": ("cus_a",),
            "merchants": {},
            "billings": {},
            "standing_instructions": {},
            "runs": (),
            "instruction_targets": {},
        },
        "products": (),
        "holdings": (),
        "orders": (),
    }


def _low_balance_bank_state() -> dict:
    """Bank state with only ¥100 available (10,000 cents) for REPLAN tests.

    Sets current_card_id to None so the account's own limits (raised high)
    are used instead of the card's restrictive 200,000/500,000 limits.
    This ensures the balance check (INSUFFICIENT_FUNDS) fires before
    CARD_SINGLE_LIMIT.
    """
    state = _default_bank_state()
    acct = dict(state["accounts"]["acc_a"])
    acct["credits_posted_cents"] = 10000
    acct["debits_pending_cents"] = 0
    acct["debits_posted_cents"] = 0
    acct["current_card_id"] = None
    acct["single_limit_cents"] = 10_000_000
    acct["daily_limit_cents"] = 10_000_000
    state["accounts"] = {**state["accounts"], "acc_a": acct}
    return state


# ── Test FastAPI app ──────────────────────────────────────────────────────────


@dataclass
class TestApp:
    """Bundled test application with client, keys, and seed helpers."""

    client: httpx.AsyncClient
    db_url: str
    admin_url: str
    public_key_pem: bytes
    private_key_pem: bytes
    wall_now_fn: any

    def assistant_jwt(
        self,
        *,
        customer_id: str = "cus_a",
        sid: str = "ses_g4test",
        utts: list[str] | None = None,
        jti: str = "test-jti",
    ) -> str:
        """Generate an assistant JWT for a test session."""
        _utts = utts if utts is not None else ["utt_ses_g4test_1"]
        return sign_jwt(
            self.private_key_pem,
            sub=customer_id,
            aud=ASSISTANT_AUDIENCE,
            scope=["assistant"],
            sid=sid,
            utts=_utts,
            jti=jti,
        )

    def app_jwt(
        self,
        *,
        customer_id: str = "cus_a",
        jti: str = "test-jti-f",
    ) -> str:
        """Generate an app (frontend) JWT without sid (F caller)."""
        return sign_jwt(
            self.private_key_pem,
            sub=customer_id,
            aud=APP_AUDIENCE,
            scope=["app"],
            sid=None,
            utts=None,
            jti=jti,
        )

    def auth_headers(
        self,
        *,
        customer_id: str = "cus_a",
        sid: str = "ses_g4test",
        utts: list[str] | None = None,
        jti: str = "test-jti",
    ) -> dict:
        """HTTP headers for an authenticated A (assistant) caller."""
        _utts = utts if utts is not None else ["utt_ses_g4test_1"]
        return {
            "X-Service-Token": AGENT_TOKEN,
            "Authorization": (
                f"Bearer {self.assistant_jwt(customer_id=customer_id, sid=sid, utts=_utts, jti=jti)}"  # noqa: E501
            ),
        }


@dataclass
class AppConfig:
    """Pre-built configuration that :func:`build_app` turns into a :class:`TestApp`."""

    gateway_db: any
    private_pem: bytes
    public_pem: bytes


async def build_app(config: AppConfig, *, truncate: bool = True, bank_state_fn=None) -> TestApp:
    """Build a FastAPI test app wired to the tmpfs PostgreSQL.

    Replaces ``app.state.l3`` with a FakeL3Client so pipeline stages can run
    without a real bank‑backend.  MUST be called inside ``asyncio.run()``.

    If *bank_state_fn* is provided it becomes the FakeL3Client's data source
    (no-arg callable returning a BankState-like dict).
    """
    gw_db = config.gateway_db
    private_pem = config.private_pem
    public_pem = config.public_pem

    # Write PEM files so _key_pair can load them from disk.
    tmpdir = tempfile.mkdtemp(prefix="sbg-w3b-")
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

    # W4b2a configures per-instance adapter keys, but the service module
    # (used by create_for_evaluation) relies on global configure().
    approval_service.configure(
        approval_hmac_key=approval_key_bytes,
        profile=settings.profile,
        wall_now=lambda: WALL,
    )

    app = build_public_app(settings, env=test_env, public_key=public_pem, runtime=rt)

    # Replace L3 with the fake so pipeline stages can run without a real
    # bank‑backend.  This only affects the test app instance.
    app.state.l3 = FakeL3Client(state_fn=bank_state_fn)

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
    """Return pre-built config; tests call ``await build_app(config)`` inside
    ``asyncio.run()`` so the app shares the test's event loop."""
    private_pem, public_pem = _generate_key_pair()
    return AppConfig(
        gateway_db=gateway_db,
        private_pem=private_pem,
        public_pem=public_pem,
    )


# ── Seed helpers ─────────────────────────────────────────────────────────────


async def _seed_session(
    conn,
    *,
    sid: str = "ses_g4test",
    customer: str = "cus_a",
    revoked: bool = False,
) -> None:
    """Seed a sessions row for the test JWT."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    await conn.execute(
        pg_insert(t.sessions)
        .values(
            id=sid,
            customer_id=customer,
            authorization_epoch=1,
            created_at_wall=WALL - timedelta(minutes=10),
            revoked_at_wall=WALL if revoked else None,
        )
        .on_conflict_do_nothing()
    )


async def _seed_auth(
    conn,
    *,
    jti: str = "test-jti",
    customer: str = "cus_a",
) -> None:
    """Seed an auth_sessions row so check_revocation accepts the test JWT."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    await conn.execute(
        pg_insert(t.auth_sessions)
        .values(
            jti=jti,
            customer_id=customer,
            created_at_wall=WALL - timedelta(minutes=5),
            expires_at_wall=WALL + timedelta(minutes=60),
            revoked_at_wall=None,
        )
        .on_conflict_do_nothing()
    )


async def _seed_utterance(
    conn,
    *,
    utt_id: str,
    sid: str = "ses_g4test",
    text: str = "转账 800 元",
    kind: str = "typed",
    status: str = "ACTIVE",
    parse_status: str = "SCOPED",
    scope: dict | None = None,
) -> None:
    """Seed an utterance row."""
    if scope is None:
        scope = {
            "action_kinds": ["TRANSFER_EXTERNAL"],
            "from_account_id": "acc_a",
            "transfers": [
                {
                    "target_fingerprint": "fp_ext",
                    "amount_cents": 80000,
                    "from_account_id": "acc_a",
                }
            ],
            "period": None,
            "channel": "online",
            "fields": {"count": 1},
        }
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    await conn.execute(
        pg_insert(t.utterances)
        .values(
            id=utt_id,
            sid=sid,
            kind=kind,
            text=text,
            normalized_scope=scope,
            echoes_untrusted=False,
            status=status,
            parse_status=parse_status,
            authorization_epoch=1,
            parent_ids=[],
            created_at_wall=WALL - timedelta(minutes=5),
            expires_at_wall=WALL + timedelta(minutes=30),
        )
        .on_conflict_do_nothing()
    )


async def _seed_full(
    conn,
    *,
    sid: str = "ses_g4test",
    customer: str = "cus_a",
    utterance_count: int = 1,
    revoked: bool = False,
    jti_auth: str = "test-jti",
) -> list[str]:
    """Seed session + auth + utterances — the full test baseline."""
    await _seed_session(conn, sid=sid, customer=customer, revoked=revoked)
    await _seed_auth(conn, jti=jti_auth, customer=customer)
    texts = ["转账 800 元", "取消订阅", "冻结卡片", "申购基金"]
    ids: list[str] = []
    for i in range(utterance_count):
        utt_id = f"utt_{sid}_{i + 1}"
        await _seed_utterance(conn, utt_id=utt_id, sid=sid, text=texts[i % len(texts)])
        ids.append(utt_id)
    return ids


async def _seed_read(
    conn,
    *,
    sid: str,
    tool: str,
    response: dict,
    rid: str | None = None,
) -> str:
    """Seed a read_records row for entity visibility checks.

    Returns the read record id.
    """
    import uuid

    _rid = rid or f"rec_{uuid.uuid4().hex[:16]}"
    await conn.execute(
        text("""
            INSERT INTO read_records (id, sid, tool, canonical_args, response,
                                      untrusted_paths, versions, observed_at,
                                      created_at_wall)
            VALUES (:id, :sid, :tool, '{}'::jsonb, CAST(:response AS jsonb),
                    '[]'::jsonb, '{"cus_a": 1}'::jsonb, :now, :now)
        """),
        {"id": _rid, "sid": sid, "tool": tool, "response": json.dumps(response), "now": WALL},
    )
    return _rid


async def _seed_workflow_event(
    conn,
    *,
    event_id: str,
    customer_id: str = "cus_a",
    action_kind: str,
    target_id: str | None = None,
    amount_cents: int = 0,
    status: str = "COMMITTED",
    occurred_at=None,
) -> None:
    """Seed a workflow_events row for RISKY_SEQUENCE / workflow tests."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    _occurred_at = occurred_at if occurred_at is not None else WALL - timedelta(hours=2)
    await conn.execute(
        pg_insert(t.workflow_events)
        .values(
            event_id=event_id,
            customer_id=customer_id,
            plan_id=None,
            step_id=None,
            action_kind=action_kind,
            target_id=target_id,
            amount_cents=amount_cents,
            status=status,
            occurred_at=_occurred_at,
        )
        .on_conflict_do_nothing()
    )


# ── Test body builders ───────────────────────────────────────────────────────


def _transfer_step(
    *,
    step_id: str = "s1",
    payee_id: str = "pye_ext",
    amount_cents: int = 80000,
    from_account_id: str = "acc_a",
) -> dict:
    """A minimal valid transfer step dict for PlanDraft."""
    return {
        "step_id": step_id,
        "tool": "transfer",
        "depends_on": [],
        "arg_sources": [],
        "args": {
            "from_account_id": from_account_id,
            "payee_id": payee_id,
            "to_account_id": None,
            "amount_cents": amount_cents,
            "memo": "test transfer",
        },
    }


def make_plan_draft_dict(
    *,
    request_id: str = "req_test_g4",
    session_id: str = "ses_g4test",
    utterance_ids: list[str] | None = None,
    steps: list[dict] | None = None,
) -> dict:
    """Return a ``PlanDraft``‑compatible dict (without Pydantic validation).

    Returns a plain dict so callers can embed it directly in a JSON body.
    """
    return {
        "request_id": request_id,
        "session_id": session_id,
        "utterance_ids": utterance_ids or ["utt_ses_g4test_1"],
        "intents": [],
        "rationale": "",
        "steps": steps or [_transfer_step()],
    }


def make_body(
    *,
    request_id: str = "req_test_g4",
    plan_draft: dict | None = None,
) -> dict:
    """Return a G4 request body dict."""
    if plan_draft is None:
        plan_draft = make_plan_draft_dict(request_id=request_id)
    return {"request_id": request_id, "plan_draft": plan_draft}
