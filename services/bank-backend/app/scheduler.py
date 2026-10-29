"""L3 scheduler: due-job scanning, S1 dispatch, lease recovery and demo clock advance.

The scheduler is the only producer of MERCHANT_CHARGE event jobs and, together
with ``state.persist``, keeps one ``event_jobs`` row per (source, period).  A
job is dispatched by POSTing to gateway S1 (``/internal/scheduled/authorize``)
outside any database transaction, so a slow or dead gateway never holds a
lease or a row lock.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

import httpx
from pydantic import BaseModel
from safebank_bankcore.ids import digest
from safebank_contracts.state import EVENT_KIND_RANK
from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from . import models as m
from .execution import utcnow

logger = logging.getLogger(__name__)

TERMINAL = {"SUCCESS", "BLOCKED", "FAILED_INSUFFICIENT", "SKIPPED_REAPPROVAL"}
LEASE_SECONDS = 60
PENDING_RETRYING = ("PENDING", "RETRYING")


def _backoff(attempt: int) -> int:
    return min(2 ** max(attempt - 1, 0), 4)


class ClockAdvanceRequest(BaseModel):
    target_at: datetime
    request_id: str


class Scheduler:
    """Scans due obligations into event_jobs and dispatches them to gateway S1."""

    def __init__(self, db, *, s1_url, s1_token, transport=None):
        self.db = db
        self.s1_url = s1_url.rstrip("/")
        self.s1_token = s1_token
        self.transport = transport

    # ── clock helpers ────────────────────────────────────────────────────

    async def read_clock(self, conn):
        row = (await conn.execute(select(m.clock).where(m.clock.c.id == 1))).mappings().one()
        return row["sim_now"], row["version"]

    async def set_clock(self, conn, sim_now: datetime, version: int) -> None:
        await conn.execute(
            update(m.clock).where(m.clock.c.id == 1).values(sim_now=sim_now, version=version)
        )

    # ── scanning ─────────────────────────────────────────────────────────

    async def scan_due(self, conn, until: datetime) -> int:
        """Create one PENDING job per due instruction/billing period; idempotent."""
        created = 0
        si = m.standing_instructions
        rows = (
            await conn.execute(
                select(si.c.id, si.c.amount_cents, si.c.from_account_id, si.c.next_run_at).where(
                    si.c.kind == "SCHEDULED_TRANSFER",
                    si.c.status == "ACTIVE",
                    si.c.next_run_at.isnot(None),
                    si.c.next_run_at <= until,
                )
            )
        ).all()
        for row in rows:
            created += await self._insert_job(
                conn,
                "SCHEDULED_TRANSFER",
                row.id,
                row.next_run_at,
                f"{row.id}|{row.amount_cents}|{row.from_account_id}",
            )
        mb = m.merchant_billing
        rows = (
            await conn.execute(
                select(mb.c.id, mb.c.amount_cents, mb.c.account_id, mb.c.next_due_at).where(
                    mb.c.active.is_(True), mb.c.next_due_at <= until
                )
            )
        ).all()
        for row in rows:
            created += await self._insert_job(
                conn,
                "MERCHANT_CHARGE",
                row.id,
                row.next_due_at,
                f"{row.id}|{row.amount_cents}|{row.account_id}",
            )
        return created

    async def _insert_job(self, conn, kind, source_id, due_at, payload) -> int:
        job_id = digest([kind, source_id, due_at.isoformat()])
        result = await conn.execute(
            pg_insert(m.event_jobs)
            .values(
                id=job_id,
                kind=kind,
                rank=EVENT_KIND_RANK[kind],
                due_at=due_at,
                immutable_payload={"payload": payload},
                payload_hash=digest(payload),
                source_id=source_id,
                period_key=due_at.isoformat(),
                status="PENDING",
                attempt_count=0,
            )
            .on_conflict_do_nothing(index_elements=["source_id", "period_key"])
        )
        return result.rowcount

    # ── dispatch ─────────────────────────────────────────────────────────

    async def run_once(self):
        """Lease and dispatch at most one due job; returns the S1 payload or None."""
        async with self.db.connect() as conn:
            sim_now, _ = await self.read_clock(conn)
        async with self.db.begin() as conn:
            job = await self._claim_next(conn, sim_now)
            if job is None:
                return None
        try:
            payload = await self._post_s1(job["id"], job["attempt_count"])
        except httpx.HTTPError:
            logger.warning("scheduler S1 unreachable for %s", job["id"], exc_info=True)
            await self._requeue(job["id"], job["attempt_count"])
            return {"event_id": job["id"], "status": "RETRYING"}
        if payload.get("status") in TERMINAL:
            await self._mark_done(job["id"])
        else:
            await self._requeue(job["id"], job["attempt_count"])
        return payload

    async def _claim_next(self, conn, sim_now):
        now = utcnow()
        job = (
            (
                await conn.execute(
                    select(m.event_jobs)
                    .where(
                        m.event_jobs.c.status.in_(PENDING_RETRYING),
                        m.event_jobs.c.due_at <= sim_now,
                        or_(
                            m.event_jobs.c.lease_until_wall.is_(None),
                            m.event_jobs.c.lease_until_wall <= now,
                        ),
                    )
                    .order_by(m.event_jobs.c.due_at, m.event_jobs.c.rank, m.event_jobs.c.id)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
            )
            .mappings()
            .first()
        )
        if job is None:
            return None
        lease_until = now + timedelta(seconds=LEASE_SECONDS)
        await conn.execute(
            update(m.event_jobs)
            .where(m.event_jobs.c.id == job["id"])
            .values(
                status="RUNNING",
                lease_until_wall=lease_until,
                attempt_count=m.event_jobs.c.attempt_count + 1,
            )
        )
        return dict(job, attempt_count=job["attempt_count"] + 1)

    async def _post_s1(self, event_id: str, attempt: int):
        kwargs = {"transport": self.transport} if self.transport is not None else {}
        async with httpx.AsyncClient(**kwargs) as client:
            resp = await client.post(
                f"{self.s1_url}/internal/scheduled/authorize",
                json={"event_id": event_id, "attempt": attempt},
                headers={"X-Service-Token": self.s1_token},
            )
            resp.raise_for_status()
            return resp.json()

    async def _mark_done(self, job_id: str) -> None:
        async with self.db.begin() as conn:
            await conn.execute(
                update(m.event_jobs)
                .where(m.event_jobs.c.id == job_id)
                .values(status="DONE", lease_until_wall=None)
            )

    async def _requeue(self, job_id: str, attempt: int) -> None:
        async with self.db.begin() as conn:
            await conn.execute(
                update(m.event_jobs)
                .where(m.event_jobs.c.id == job_id)
                .values(
                    status="RETRYING",
                    lease_until_wall=utcnow() + timedelta(seconds=_backoff(attempt)),
                )
            )

    # ── recovery ─────────────────────────────────────────────────────────

    async def recover(self) -> int:
        """Reset RUNNING jobs whose lease has expired back to RETRYING."""
        now = utcnow()
        async with self.db.begin() as conn:
            result = await conn.execute(
                update(m.event_jobs)
                .where(
                    m.event_jobs.c.status == "RUNNING",
                    m.event_jobs.c.lease_until_wall <= now,
                )
                .values(status="RETRYING", lease_until_wall=now)
            )
        return result.rowcount


class ClockAdvancer:
    """DEMO/EVAL-only simulated clock that steps to each due event and runs it."""

    def __init__(self, scheduler: Scheduler):
        self.scheduler = scheduler
        self._lock = asyncio.Lock()
        self._jobs: dict[str, dict] = {}

    def advance(self, target_at: datetime, request_id: str) -> str:
        job_id = digest(["clock", "advance", request_id])
        if job_id in self._jobs:
            return job_id
        self._jobs[job_id] = {
            "status": "PENDING",
            "sim_now": None,
            "processed": 0,
            "blocked": 0,
            "remaining": 0,
        }
        asyncio.get_running_loop().create_task(self._run(job_id, target_at))
        return job_id

    def status(self, job_id: str):
        state = self._jobs.get(job_id)
        if state is None:
            return None
        return {k: state[k] for k in ("status", "sim_now", "processed", "blocked", "remaining")}

    async def _run(self, job_id: str, target_at: datetime) -> None:
        async with self._lock:
            state = self._jobs[job_id]
            try:
                async with self.scheduler.db.connect() as conn:
                    sim_now, version = await self.scheduler.read_clock(conn)
                if target_at <= sim_now:
                    state.update(status="DONE", sim_now=sim_now)
                    return
                processed = 0
                blocked = 0
                while True:
                    next_due = await self._next_due(sim_now, target_at)
                    if next_due is None:
                        break
                    version += 1
                    async with self.scheduler.db.begin() as conn:
                        await self.scheduler.set_clock(conn, next_due, version)
                    sim_now = next_due
                    while True:
                        result = await self.scheduler.run_once()
                        if result is None:
                            break
                        processed += 1
                        if result.get("status") == "BLOCKED":
                            blocked += 1
                remaining = await self._count_due(target_at)
                state.update(
                    status="DONE",
                    sim_now=sim_now,
                    processed=processed,
                    blocked=blocked,
                    remaining=remaining,
                )
            except Exception:
                logger.exception("clock advance %s failed", job_id)
                state.update(status="FAILED")

    async def _next_due(self, sim_now: datetime, target_at: datetime):
        async with self.scheduler.db.connect() as conn:
            return await conn.scalar(
                select(m.event_jobs.c.due_at)
                .where(
                    m.event_jobs.c.status.in_(PENDING_RETRYING),
                    m.event_jobs.c.due_at > sim_now,
                    m.event_jobs.c.due_at <= target_at,
                )
                .order_by(m.event_jobs.c.due_at)
                .limit(1)
            )

    async def _count_due(self, target_at: datetime) -> int:
        async with self.scheduler.db.connect() as conn:
            return await conn.scalar(
                select(func.count())
                .select_from(m.event_jobs)
                .where(
                    m.event_jobs.c.status.in_(PENDING_RETRYING + ("RUNNING",)),
                    m.event_jobs.c.due_at <= target_at,
                )
            )
