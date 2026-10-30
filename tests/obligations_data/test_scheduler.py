"""L3 scheduler – real PostgreSQL acceptance tests (make test-obligations)."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from app import models as m
from app.db import engine
from app.scheduler import ClockAdvancer, Scheduler
from sqlalchemy import text, update
from sqlalchemy.pool import NullPool

from tests.obligations_data.fixtures import (
    NOW,
    install_core,
    install_obligations,
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def clean_database(obligations_db):
    """Truncate every application table before each test."""

    async def _truncate():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        async with admin.begin() as conn:
            names = ",".join(m.metadata.tables)
            await conn.execute(text(f"TRUNCATE {names} CASCADE"))
        await admin.dispose()

    asyncio.run(_truncate())
    yield


def _tomorrow_6am() -> datetime:
    """First due time after NOW that the seed SI's next_run_at points to."""
    return datetime.fromisoformat("2026-11-05T06:00:00+08:00")


class FakeScheduler(Scheduler):
    """Scheduler with S1 dispatch replaced by an in-memory recorder."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.s1_calls = []

    async def _post_s1(self, event_id: str, attempt: int):
        self.s1_calls.append({"event_id": event_id, "attempt": attempt})
        return {"status": "SUCCESS", "event_id": event_id, "attempt": attempt}


class FailingS1Scheduler(Scheduler):
    """Scheduler whose S1 always returns a non-terminal status."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.s1_calls = []

    async def _post_s1(self, event_id: str, attempt: int):
        self.s1_calls.append({"event_id": event_id, "attempt": attempt})
        return {"status": "RETRYING", "event_id": event_id, "attempt": attempt}


# ── helpers ──────────────────────────────────────────────────────────────


async def _install(admin) -> None:
    async with admin.begin() as conn:
        await install_core(conn)
        await install_obligations(conn)


async def _make_scheduler(db_url: str, *, fake_cls=FakeScheduler) -> FakeScheduler:
    sched = fake_cls(
        engine(db_url, poolclass=NullPool),
        s1_url="http://gateway:8200",
        s1_token="test-token",
    )
    return sched


# ── scan_due ─────────────────────────────────────────────────────────────


class TestScanDue:
    def test_scan_creates_jobs_for_due_instructions(self, obligations_db):
        """scan_due creates one PENDING event job for each due SCHEDULED_TRANSFER."""

        async def check():
            admin = engine(obligations_db.admin_url, poolclass=NullPool)
            await _install(admin)
            sched = await _make_scheduler(obligations_db.runtime_url)

            async with admin.begin() as conn:
                created = await sched.scan_due(conn, _tomorrow_6am() + timedelta(days=1))
            assert created >= 1

            async with admin.connect() as conn:
                jobs = (
                    (
                        await conn.execute(
                            text(
                                "SELECT kind, status, due_at, source_id, period_key "
                                "FROM event_jobs WHERE kind='SCHEDULED_TRANSFER'"
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
                assert len(jobs) >= 1
                for j in jobs:
                    assert j["status"] == "PENDING"
                    assert j["source_id"] is not None
                    assert j["period_key"] is not None

            await admin.dispose()

        asyncio.run(check())

    def test_scan_due_is_idempotent(self, obligations_db):
        """A second scan_due over the same window inserts no new rows."""

        async def check():
            admin = engine(obligations_db.admin_url, poolclass=NullPool)
            await _install(admin)
            sched = await _make_scheduler(obligations_db.runtime_url)
            until = _tomorrow_6am() + timedelta(days=1)

            async with admin.begin() as conn:
                first = await sched.scan_due(conn, until)
            async with admin.begin() as conn:
                second = await sched.scan_due(conn, until)
            assert second == 0, f"idempotent scan should return 0, got {second}"
            assert first > 0

            await admin.dispose()

        asyncio.run(check())

    def test_scan_creates_merchant_charge_jobs(self, obligations_db):
        """scan_due creates MERCHANT_CHARGE jobs for billings and SCHEDULED_TRANSFER for SI."""

        async def check():
            admin = engine(obligations_db.admin_url, poolclass=NullPool)
            await _install(admin)
            # Seed SCHEDULED_TRANSFER is due 2026-11-05; billings have next_due_at in Oct.
            # Scan window must cover both.
            until = _tomorrow_6am() + timedelta(days=1)
            sched = await _make_scheduler(obligations_db.runtime_url)
            async with admin.begin() as conn:
                await sched.scan_due(conn, until)

            async with admin.connect() as conn:
                si_jobs = (
                    await conn.execute(
                        text("SELECT count(*) FROM event_jobs WHERE kind='SCHEDULED_TRANSFER'")
                    )
                ).scalar()
                charge_jobs = (
                    await conn.execute(
                        text("SELECT count(*) FROM event_jobs WHERE kind='MERCHANT_CHARGE'")
                    )
                ).scalar()
            # At least one of each kind should be created.
            assert si_jobs >= 1
            assert charge_jobs >= 1

            await admin.dispose()

        asyncio.run(check())


# ── run_once ─────────────────────────────────────────────────────────────


class TestRunOnce:
    def test_run_once_leases_and_dispatches(self, obligations_db):
        """run_once leases a PENDING job, calls S1 once, and marks it DONE."""

        async def check():
            admin = engine(obligations_db.admin_url, poolclass=NullPool)
            await _install(admin)
            sched = await _make_scheduler(obligations_db.runtime_url)
            until = _tomorrow_6am() + timedelta(days=1)

            # Advance clock to after due_at so jobs are eligible.
            async with admin.begin() as conn:
                await conn.execute(
                    update(m.clock)
                    .where(m.clock.c.id == 1)
                    .values(
                        sim_now=_tomorrow_6am() + timedelta(hours=1),
                        version=2,
                    )
                )
                await sched.scan_due(conn, until)

            async with admin.connect() as conn:
                pending = await conn.scalar(
                    text("SELECT count(*) FROM event_jobs WHERE status='PENDING'")
                )
            assert pending >= 1

            result = await sched.run_once()
            assert result is not None
            assert result["status"] == "SUCCESS"
            assert len(sched.s1_calls) == 1

            # Job should now be DONE.
            async with admin.connect() as conn:
                done = await conn.scalar(
                    text("SELECT count(*) FROM event_jobs WHERE status='DONE'")
                )
            assert done == 1

            await admin.dispose()

        asyncio.run(check())

    def test_run_once_returns_none_when_nothing_due(self, obligations_db):
        """run_once returns None when no jobs are within the current clock."""

        async def check():
            admin = engine(obligations_db.admin_url, poolclass=NullPool)
            await _install(admin)
            sched = await _make_scheduler(obligations_db.runtime_url)
            until = _tomorrow_6am() + timedelta(days=1)

            async with admin.begin() as conn:
                await sched.scan_due(conn, until)
            # Clock still at NOW (before any job is due).
            result = await sched.run_once()
            assert result is None

            await admin.dispose()

        asyncio.run(check())

    def test_run_once_requeues_on_retrying_response(self, obligations_db):
        """When S1 returns non-terminal status, the job goes back to RETRYING."""

        async def check():
            admin = engine(obligations_db.admin_url, poolclass=NullPool)
            await _install(admin)
            sched = await _make_scheduler(obligations_db.runtime_url, fake_cls=FailingS1Scheduler)
            until = _tomorrow_6am() + timedelta(days=1)
            async with admin.begin() as conn:
                await conn.execute(
                    update(m.clock)
                    .where(m.clock.c.id == 1)
                    .values(
                        sim_now=_tomorrow_6am() + timedelta(hours=1),
                        version=2,
                    )
                )
                await sched.scan_due(conn, until)

            result = await sched.run_once()
            assert result is not None
            assert result["status"] == "RETRYING"
            assert len(sched.s1_calls) == 1

            async with admin.connect() as conn:
                retry = await conn.scalar(
                    text("SELECT count(*) FROM event_jobs WHERE status='RETRYING'")
                )
            assert retry == 1

            await admin.dispose()

        asyncio.run(check())

    def test_two_concurrent_run_once_never_double_process(self, obligations_db):
        """With skip_locked, two concurrent run_once threads each get a different job."""

        async def check():
            admin = engine(obligations_db.admin_url, poolclass=NullPool)
            await _install(admin)
            until = _tomorrow_6am() + timedelta(days=30)

            # Create two due standing instructions with different next_run_at.
            async with admin.begin() as conn:
                await conn.execute(
                    update(m.clock)
                    .where(m.clock.c.id == 1)
                    .values(
                        sim_now=_tomorrow_6am() + timedelta(days=30),
                        version=5,
                    )
                )
                sched_scan = await _make_scheduler(obligations_db.runtime_url)
                await sched_scan.scan_due(conn, until)

            # Both schedulers share the same DB but different instances.
            sched1 = await _make_scheduler(obligations_db.runtime_url)
            sched2 = await _make_scheduler(obligations_db.runtime_url)

            r1, r2 = await asyncio.gather(sched1.run_once(), sched2.run_once())
            if r1 is not None and r2 is not None:
                ids = {r1["event_id"], r2["event_id"]}
                assert len(ids) == 2, f"should process different jobs, got {ids}"

            # At most 2 jobs processed.
            async with admin.connect() as conn:
                q = "SELECT count(*) FROM event_jobs WHERE status='DONE'"
                done = await conn.scalar(text(q))
            assert done <= 2

            await admin.dispose()

        asyncio.run(check())


# ── recover ──────────────────────────────────────────────────────────────


class TestRecover:
    def test_recover_resets_expired_running_jobs(self, obligations_db):
        """recover resets RUNNING jobs with expired leases to RETRYING."""

        async def check():
            admin = engine(obligations_db.admin_url, poolclass=NullPool)
            await _install(admin)
            sched = await _make_scheduler(obligations_db.runtime_url)
            until = _tomorrow_6am() + timedelta(days=1)

            # Manually set a job to RUNNING with an expired lease.
            async with admin.begin() as conn:
                await conn.execute(
                    update(m.clock)
                    .where(m.clock.c.id == 1)
                    .values(
                        sim_now=_tomorrow_6am() + timedelta(hours=1),
                        version=2,
                    )
                )
                await sched.scan_due(conn, until)
                # Pick one job and set it RUNNING with expired lease.
                row = (
                    await conn.execute(
                        text(
                            "SELECT id FROM event_jobs "
                            "WHERE status='PENDING' AND kind='SCHEDULED_TRANSFER' LIMIT 1"
                        )
                    )
                ).scalar_one()
                await conn.execute(
                    update(m.event_jobs)
                    .where(m.event_jobs.c.id == row)
                    .values(
                        status="RUNNING",
                        lease_until_wall=datetime.now(UTC) - timedelta(seconds=60),
                        attempt_count=1,
                    )
                )

            recovered = await sched.recover()
            assert recovered == 1

            async with admin.connect() as conn:
                status = (
                    await conn.execute(text(f"SELECT status FROM event_jobs WHERE id='{row}'"))
                ).scalar()
                assert status == "RETRYING"

            await admin.dispose()

        asyncio.run(check())

    def test_recover_ignores_active_leases(self, obligations_db):
        """RUNNING jobs whose lease hasn't expired are left alone."""

        async def check():
            admin = engine(obligations_db.admin_url, poolclass=NullPool)
            await _install(admin)
            sched = await _make_scheduler(obligations_db.runtime_url)
            until = _tomorrow_6am() + timedelta(days=1)

            async with admin.begin() as conn:
                await conn.execute(
                    update(m.clock)
                    .where(m.clock.c.id == 1)
                    .values(
                        sim_now=_tomorrow_6am() + timedelta(hours=1),
                        version=2,
                    )
                )
                await sched.scan_due(conn, until)
                row = (
                    await conn.execute(
                        text(
                            "SELECT id FROM event_jobs "
                            "WHERE status='PENDING' AND kind='SCHEDULED_TRANSFER' LIMIT 1"
                        )
                    )
                ).scalar_one()
                await conn.execute(
                    update(m.event_jobs)
                    .where(m.event_jobs.c.id == row)
                    .values(
                        status="RUNNING",
                        lease_until_wall=datetime.now(UTC) + timedelta(seconds=120),
                        attempt_count=1,
                    )
                )

            recovered = await sched.recover()
            assert recovered == 0

            async with admin.connect() as conn:
                status = (
                    await conn.execute(text(f"SELECT status FROM event_jobs WHERE id='{row}'"))
                ).scalar()
                assert status == "RUNNING"

            await admin.dispose()

        asyncio.run(check())


# ── ClockAdvancer ────────────────────────────────────────────────────────


class TestClockAdvance:
    def test_advance_moves_clock_and_processes_jobs(self, obligations_db):
        """advance steps clock forward to each due event and runs due jobs."""

        async def check():
            admin = engine(obligations_db.admin_url, poolclass=NullPool)
            await _install(admin)
            sched = await _make_scheduler(obligations_db.runtime_url)
            target = _tomorrow_6am() + timedelta(days=7)

            # Scan jobs for the period.
            async with admin.begin() as conn:
                await sched.scan_due(conn, target)

            advancer = ClockAdvancer(sched)
            job_id = advancer.advance(target, "req-1")
            assert job_id is not None

            # Let the background task finish.
            await asyncio.sleep(2)

            status = advancer.status(job_id)
            assert status is not None
            assert status["status"] == "DONE"
            assert status["sim_now"] is not None
            assert status["processed"] >= 1

            # Clock must have moved forward.
            async with admin.connect() as conn:
                sim_now, version = await sched.read_clock(conn)
                assert sim_now >= _tomorrow_6am()
                assert version > 1

            await admin.dispose()

        asyncio.run(check())

    def test_advance_never_moves_backward(self, obligations_db):
        """advance refuses to move clock to a past time."""

        async def check():
            admin = engine(obligations_db.admin_url, poolclass=NullPool)
            await _install(admin)
            sched = await _make_scheduler(obligations_db.runtime_url)

            # Target is before current clock.
            past_target = NOW - timedelta(days=1)
            advancer = ClockAdvancer(sched)
            job_id = advancer.advance(past_target, "req-past")
            assert job_id is not None

            await asyncio.sleep(1)
            status = advancer.status(job_id)
            assert status["status"] == "DONE"
            assert status["sim_now"] is not None

            async with admin.connect() as conn:
                sim_now, _ = await sched.read_clock(conn)
                # Clock must not have moved backward.
                assert sim_now >= NOW

            await admin.dispose()

        asyncio.run(check())

    def test_advance_idempotent_request_id(self, obligations_db):
        """Same request_id returns the same advance job id."""

        async def check():
            admin = engine(obligations_db.admin_url, poolclass=NullPool)
            await _install(admin)
            sched = await _make_scheduler(obligations_db.runtime_url)

            advancer = ClockAdvancer(sched)
            j1 = advancer.advance(NOW + timedelta(days=1), "dup-req")
            j2 = advancer.advance(NOW + timedelta(days=1), "dup-req")
            assert j1 == j2, f"same request_id must return same job id: {j1} != {j2}"

            await admin.dispose()

        asyncio.run(check())

    def test_advance_unknown_job_returns_none(self, obligations_db):
        """status() returns None for unknown job IDs."""

        async def check():
            admin = engine(obligations_db.admin_url, poolclass=NullPool)
            await _install(admin)
            sched = await _make_scheduler(obligations_db.runtime_url)

            advancer = ClockAdvancer(sched)
            assert advancer.status("no-such-job") is None

            await admin.dispose()

        asyncio.run(check())


async def _wait_advance(advancer, job):
    for _ in range(100):
        result = advancer.status(job)
        if result["status"] in {"DONE", "FAILED", "RETRYING"}:
            return result
        await asyncio.sleep(0.02)
    raise AssertionError("advance never reached a bounded outcome")


def test_clock_reaches_target_with_no_events(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
        sched = await _make_scheduler(obligations_db.runtime_url)
        adv = ClockAdvancer(sched)
        target = NOW + timedelta(minutes=40)
        result = await _wait_advance(adv, adv.advance(target, "empty-window"))
        assert result["status"] == "DONE"
        assert result["sim_now"] == target
        async with admin.connect() as conn:
            assert (await sched.read_clock(conn))[0] == target
        await admin.dispose()

    asyncio.run(check())


def test_clock_scans_future_obligations_without_manual_prescan(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        await _install(admin)
        sched = await _make_scheduler(obligations_db.runtime_url)
        adv = ClockAdvancer(sched)
        target = _tomorrow_6am() + timedelta(days=1)
        result = await _wait_advance(adv, adv.advance(target, "future-scan"))
        assert result["status"] == "DONE"
        assert result["processed"] > 0
        assert result["sim_now"] == target
        await admin.dispose()

    asyncio.run(check())


def test_clock_request_id_cannot_change_target(obligations_db):
    from app.errors import BankError

    async def check():
        sched = await _make_scheduler(obligations_db.runtime_url)
        adv = ClockAdvancer(sched)
        adv.advance(NOW + timedelta(days=1), "same-id")
        with pytest.raises(BankError):
            adv.advance(NOW + timedelta(days=2), "same-id")
        await sched.db.dispose()

    asyncio.run(check())


def test_clock_request_requires_timezone():
    from app.scheduler import ClockAdvanceRequest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ClockAdvanceRequest(target_at="2026-10-15T09:00:00", request_id="naive")


def test_clock_api_shape_backward_rejection_and_replay(obligations_db):
    import httpx
    from app.main import create_app

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        app = create_app(
            runtime,
            service_token="clock-token",
            fingerprint_key="test-key",
            demo_mode=True,
            admin_db=admin,
            scheduler_enabled=False,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://bank",
            headers={"X-Service-Token": "clock-token"},
        ) as c:
            body = {
                "request_id": "http-shape",
                "target_at": (NOW + timedelta(minutes=40)).isoformat(),
            }
            response = await c.post("/internal/clock/advance", json=body)
            assert response.status_code == 200
            job = response.json()["advance_job_id"]
            for _ in range(100):
                status = (await c.get("/internal/clock/advances/" + job)).json()
                if status["status"] == "DONE":
                    break
                await asyncio.sleep(0.02)
            assert status["status"] == "DONE" and status["remaining"] == 0
            assert (await c.post("/internal/clock/advance", json=body)).json() == response.json()
            conflict = await c.post(
                "/internal/clock/advance",
                json={**body, "target_at": (NOW + timedelta(days=1)).isoformat()},
            )
            assert conflict.status_code == 409
            past = await c.post(
                "/internal/clock/advance", json={"request_id": "past", "target_at": NOW.isoformat()}
            )
            assert past.status_code == 422
        await runtime.dispose()
        await admin.dispose()

    asyncio.run(check())


def test_clock_retrying_is_not_done_and_same_request_can_resume(obligations_db, monkeypatch):
    monkeypatch.setattr("app.scheduler._backoff", lambda attempt: 0.01)

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        await _install(admin)
        sched = await _make_scheduler(obligations_db.runtime_url, fake_cls=FailingS1Scheduler)
        adv = ClockAdvancer(sched)
        target = _tomorrow_6am() + timedelta(days=1)
        job = adv.advance(target, "retry-original")
        result = await _wait_advance(adv, job)
        assert result["status"] == "RETRYING" and result["remaining"] > 0

        async def recovered(event_id, attempt):
            return {"status": "SUCCESS", "event_id": event_id}

        sched._post_s1 = recovered
        assert adv.advance(target, "retry-original") == job
        result = await _wait_advance(adv, job)
        assert result["status"] == "DONE" and result["sim_now"] == target
        await sched.db.dispose()
        await admin.dispose()

    asyncio.run(check())


def test_scan_does_not_duplicate_business_timezone_job(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        await _install(admin)
        due = _tomorrow_6am()
        async with admin.begin() as conn:
            await conn.execute(
                m.event_jobs.insert().values(
                    id="precreated_business_zone_job",
                    kind="SCHEDULED_TRANSFER",
                    rank=3,
                    due_at=due,
                    immutable_payload={},
                    payload_hash="existing-hash",
                    source_id="si_zs_mom",
                    period_key=due.isoformat(),
                    status="PENDING",
                    attempt_count=0,
                )
            )
        sched = await _make_scheduler(obligations_db.runtime_url)
        for _ in range(2):
            async with admin.begin() as conn:
                await sched.scan_due(conn, due)
        async with admin.connect() as conn:
            rows = (
                await conn.execute(
                    text("select id from event_jobs where source_id='si_zs_mom' and due_at=:due"),
                    {"due": due},
                )
            ).all()
        assert len(rows) == 1
        await admin.dispose()

    asyncio.run(check())


def test_background_dispatch_cannot_overtake_same_time_lower_rank(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            for i in (0, 1):
                await conn.execute(
                    m.event_jobs.insert().values(
                        id=f"rank_job_{i}",
                        kind="DEMO_EVENT",
                        rank=i,
                        due_at=NOW,
                        immutable_payload={},
                        payload_hash=f"rank_hash_{i}",
                        source_id=f"rank_src_{i}",
                        period_key="same-time",
                        status="PENDING",
                        attempt_count=0,
                    )
                )
        sched = await _make_scheduler(obligations_db.runtime_url)
        first_started, release_first = asyncio.Event(), asyncio.Event()
        started = []

        async def slow(event_id, attempt):
            started.append(event_id)
            if event_id == "rank_job_0":
                first_started.set()
                await release_first.wait()
            return {"status": "SUCCESS", "event_id": event_id}

        sched._post_s1 = slow
        background = asyncio.create_task(sched.run_once())
        await first_started.wait()
        advance = asyncio.create_task(sched.run_once())
        await asyncio.sleep(0.05)
        try:
            assert started == ["rank_job_0"]
        finally:
            release_first.set()
            await asyncio.gather(background, advance)
        assert started == ["rank_job_0", "rank_job_1"]
        await sched.db.dispose()
        await admin.dispose()

    asyncio.run(check())


def test_clock_counts_job_completed_by_background_dispatch(obligations_db):
    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        due = NOW + timedelta(hours=1)
        async with admin.begin() as conn:
            await install_core(conn)
            await conn.execute(
                m.event_jobs.insert().values(
                    id="background_blocked",
                    kind="DEMO_EVENT",
                    rank=6,
                    due_at=due,
                    immutable_payload={},
                    payload_hash="background-hash",
                    source_id="background-src",
                    period_key="background-period",
                    status="PENDING",
                    attempt_count=0,
                )
            )
        sched = await _make_scheduler(obligations_db.runtime_url)

        async def blocked(event_id, attempt):
            async with admin.begin() as conn:
                await conn.execute(
                    m.event_executions.insert().values(
                        event_id=event_id,
                        status="BLOCKED",
                        authorization_evidence={},
                        result_json={"status": "BLOCKED"},
                        committed_at=datetime.now(UTC),
                    )
                )
            return {"event_id": event_id, "status": "BLOCKED"}

        sched._post_s1 = blocked
        original = sched.run_once
        first = True

        async def background_won_claim():
            nonlocal first
            if first:
                first = False
                await asyncio.create_task(original())
                return None  # the advance worker itself did not claim a job
            return await original()

        sched.run_once = background_won_claim
        adv = ClockAdvancer(sched)
        result = await _wait_advance(
            adv, adv.advance(due + timedelta(minutes=1), "background-count")
        )
        assert result["status"] == "DONE"
        assert result["processed"] == 1 and result["blocked"] == 1
        await sched.db.dispose()
        await admin.dispose()

    asyncio.run(check())
