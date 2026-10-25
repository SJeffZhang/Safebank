"""W4c T2a: fixture producing a decision=ALLOW plan through real L3 + real G4.

Builds the w3b test app, swaps ``app.state.l3`` for a real ``L3Client`` served
from a temporary bank-backend (obligations seed: customer ``cus_zs``, checking
account ``acc_zs_chk``, card ``crd_zs_1``), then evaluates a GREEN
``freeze_card`` plan and returns the app together with the G4 JSON response.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.pool import NullPool

from services.gateway.app.db import engine as gw_engine
from tests.gateway.w3a.db.test_real_preflight import _serve_l3
from tests.gateway.w3b.db.conftest import (
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


async def _build_allow_plan(app_config, obligations_db) -> AllowPlan:
    """Build the real-L3 G4 evaluation once and return the captured result."""
    test_app = await build_app(app_config)
    fastapi_app = test_app.client._transport.app

    # Swap the fake L3 for a real one served from a fresh obligations seed.
    _, _, l3 = await _serve_l3(obligations_db)
    fastapi_app.state.l3 = l3

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

    pd = make_plan_draft_dict(
        request_id="req_w4c_allow",
        session_id=sid,
        utterance_ids=[utt_id],
        steps=[_freeze_step(rid_card)],
    )
    body = make_body(request_id="req_w4c_allow", plan_draft=pd)
    headers = test_app.auth_headers(customer_id="cus_zs", sid=sid, jti=jti, utts=[utt_id])

    resp = await test_app.client.post(EVALUATE_URL, json=body, headers=headers)
    await db.dispose()
    return AllowPlan(app=fastapi_app, response=resp.json(), db_url=test_app.db_url)


def allow_plan(app_config, obligations_db) -> AllowPlan:
    """Produce the real-L3 G4 evaluation result for a freeze_card plan."""
    return asyncio.run(_build_allow_plan(app_config, obligations_db))
