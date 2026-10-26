"""W4c T2a: fixture producing a decision=ALLOW plan through real L3 + real G4.

Builds the w3b test app, swaps ``app.state.l3`` for a real ``L3Client`` served
from a temporary bank-backend (obligations seed: customer ``cus_zs``, checking
account ``acc_zs_chk``, card ``crd_zs_1``), then evaluates a GREEN
``freeze_card`` plan and returns the app together with the G4 JSON response.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.pool import NullPool

from services.gateway.app import locks
from services.gateway.app.approval import service as approval_service
from services.gateway.app.auth.tokens import APP_AUDIENCE, sign_jwt
from services.gateway.app.db import engine as gw_engine
from tests.gateway.w3a.db.test_real_preflight import _serve_l3
from tests.gateway.w3b.db.conftest import (
    HMAC_KEY,
    SERVICE_TOKEN,
    _seed_auth,
    _seed_session,
    _seed_utterance,
    build_app,
    make_body,
    make_plan_draft_dict,
)
from tests.obligations_data.fixtures import NOW

EVALUATE_URL = "/plans/evaluate"


@dataclass
class AllowPlan:
    """Result of a real-L3 G4 evaluation: the FastAPI app, response and db_url."""

    app: Any
    response: dict
    db_url: str


def _real_now() -> datetime:
    return datetime.now(UTC)


def _use_real_clock(fastapi_app) -> None:
    """Run the w3b-built gateway on real time.

    The w3b fixture pins every gateway clock to a fixed WALL, but the real L3
    validates execution deadlines against real time, so authorization windows
    derived from WALL (utterances, approvals) arrive at L3 already expired.
    """
    fastapi_app.state.wall_now = _real_now
    approval_service.configure(approval_hmac_key=HMAC_KEY, profile="B2", wall_now=_real_now)


async def _anchor_to_real_time(conn, *, sid: str) -> None:
    """Re-anchor the WALL-based seed rows of this session to real now."""
    for sql in (
        "UPDATE sessions SET created_at_wall = now() - interval '5 minutes' WHERE id = :sid",
        "UPDATE auth_sessions SET created_at_wall = now() - interval '5 minutes', "
        "expires_at_wall = now() + interval '60 minutes'",
        "UPDATE utterances SET created_at_wall = now() - interval '1 minute', "
        "expires_at_wall = now() + interval '30 minutes' WHERE sid = :sid",
    ):
        await conn.execute(text(sql), {"sid": sid})


@dataclass
class AllowPlanContext:
    """Context yielded by :func:`allow_plan_ctx`: live app, L3, and plan metadata."""

    test_app: Any
    headers: dict
    plan_id: str
    utt_id: str
    db_url: str
    l3_admin: Any
    response: dict


def _freeze_scope() -> dict:
    """Normalized scope authorizing exactly one CARD_FREEZE of ``crd_zs_1``."""
    return {
        "action_kinds": ["CARD_FREEZE"],
        "from_account_id": "acc_zs_chk",
        "transfers": [],
        "period": None,
        "channel": "online",
        "fields": {"card_id": "crd_zs_1"},
    }


def _freeze_step(rid_card: str) -> dict:
    """A freeze_card step whose ``card_id`` comes from a get_cards read record."""
    return {
        "step_id": "s1",
        "tool": "freeze_card",
        "depends_on": [],
        "arg_sources": [
            {
                "param": "card_id",
                "source": {"kind": "read", "ref": rid_card, "pointer": "/cards/0/id"},
            },
        ],
        "args": {"card_id": "crd_zs_1"},
    }


async def _seed_card_read(conn, *, rid: str, sid: str) -> str:
    """Seed a get_cards read record matching the real L3 clock and versions."""
    await conn.execute(
        text(
            """
            INSERT INTO read_records (id, sid, tool, canonical_args, response,
                                      untrusted_paths, versions, observed_at,
                                      created_at_wall)
            VALUES (:id, :sid, :tool, '{}'::jsonb, CAST(:response AS jsonb),
                    '[]'::jsonb, CAST(:versions AS jsonb), :obs, :obs)
            """
        ),
        {
            "id": rid,
            "sid": sid,
            "tool": "get_cards",
            "response": json.dumps({"cards": [{"id": "crd_zs_1", "last4": "0001"}]}),
            "versions": json.dumps({"cus_zs": 0}),
            "obs": NOW,
        },
    )
    return rid


@asynccontextmanager
async def allow_plan_ctx(app_config, obligations_db):
    """Async context manager yielding an ALLOW plan with live L3 + gateway app.

    Builds the w3b test app, swaps in a real L3 served from *obligations_db*,
    seeds a session / utterance / card-read, evaluates a GREEN freeze_card plan,
    and yields an :class:`AllowPlanContext`.  On exit the L3 admin and runtime
    engines are disposed (the test app's own engine lives with the app).
    """
    test_app = await build_app(app_config)
    fastapi_app = test_app.client._transport.app

    admin, runtime, l3 = await _serve_l3(obligations_db)
    fastapi_app.state.l3 = l3
    locks.configure(fastapi_app.state.db, max_executions=4)
    _use_real_clock(fastapi_app)

    sid = "ses_w4c_allow"
    jti = "jti-w4c-allow"
    utt_id = f"utt_{sid}_1"

    db = gw_engine(test_app.db_url, poolclass=NullPool)
    async with db.begin() as conn:
        await _seed_session(conn, sid=sid, customer="cus_zs")
        await _seed_auth(conn, jti=jti, customer="cus_zs")
        await _seed_utterance(
            conn, utt_id=utt_id, sid=sid, text="冻结我的卡", scope=_freeze_scope()
        )
        rid_card = await _seed_card_read(conn, rid="rec_w4c_card", sid=sid)
        await _anchor_to_real_time(conn, sid=sid)
    await db.dispose()

    pd = make_plan_draft_dict(
        request_id="req_w4c_allow",
        session_id=sid,
        utterance_ids=[utt_id],
        steps=[_freeze_step(rid_card)],
    )
    body = make_body(request_id="req_w4c_allow", plan_draft=pd)
    headers = test_app.auth_headers(customer_id="cus_zs", sid=sid, jti=jti, utts=[utt_id])

    resp = await test_app.client.post(EVALUATE_URL, json=body, headers=headers)
    result = resp.json()

    ctx = AllowPlanContext(
        test_app=test_app,
        headers=headers,
        plan_id=result["plan_id"],
        utt_id=utt_id,
        db_url=test_app.db_url,
        l3_admin=admin,
        response=result,
    )
    try:
        yield ctx
    finally:
        await admin.dispose()
        await runtime.dispose()


async def _build_allow_plan(app_config, obligations_db) -> AllowPlan:
    """Build the real-L3 G4 evaluation and return the captured result."""
    async with allow_plan_ctx(app_config, obligations_db) as ctx:
        return AllowPlan(
            app=ctx.test_app.client._transport.app,
            response=ctx.response,
            db_url=ctx.db_url,
        )


def allow_plan(app_config, obligations_db) -> AllowPlan:
    """Produce the real-L3 G4 evaluation result for a freeze_card plan."""
    return asyncio.run(_build_allow_plan(app_config, obligations_db))


# ── T3a: approval plan fixture (YELLOW confirm via real G4 → G9 → G6) ──────────


@dataclass
class ApprovalPlanContext:
    """Context yielded by :func:`approval_plan_ctx`: same as AllowPlanContext
    plus the approval_id and view_hash for the confirmed plan."""

    test_app: Any
    headers: dict
    plan_id: str
    utt_id: str
    db_url: str
    l3_admin: Any
    response: dict
    approval_id: str
    view_hash: str


def _limit_tighten_step(rid_card: str, utt_id: str) -> dict:
    """An update_card_limits step lowering single limit from 500000→100000.

    ``card_id`` comes from a get_cards read record; ``single_limit_cents``
    comes from the utterance span covering "1000" (元→分 conversion handled
    by the pipeline)."""
    return {
        "step_id": "s1",
        "tool": "update_card_limits",
        "depends_on": [],
        "arg_sources": [
            {
                "param": "card_id",
                "source": {"kind": "read", "ref": rid_card, "pointer": "/cards/0/id"},
            },
            {
                "param": "single_limit_cents",
                "source": {
                    "kind": "utterance",
                    "ref": utt_id,
                    "span": [7, 11],
                },
            },
        ],
        "args": {"card_id": "crd_zs_1", "single_limit_cents": 100000, "daily_limit_cents": None},
    }


def _tighten_scope() -> dict:
    """Normalized scope authorizing exactly one CARD_LIMIT_TIGHTEN of ``crd_zs_1``."""
    return {
        "action_kinds": ["CARD_LIMIT_TIGHTEN"],
        "from_account_id": "acc_zs_chk",
        "transfers": [],
        "period": None,
        "channel": "online",
        "fields": {"card_id": "crd_zs_1", "single_limit_cents": 100000},
    }


@asynccontextmanager
async def approval_plan_ctx(app_config, obligations_db):
    """Async context manager yielding a CONFIRMED plan through real L3+G4+G9+G6.

    Builds a w3b test app with real L3, evaluates an update_card_limits
    (TIGHTEN) plan, then confirms the resulting approval via G9→G6.
    On exit the L3 admin and runtime engines are disposed.
    """
    test_app = await build_app(app_config)
    fastapi_app = test_app.client._transport.app

    admin, runtime, l3 = await _serve_l3(obligations_db)
    fastapi_app.state.l3 = l3
    locks.configure(fastapi_app.state.db, max_executions=4)
    _use_real_clock(fastapi_app)

    sid = "ses_w4c_approve"
    jti = "jti-w4c-approve"
    utt_id = f"utt_{sid}_1"
    utt_text = "把单笔限额改成1000元"

    db = gw_engine(test_app.db_url, poolclass=NullPool)
    async with db.begin() as conn:
        await _seed_session(conn, sid=sid, customer="cus_zs")
        await _seed_auth(conn, jti=jti, customer="cus_zs")
        await _seed_auth(conn, jti="jti-w4c-approve-f", customer="cus_zs")
        await _seed_utterance(conn, utt_id=utt_id, sid=sid, text=utt_text, scope=_tighten_scope())
        rid_card = await _seed_card_read(conn, rid="rec_w4c_approve", sid=sid)
        await _anchor_to_real_time(conn, sid=sid)
    await db.dispose()

    step = _limit_tighten_step(rid_card, utt_id)
    pd = make_plan_draft_dict(
        request_id="req_w4c_approve",
        session_id=sid,
        utterance_ids=[utt_id],
        steps=[step],
    )
    body = make_body(request_id="req_w4c_approve", plan_draft=pd)

    # Evaluate with agent headers.
    a_headers = test_app.auth_headers(customer_id="cus_zs", sid=sid, jti=jti, utts=[utt_id])

    for _ in range(3):
        resp = await test_app.client.post(EVALUATE_URL, json=body, headers=a_headers)
        result = resp.json()
        decision = result.get("decision", "")
        if decision == "REQUIRE_APPROVAL":
            break
        # Adjust arg_sources or scope based on G4 findings — never change rules.
        findings = result.get("findings", [])
        if decision == "REPLAN":
            for f in findings:
                code = f.get("code", "")
                if "SOURCE" in code or "UNKNOWN" in code or "UNTRUSTED" in code:
                    # G4 couldn't resolve a span; add the raw cent value to
                    # args so the pipeline has a concrete fallback.
                    step["args"]["single_limit_cents"] = 100000
        elif decision == "DENY":
            raise RuntimeError(f"G4 DENY (unexpected): {findings}")
    else:
        raise RuntimeError(f"G4 did not return REQUIRE_APPROVAL after 3 attempts: {result}")

    approval_id = result["approval_id"]

    # Confirm via G9 → G6 using F (frontend) credentials.
    # The app token needs the "approve" scope for G9/G6 (w3b's app_jwt only
    # grants "app"); sign it directly like the w4b2b fixtures do.
    f_jwt = sign_jwt(
        test_app.private_key_pem,
        sub="cus_zs",
        aud=APP_AUDIENCE,
        scope=["app", "approve"],
        sid=None,
        utts=None,
        jti="jti-w4c-approve-f",
    )
    f_headers = {
        "X-Service-Token": SERVICE_TOKEN,
        "Authorization": f"Bearer {f_jwt}",
    }

    # G9: get the approval panel (view_hash + risk_ack_required).
    g9_url = f"/approvals/{approval_id}"
    g9_resp = await test_app.client.get(g9_url, headers=f_headers)
    panel = g9_resp.json()
    assert g9_resp.status_code == 200, (g9_resp.status_code, panel)
    view_hash = panel["view_hash"]
    risk_ack_required = panel.get("risk_ack_required", True)

    # G6: confirm.
    g6_url = f"/approvals/{approval_id}/confirm"
    g6_body = {
        "request_id": "req_w4c_approve_confirm",
        "view_hash": view_hash,
        "risk_ack": risk_ack_required,
    }
    g6_resp = await test_app.client.post(g6_url, json=g6_body, headers=f_headers)
    assert g6_resp.status_code == 200, f"G6 confirm failed: {g6_resp.text}"

    ctx = ApprovalPlanContext(
        test_app=test_app,
        headers=a_headers,
        plan_id=result["plan_id"],
        utt_id=utt_id,
        db_url=test_app.db_url,
        l3_admin=admin,
        response=result,
        approval_id=approval_id,
        view_hash=view_hash,
    )
    try:
        yield ctx
    finally:
        await admin.dispose()
        await runtime.dispose()
