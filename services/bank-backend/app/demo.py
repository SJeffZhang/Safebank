"""Demo / eval only L3 routes (PRD §9.9 D1/D2 dataset install, D4 external events).

These routes are only mounted when ``DEMO_MODE`` / ``EVAL_MODE`` is truthy; the
whole module is inert otherwise.  They are still ``/internal/*`` routes, so the
global service-token dependency applies as everywhere else.

``/internal/demo/reset`` and ``/internal/demo/prepare`` re-run the idempotent
demo seed installer (``app.seed_demo.install_demo``) on the owner role.  The
ledger is append-only, so neither route ever deletes or rewinds rows — a second
run converges reference data (payee creation times) and otherwise inserts
nothing new.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from safebank_contracts.codes import Code
from safebank_contracts.state import EVENT_KIND_RANK
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from . import models as m
from .errors import BankError
from .seed_demo import install_demo

# The only demo dataset we ship.  ``dataset_revision`` comes from the seed file
# (``fixtures/l1_seed_v1.json``); more seed/scenario ids can be whitelisted here.
DEMO_SEEDS: dict[str, str] = {"l1_seed_v1": "l1_seed_v1.0"}
DEMO_SCENARIOS: dict[str, str] = {"l1_seed_v1": "l1_seed_v1.0"}

DEMO_EVENT_KINDS = ("EXTERNAL_CREDIT", "EXTERNAL_DEBIT", "SPLIT_PAYMENT")

# Advisory lock taken by the seed installer (mirrors ``app.seed_demo.main``).
_SEED_LOCK = 814001


class DemoResetRequest(BaseModel):
    seed_id: str


class DemoPrepareRequest(BaseModel):
    scenario_id: str


class DemoEventRequest(BaseModel):
    kind: Literal["EXTERNAL_CREDIT", "EXTERNAL_DEBIT", "SPLIT_PAYMENT"]
    customer_id: str
    account_id: str
    amount_cents: int = Field(gt=0)
    card_id: str | None = None
    counterparty: str | None = None
    memo: str | None = None
    split_item_id: str | None = None
    due_at: datetime

    @field_validator("due_at")
    @classmethod
    def _timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("due_at must be timezone-aware")
        return value


async def _install_demo(admin_database, fingerprint_key: str) -> None:
    """Run the idempotent demo seed installer on a caller-managed owner engine."""
    async with admin_database.begin() as conn:
        await conn.execute(text("SELECT pg_advisory_xact_lock(:lock)"), {"lock": _SEED_LOCK})
        await install_demo(conn, fingerprint_key)


def register_demo_routes(app, database, admin_database, fingerprint_key: str) -> None:
    @app.post("/internal/demo/reset")
    async def demo_reset(body: DemoResetRequest):
        revision = DEMO_SEEDS.get(body.seed_id)
        if revision is None:
            raise BankError(Code.CONTRACT_INVALID, 422)
        await _install_demo(admin_database, fingerprint_key)
        return {"dataset_revision": revision}

    @app.post("/internal/demo/prepare")
    async def demo_prepare(body: DemoPrepareRequest):
        revision = DEMO_SCENARIOS.get(body.scenario_id)
        if revision is None:
            raise BankError(Code.CONTRACT_INVALID, 422)
        await _install_demo(admin_database, fingerprint_key)
        return {"dataset_revision": revision}

    @app.post("/internal/demo/events")
    async def demo_events(body: DemoEventRequest):
        payload = body.model_dump(mode="json")
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        payload_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        event_id = f"evt_demo_{payload_hash}"
        source_id = f"demo:{payload_hash[:16]}"
        period_key = body.due_at.isoformat()

        async with database.begin() as conn:
            owner = await conn.execute(
                select(m.accounts.c.id).where(
                    m.accounts.c.id == body.account_id,
                    m.accounts.c.customer_id == body.customer_id,
                )
            )
            if owner.scalar_one_or_none() is None:
                raise BankError(Code.NOT_FOUND, 404)
            await conn.execute(
                pg_insert(m.event_jobs)
                .values(
                    id=event_id,
                    kind="DEMO_EVENT",
                    rank=EVENT_KIND_RANK["DEMO_EVENT"],
                    due_at=body.due_at,
                    immutable_payload=payload,
                    payload_hash=payload_hash,
                    source_id=source_id,
                    period_key=period_key,
                    status="PENDING",
                )
                .on_conflict_do_nothing(constraint="event_jobs_payload_hash_key")
            )
        return {"event_id": event_id}
