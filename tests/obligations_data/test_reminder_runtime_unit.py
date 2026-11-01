"""L3 reminder event boundary exercised with SQL AST-backed memory rows, no DB."""

import asyncio
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest
from app.errors import BankError
from app.events import EventService
from app.reminders import build_reminder_job
from safebank_contracts.codes import Code
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.elements import BindParameter

from tests.bankcore.fixtures import NOW
from tests.bankcore.obligations_fixtures import raw_billing, raw_mandate


class Rows:
    def __init__(self, values, first_key=None):
        self.values, self.first_key = values, first_key

    def mappings(self):
        return self

    def first(self):
        return self.values[0] if self.values else None

    one_or_none = first

    def all(self):
        return self.values

    def scalar(self):
        return self.first().get(self.first_key) if self.values else None

    def scalars(self):
        return self

    def __iter__(self):
        return iter(row[self.first_key] for row in self.values)


def _value(expr, row):
    return expr.value if isinstance(expr, BindParameter) else row[expr.name]


class BankMemory:
    def __init__(self):
        bill = raw_billing().model_dump(mode="python")
        mandate = raw_mandate().model_dump(mode="python")
        job = build_reminder_job(billing=bill, mandate=mandate, scan_at=NOW)
        self.job = job
        self.tables = {
            "event_jobs": [job],
            "merchant_billing": [bill],
            "standing_instructions": [mandate],
            "customers": [{"id": "cus_a"}],
            "clock": [{"id": 1, "sim_now": NOW, "version": 7}],
            "state_versions": [{"customer_id": "cus_a", "version": 11}],
            "event_executions": [],
            "plan_executions": [],
            "operation_events": [],
            "fund_orders": [],
            "accounts": [{"id": "acc_a", "balance_cents": 600000}],
        }
        self.writes = []

    async def execute(self, query):
        if (
            not getattr(query, "is_select", False)
            and not getattr(query, "is_insert", False)
            and not getattr(query, "is_update", False)
        ):
            return Rows([])
        if query.is_select:
            table = query.get_final_froms()[0]
            rows = self.tables.get(table.name, [])
            rows = [
                row
                for row in rows
                if all(
                    clause.operator(_value(clause.left, row), _value(clause.right, row))
                    for clause in query._where_criteria
                )
            ]
            first_key = next(iter(query.selected_columns)).name
            return Rows(rows, first_key)
        table = query.table.name
        self.writes.append(table)
        params = query.compile(dialect=postgresql.dialect()).params
        if query.is_insert:
            self.tables[table].append(params)
        else:
            for row in self.tables[table]:
                if all(
                    clause.operator(_value(clause.left, row), _value(clause.right, row))
                    for clause in query._where_criteria
                ):
                    for column, value in query._values.items():
                        row[column if isinstance(column, str) else column.name] = value.value
        return SimpleNamespace(rowcount=1)

    async def scalar(self, query):
        return (await self.execute(query)).scalar()


def evidence(db):
    return {
        "type": "SYSTEM_REMINDER",
        "billing_id": db.job["source_id"],
        "instruction_id": db.job["immutable_payload"]["payload"]["instruction_id"],
    }


async def execute(db, **overrides):
    args = dict(
        conn=db,
        event_id=db.job["id"],
        expected_versions={"cus_a": 11},
        clock_version=7,
        expected_payload_hash=db.job["payload_hash"],
        authorization_evidence=evidence(db),
    )
    args.update(overrides)
    return await EventService().execute_event(**args)


def test_reminder_definition_has_authoritative_owner_grant_and_immutable_charge():
    async def check():
        db = BankMemory()
        event = await EventService().get_event(db, db.job["id"], "cus_a")
        assert event["customer_id"] == "cus_a" and event["source_id"] == "bill_video"
        assert event["instruction_id"] == "si_video" and event["source_plan_id"] == "pln_seed"
        assert event["payload"] == db.job["immutable_payload"]["payload"]
        with pytest.raises(BankError) as error:
            await EventService().get_event(db, db.job["id"], "cus_other")
        assert error.value.status == 404

    asyncio.run(check())


def test_reminder_execution_is_idempotent_and_writes_only_job_terminal_rows():
    async def check():
        db = BankMemory()
        before = deepcopy((db.tables["accounts"], db.tables["state_versions"]))
        first = await execute(db)
        assert first["status"] == "SUCCESS"
        replay = await execute(db)
        assert replay["replayed"] is True
        assert (db.tables["accounts"], db.tables["state_versions"]) == before
        assert db.writes == ["event_executions", "event_jobs"]
        assert len(db.tables["event_executions"]) == 1

    asyncio.run(check())


@pytest.mark.parametrize("fault", ["cancelled", "amount", "charge_due", "already_due", "expired"])
def test_stale_or_cancelled_reminder_is_blocked_without_business_state_write(fault):
    async def check():
        db = BankMemory()
        bill, mandate = db.tables["merchant_billing"][0], db.tables["standing_instructions"][0]
        if fault == "cancelled":
            mandate["status"] = "DELETED"
        elif fault == "amount":
            bill["amount_cents"] += 1
        elif fault == "charge_due":
            bill["next_due_at"] += timedelta(days=31)
        elif fault == "already_due":
            db.tables["clock"][0]["sim_now"] = bill["next_due_at"]
        else:
            mandate["end_at"] = NOW - timedelta(seconds=1)
        result = await execute(db)
        assert result["status"] == "BLOCKED"
        assert db.tables["state_versions"][0]["version"] == 11
        assert db.writes == ["event_executions", "event_jobs"]

    asyncio.run(check())


@pytest.mark.parametrize(
    "fault",
    [
        "not_due",
        "clock",
        "customer_version",
        "argument_hash",
        "stored_payload",
        "wrong_owner",
        "wrong_instruction",
        "wrong_billing",
    ],
)
def test_reminder_identity_due_hash_and_version_fences_fail_closed(fault):
    async def check():
        db = BankMemory()
        args = {}
        if fault == "not_due":
            db.tables["clock"][0]["sim_now"] = NOW - timedelta(microseconds=1)
        elif fault == "clock":
            args["clock_version"] = 6
        elif fault == "customer_version":
            args["expected_versions"] = {"cus_a": 10}
        elif fault == "argument_hash":
            args["expected_payload_hash"] = "wrong"
        elif fault == "stored_payload":
            db.job["immutable_payload"]["payload"]["amount_cents"] += 1
        elif fault == "wrong_owner":
            db.tables["standing_instructions"][0]["customer_id"] = "cus_other"
        elif fault == "wrong_instruction":
            db.job["immutable_payload"]["payload"]["instruction_id"] = "si_other"
        else:
            db.job["source_id"] = "bill_other"
        with pytest.raises(BankError):
            await execute(db, **args)
        assert db.writes == []

    asyncio.run(check())


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "DEMO_EVENT", "billing_id": "bill_video", "instruction_id": "si_video"},
        {"type": "SYSTEM_REMINDER", "billing_id": "bill_other", "instruction_id": "si_video"},
        {"type": "SYSTEM_REMINDER", "billing_id": "bill_video", "instruction_id": "si_other"},
    ],
)
def test_reminder_cannot_use_demo_or_another_billing_evidence(bad):
    async def check():
        db = BankMemory()
        with pytest.raises(BankError):
            await execute(db, authorization_evidence=bad)
        assert db.writes == []

    asyncio.run(check())


def test_l3_blocked_reminder_records_terminal_without_advancing_bill_or_version():
    async def check():
        db = BankMemory()
        before = deepcopy((db.tables["merchant_billing"], db.tables["state_versions"]))
        result = await EventService().block_event(
            db,
            db.job["id"],
            "BLOCKED",
            Code.MANDATE_NOT_ACTIVE.value,
            {"cus_a": 11},
            7,
            db.job["payload_hash"],
        )
        assert result["status"] == "BLOCKED"
        assert (db.tables["merchant_billing"], db.tables["state_versions"]) == before
        assert db.writes == ["event_executions", "event_jobs"]

    asyncio.run(check())


@pytest.mark.parametrize(
    "kind,after_status,has_effect,error,expected_noop",
    [
        ("FUND_CONFIRM", "SETTLED", True, False, False),
        ("FUND_EXPIRY", "SETTLED", False, False, True),
        ("FUND_SETTLE", "PENDING_SETTLEMENT", False, True, False),
    ],
)
def test_fund_result_reports_actual_order_state_and_never_hides_failure_as_noop(
    monkeypatch, kind, after_status, has_effect, error, expected_noop
):
    async def check():
        from app import events

        db = BankMemory()
        row = dict(db.job, kind=kind, source_id="ord_one")
        db.tables["fund_orders"] = [
            {
                "id": "ord_one",
                "customer_id": "cus_a",
                "plan_id": "pln_actual",
                "step_id": "step_actual",
                "account_id": "acc_a",
            }
        ]
        state = SimpleNamespace(fund_orders=(SimpleNamespace(id="ord_one", status=after_status),))
        effects = (object(),) if has_effect else ()
        errors = (SimpleNamespace(code=Code.INSUFFICIENT_FUNDS),) if error else ()
        persisted = []

        async def load(*args, **kwargs):
            return state

        async def persist(conn, actual):
            persisted.extend(actual)

        monkeypatch.setattr(events, "load_bank_state", load)
        monkeypatch.setattr(events, "persist", persist)
        applier = {
            "FUND_CONFIRM": "apply_fund_confirm",
            "FUND_EXPIRY": "apply_pending_expiry",
            "FUND_SETTLE": "apply_fund_settle",
        }[kind]
        monkeypatch.setattr(
            events.bank_funds, applier, lambda *args, **kwargs: (state, effects, errors)
        )
        result = await EventService()._execute_fund_event(
            db,
            row,
            row["id"],
            {"cus_a": 11},
            7,
            row["payload_hash"],
            {"type": "SYSTEM_ORDER", "order_id": "ord_one"},
        )
        assert result["result"]["order_status"] == after_status
        assert result["result"]["no_op"] is expected_noop
        assert result["status"] == ("FAILED_INSUFFICIENT" if error else "SUCCESS")
        assert persisted == list(effects)

    asyncio.run(check())


def test_fund_definition_plan_link_is_loaded_from_actual_order():
    async def check():
        db = BankMemory()
        db.job.update(kind="FUND_SETTLE", source_id="ord_one")
        db.tables["fund_orders"] = [
            {
                "id": "ord_one",
                "customer_id": "cus_a",
                "plan_id": "pln_actual",
                "step_id": "step_actual",
                "account_id": "acc_a",
            }
        ]
        event = await EventService().get_event(db, db.job["id"], "cus_a")
        assert event["source_plan_id"] == "pln_actual" and event["account_id"] == "acc_a"

    asyncio.run(check())
