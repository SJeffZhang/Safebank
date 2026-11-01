"""Reminder definition, scheduler inserts and clock boundaries; no DB or network."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from app.scheduler import ClockAdvancer, Scheduler
from sqlalchemy.dialects import postgresql


def at(value):
    return datetime.fromisoformat(value)


SCAN = at("2026-10-15T09:00:00+08:00")
DUE = at("2026-10-18T06:00:00+08:00")


def billing():
    return {
        "id": "bil_one",
        "customer_id": "cus_one",
        "account_id": "acc_one",
        "merchant_id": "mer_one",
        "amount_cents": 3000,
        "next_due_at": DUE,
        "active": True,
        "created_at": SCAN - timedelta(days=30),
        "period": "MONTHLY",
        "anchor_day": 18,
        "weekday": None,
        "time_of_day": DUE.time(),
        "merchant_name": "EVIL商户",
        "memo": "EVIL自由文本",
    }


def mandate():
    return {
        "id": "si_one",
        "customer_id": "cus_one",
        "kind": "MERCHANT_MANDATE",
        "status": "ACTIVE",
        "from_account_id": "acc_one",
        "billing_id": "bil_one",
        "merchant_id": "mer_one",
        "max_amount_cents": 10000,
        "end_at": None,
        "created_at": SCAN - timedelta(days=30),
    }


def build(bill=None, consent=None, scan=SCAN):
    from app.reminders import build_reminder_job

    return build_reminder_job(billing=bill or billing(), mandate=consent or mandate(), scan_at=scan)


def test_reminder_job_contains_only_authoritative_minimal_payload_and_rank():
    from safebank_bankcore.ids import digest

    job = build()
    assert job["kind"] == "REMINDER" and job["rank"] == 5 and job["status"] == "PENDING"
    assert job["due_at"] == SCAN
    payload = job["immutable_payload"]["payload"]
    assert payload == {
        "billing_id": "bil_one",
        "customer_id": "cus_one",
        "account_id": "acc_one",
        "amount_cents": 3000,
        "charge_due_at": DUE.astimezone(UTC).isoformat(),
        "instruction_id": "si_one",
    }
    assert job["payload_hash"] == digest(payload)
    assert "EVIL" not in str(job)


def test_repeat_scans_keep_one_key_per_billing_and_specific_charge_instant():
    first = build()
    repeated = build(scan=SCAN + timedelta(days=1))
    final = build(scan=SCAN + timedelta(days=2))
    assert first["id"] == repeated["id"] == final["id"]
    assert first["period_key"] == repeated["period_key"] == final["period_key"]
    other = billing()
    other["next_due_at"] = DUE + timedelta(days=7)
    later = build(other, scan=SCAN + timedelta(days=7))
    assert later["id"] != first["id"]
    # Equivalent RFC3339 instants cannot split the idempotency key.
    other["next_due_at"] = DUE.astimezone(UTC)
    assert build(other)["id"] == first["id"]


@pytest.mark.parametrize(
    "offset,eligible",
    [
        (timedelta(0), False),
        (timedelta(microseconds=-1), False),
        (timedelta(microseconds=1), True),
        (timedelta(days=3), True),
        (timedelta(days=3, microseconds=1), False),
    ],
)
def test_three_day_window_is_open_at_scan_and_closed_at_exact_72_hours(offset, eligible):
    bill = billing()
    bill["next_due_at"] = SCAN + offset
    assert (build(bill) is not None) is eligible


@pytest.mark.parametrize("clock", [SCAN - timedelta(minutes=1), SCAN + timedelta(minutes=1)])
def test_job_builder_never_fabricates_a_non_0900_scan(clock):
    assert build(scan=clock) is None


@pytest.mark.parametrize(
    "fault",
    [
        "billing_inactive",
        "deleted",
        "wrong_customer",
        "wrong_account",
        "wrong_merchant",
        "wrong_billing",
        "expired",
        "above_cap",
        "future_bill",
        "future_consent",
        "float_amount",
        "bool_amount",
        "naive_due",
    ],
)
def test_invalid_or_cancelled_authorization_cannot_generate_reminder(fault):
    bill, consent = billing(), mandate()
    if fault == "billing_inactive":
        bill["active"] = False
    elif fault == "deleted":
        consent["status"] = "DELETED"
    elif fault == "wrong_customer":
        consent["customer_id"] = "cus_other"
    elif fault == "wrong_account":
        consent["from_account_id"] = "acc_other"
    elif fault == "wrong_merchant":
        consent["merchant_id"] = "mer_other"
    elif fault == "wrong_billing":
        consent["billing_id"] = "bil_other"
    elif fault == "expired":
        consent["end_at"] = DUE - timedelta(microseconds=1)
    elif fault == "above_cap":
        consent["max_amount_cents"] = 2999
    elif fault == "future_bill":
        bill["created_at"] = SCAN + timedelta(minutes=1)
    elif fault == "future_consent":
        consent["created_at"] = SCAN + timedelta(minutes=1)
    elif fault == "float_amount":
        bill["amount_cents"] = 3000.0
    elif fault == "bool_amount":
        bill["amount_cents"] = True
    else:
        bill["next_due_at"] = DUE.replace(tzinfo=None)
    assert build(bill, consent) is None


def test_execution_recheck_rejects_revocation_or_changed_due_amount_and_owner():
    from app.reminders import reminder_is_current

    job = build()
    payload = job["immutable_payload"]["payload"]
    assert reminder_is_current(payload, billing(), mandate(), SCAN)
    for field, changed in (
        ("active", False),
        ("amount_cents", 4000),
        ("customer_id", "cus_other"),
        ("next_due_at", DUE + timedelta(days=7)),
    ):
        bill = billing()
        bill[field] = changed
        assert not reminder_is_current(payload, bill, mandate(), SCAN)
    consent = mandate()
    consent["status"] = "DELETED"
    assert not reminder_is_current(payload, billing(), consent, SCAN)
    assert not reminder_is_current(payload, billing(), mandate(), DUE)


@pytest.mark.parametrize(
    "clock,expected",
    [
        ("2026-10-15T08:59:00+08:00", "2026-10-15T09:00:00+08:00"),
        ("2026-10-15T09:00:00+08:00", "2026-10-16T09:00:00+08:00"),
        ("2026-10-15T23:59:00+08:00", "2026-10-16T09:00:00+08:00"),
        ("2026-10-16T00:59:00+00:00", "2026-10-16T09:00:00+08:00"),
    ],
)
def test_next_scan_is_a_daily_shanghai_0900_boundary(clock, expected):
    from app.reminders import next_reminder_scan

    assert next_reminder_scan(at(clock)) == at(expected)


class Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def scalars(self):
        return self

    def all(self):
        return self.rows

    def one(self):
        assert len(self.rows) == 1
        return self.rows[0]


class ScanConn:
    def __init__(self, now, candidates=None):
        self.now = now
        self.candidates = candidates or [(billing(), mandate())]
        self.jobs = {}
        self.sql = []

    async def execute(self, statement):
        compiled = statement.compile(dialect=postgresql.dialect())
        query, params = str(compiled), compiled.params
        self.sql.append(query)
        if statement.is_insert:
            key = (params["source_id"], params["period_key"])
            if key in self.jobs:
                return SimpleNamespace(rowcount=0)
            self.jobs[key] = params
            return SimpleNamespace(rowcount=1)
        if "FROM clock" in query:
            return Result([{"sim_now": self.now, "version": 1}])
        if "merchant_billing JOIN standing_instructions" in query:
            rows = []
            for bill, consent in self.candidates:
                rows.append(
                    {
                        **{f"merchant_billing_{k}": v for k, v in bill.items()},
                        **{f"standing_instructions_{k}": v for k, v in consent.items()},
                    }
                )
            return Result(rows)
        return Result([])


def test_scheduler_scan_before_0900_and_repeated_recovery_only_insert_once():
    async def check():
        conn = ScanConn(SCAN - timedelta(minutes=1))
        scheduler = Scheduler(None, s1_url="http://unused", s1_token="synthetic")
        assert await scheduler.scan_reminders(conn, conn.now) == 0
        conn.now = SCAN
        assert await scheduler.scan_reminders(conn, conn.now) == 1
        assert await scheduler.scan_reminders(conn, conn.now) == 0
        assert await scheduler.scan_reminders(conn, conn.now + timedelta(days=1)) == 0
        job = next(iter(conn.jobs.values()))
        assert job["due_at"] == SCAN
        assert any("ON CONFLICT DO NOTHING" in sql for sql in conn.sql)
        sql = next(sql for sql in conn.sql if "merchant_billing JOIN standing_instructions" in sql)
        assert "standing_instructions.customer_id = merchant_billing.customer_id" in sql
        assert "standing_instructions.status" in sql
        assert "merchant_billing.active IS true" in sql

    asyncio.run(check())


def test_scheduler_scan_due_uses_current_clock_for_reminders_not_future_target():
    async def check():
        conn = ScanConn(SCAN - timedelta(minutes=1))
        scheduler = Scheduler(None, s1_url="http://unused", s1_token="synthetic")
        await scheduler.scan_due(conn, SCAN + timedelta(days=10))
        assert conn.jobs == {}
        conn.now = SCAN
        await scheduler.scan_due(conn, SCAN + timedelta(days=10))
        assert len(conn.jobs) == 1
        assert next(iter(conn.jobs.values()))["due_at"] == SCAN

    asyncio.run(check())


class ClockConn:
    def __init__(self, owner):
        self.owner = owner

    async def execute(self, statement):
        compiled = statement.compile(dialect=postgresql.dialect())
        query, params = str(compiled), compiled.params
        if "FROM clock" in query:
            return Result([{"sim_now": self.owner.now, "version": self.owner.version}])
        if "outcome" in query:
            return Result(
                [
                    {
                        "status": job["status"],
                        "outcome": "SUCCESS" if job["status"] == "DONE" else None,
                    }
                    for job in self.owner.jobs.values()
                ]
            )
        target = params.get("due_at_1", self.owner.target)
        return Result(
            [
                job["id"]
                for job in self.owner.jobs.values()
                if job["status"] != "DONE" and job["due_at"] <= target
            ]
        )

    async def scalar(self, statement):
        compiled = statement.compile(dialect=postgresql.dialect())
        target = compiled.params.get("due_at_1", self.owner.target)
        values = [
            job["due_at"]
            for job in self.owner.jobs.values()
            if job["status"] != "DONE" and job["due_at"] <= target
        ]
        return len(values) if "count(" in str(compiled) else min(values, default=None)


class MemoryClockScheduler(Scheduler):
    def __init__(self, *, target, has_billing=True):
        self.now = SCAN - timedelta(minutes=1)
        self.version = 1
        self.target = target
        self.jobs = {}
        self.scans = []
        self.dispatched = []
        self.has_billing = has_billing
        self.db = self

    @asynccontextmanager
    async def begin(self):
        yield ClockConn(self)

    connect = begin

    async def read_clock(self, conn):
        return self.now, self.version

    async def set_clock(self, conn, sim_now, version):
        self.now, self.version = sim_now, version

    async def scan_due(self, conn, until):
        self.scans.append(self.now)
        if self.has_billing:
            from app.reminders import reminder_scan_at

            scan = reminder_scan_at(self.now)
            job = build(scan=scan) if scan else None
            if job:
                self.jobs.setdefault(job["id"], job)
        return 0

    async def run_once(self):
        pending = [
            job
            for job in self.jobs.values()
            if job["status"] != "DONE" and job["due_at"] <= self.now
        ]
        if not pending:
            return None
        job = min(pending, key=lambda item: (item["due_at"], item["rank"], item["id"]))
        job["status"] = "DONE"
        self.dispatched.append((self.now, deepcopy(job)))
        return {"event_id": job["id"], "status": "SUCCESS"}


@pytest.mark.parametrize("has_billing", [True, False])
def test_multiday_clock_advance_visits_each_0900_even_with_empty_event_queue(has_billing):
    async def check():
        target = SCAN + timedelta(days=4, hours=1)
        scheduler = MemoryClockScheduler(target=target, has_billing=has_billing)
        advancer = ClockAdvancer(scheduler)
        job_id = advancer.advance(target, "req_clock")
        for _ in range(100):
            if advancer.status(job_id)["status"] in ("DONE", "FAILED", "RETRYING"):
                break
            await asyncio.sleep(0)
        assert advancer.status(job_id)["status"] == "DONE"
        assert scheduler.now == target
        for day in range(5):
            assert SCAN + timedelta(days=day) in scheduler.scans
        if has_billing:
            assert len(scheduler.dispatched) == 1
            assert scheduler.dispatched[0][0] == SCAN
        else:
            assert scheduler.dispatched == []

    asyncio.run(check())


def test_clock_target_before_0900_does_not_create_or_dispatch_reminder():
    async def check():
        target = SCAN - timedelta(seconds=1)
        scheduler = MemoryClockScheduler(target=target)
        advancer = ClockAdvancer(scheduler)
        job_id = advancer.advance(target, "req_before9")
        for _ in range(20):
            if advancer.status(job_id)["status"] == "DONE":
                break
            await asyncio.sleep(0)
        assert advancer.status(job_id)["status"] == "DONE"
        assert scheduler.jobs == {} and scheduler.dispatched == []
        assert scheduler.now == target

    asyncio.run(check())


def test_0900_scan_materializes_reminder_before_dispatching_same_time_ranked_events():
    async def check():
        scheduler = MemoryClockScheduler(target=SCAN)
        scheduler.jobs["evt_fund"] = {
            "id": "evt_fund",
            "kind": "FUND_CONFIRM",
            "rank": 0,
            "due_at": SCAN,
            "status": "PENDING",
        }
        advancer = ClockAdvancer(scheduler)
        job_id = advancer.advance(SCAN, "req_rank")
        for _ in range(50):
            if advancer.status(job_id)["status"] == "DONE":
                break
            await asyncio.sleep(0)
        assert advancer.status(job_id)["status"] == "DONE"
        assert [job["rank"] for _, job in scheduler.dispatched] == [0, 5]
        assert all(dispatched_at == SCAN for dispatched_at, _ in scheduler.dispatched)

    asyncio.run(check())


def test_scheduler_rejects_cross_owner_candidate_even_if_a_query_returns_it():
    async def check():
        wrong = mandate()
        wrong["customer_id"] = "cus_other"
        conn = ScanConn(SCAN, [(billing(), wrong)])
        scheduler = Scheduler(None, s1_url="http://unused", s1_token="synthetic")
        assert await scheduler.scan_reminders(conn, SCAN) == 0
        assert conn.jobs == {}

    asyncio.run(check())
