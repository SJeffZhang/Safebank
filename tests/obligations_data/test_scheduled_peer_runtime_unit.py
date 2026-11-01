"""Scheduled peer event scope/version barriers with real bankcore, no DB."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest
from app import events as event_module
from app.errors import BankError
from app.events import EventService
from safebank_contracts.codes import Code
from safebank_contracts.state import Payee
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.elements import BindParameter, BooleanClauseList
from sqlalchemy.sql.operators import and_, eq, in_op, is_, ne

from tests.bankcore.fixtures import NOW
from tests.bankcore.obligations_fixtures import raw_instruction, raw_projection_state


class Rows:
    def __init__(self, rows, key=None):
        self.rows, self.key = rows, key

    def mappings(self):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def one(self):
        assert len(self.rows) == 1
        return self.rows[0]

    one_or_none = first

    def all(self):
        return self.rows

    def scalar(self):
        return self.rows[0].get(self.key) if self.rows else None


def _value(expr, row):
    return expr.value if isinstance(expr, BindParameter) else row[expr.name]


def _matches(clause, row):
    if isinstance(clause, BooleanClauseList):
        assert clause.operator is and_
        return all(_matches(child, row) for child in clause.clauses)
    left, right = _value(clause.left, row), _value(clause.right, row)
    if clause.operator is in_op:
        return left in right
    if clause.operator is eq:
        return left == right
    if clause.operator is ne:
        return left != right
    if clause.operator is is_:
        return left is right
    raise AssertionError(f"unexpected SQL operator {clause.operator}")


class Memory:
    def __init__(self, *, disabled=False):
        payee = Payee(
            id="pye_peer",
            customer_id="cus_a",
            name="合成收款人",
            alias=None,
            phone=None,
            bank_name="本行",
            account_no="6222020000000003",
            target_account_id="acc_b",
            target_fingerprint="peer",
            created_at=NOW,
        )
        instruction = raw_instruction(
            payee_id=payee.id, amount_cents=100000, status="DISABLED" if disabled else "ACTIVE"
        )
        self.state = raw_projection_state(instructions=[instruction], payees=[payee])
        self.tables = {
            "event_jobs": [
                {
                    "id": "evt_peer",
                    "kind": "SCHEDULED_TRANSFER",
                    "source_id": instruction.id,
                    "due_at": instruction.next_run_at,
                    "period_key": instruction.next_run_at.isoformat(),
                    "payload_hash": "h",
                    "immutable_payload": {},
                    "status": "PENDING",
                }
            ],
            "event_executions": [],
            "customers": [{"id": "cus_a"}, {"id": "cus_b"}],
            "clock": [{"id": 1, "version": 1, "sim_now": instruction.next_run_at}],
            "state_versions": [
                {"customer_id": "cus_b", "version": 0},
                {"customer_id": "cus_a", "version": 0},
            ],
            "standing_instructions": [
                dict(instruction.model_dump(mode="python"), kind="SCHEDULED_TRANSFER")
            ],
            "payees": [payee.model_dump(mode="python")],
            "accounts": [
                account.model_dump(mode="python") for account in self.state.accounts.values()
            ],
            "instruction_runs": [],
        }
        self.locks = []
        self.writes = []
        self.scopes = []
        self.persisted = []
        self.on_version_lock = None

    async def execute(self, query):
        if getattr(query, "is_select", False):
            table = query.get_final_froms()[0].name
            rows = [
                deepcopy(row)
                for row in self.tables[table]
                if all(_matches(c, row) for c in query._where_criteria)
            ]
            if table == "state_versions" and query._order_by_clauses:
                rows.sort(key=lambda row: row["customer_id"])
            if query._for_update_arg is not None:
                self.locks.append((table, tuple(row.get("customer_id") for row in rows)))
                if table == "state_versions" and self.on_version_lock is not None:
                    self.on_version_lock(self)
            return Rows(rows, next(iter(query.selected_columns)).name)
        if getattr(query, "is_insert", False):
            table = query.table.name
            params = query.compile(dialect=postgresql.dialect()).params
            self.tables[table].append(params)
            self.writes.append(table)
            return SimpleNamespace(rowcount=1)
        if getattr(query, "is_update", False):
            table = query.table.name
            self.writes.append(table)
            rows = [
                row
                for row in self.tables[table]
                if all(_matches(c, row) for c in query._where_criteria)
            ]
            for row in rows:
                for column, value in query._values.items():
                    row[column if isinstance(column, str) else column.name] = value.value
            return SimpleNamespace(rowcount=len(rows))
        return Rows([])

    async def scalar(self, query):
        return (await self.execute(query)).scalar()

    def versions(self):
        return {row["customer_id"]: row["version"] for row in self.tables["state_versions"]}


@pytest.fixture
def memory_runtime(monkeypatch):
    async def load(db, scope, now, *, customer_id):
        assert customer_id == "cus_a"
        db.scopes.append(scope.customer_ids)
        accounts = {
            key: value
            for key, value in db.state.accounts.items()
            if value.customer_id is None or value.customer_id in scope.customer_ids
        }
        return db.state.model_copy(update={"accounts": accounts})

    async def persist(db, effects):
        db.persisted.extend(effects)

    monkeypatch.setattr(event_module, "load_bank_state", load)
    monkeypatch.setattr(event_module, "persist", persist)


async def execute(db, expected=None, clock=1):
    return await EventService(wall_clock=lambda: NOW).execute_event(
        db,
        "evt_peer",
        expected_versions={"cus_a": 0, "cus_b": 0} if expected is None else expected,
        clock_version=clock,
        expected_payload_hash="h",
        authorization_evidence={"by": "test"},
    )


def test_other_event_loads_peer_and_bumps_both_only_after_a_real_transfer(memory_runtime):
    db = Memory()
    result = asyncio.run(execute(db))
    assert result["status"] == "SUCCESS", result
    assert db.scopes == [("cus_a", "cus_b")]
    assert db.versions() == {"cus_a": 1, "cus_b": 1}
    assert [entry[0] for entry in db.locks[:2]] == ["event_jobs", "clock"]
    assert next(entry[1] for entry in db.locks if entry[0] == "state_versions") == (
        "cus_a",
        "cus_b",
    )
    first_writes = deepcopy(db.writes)
    replay = asyncio.run(execute(db))
    assert (
        replay["replayed"] is True
        and db.versions() == {"cus_a": 1, "cus_b": 1}
        and db.writes == first_writes
    )


@pytest.mark.parametrize("expected", [{"cus_a": 0}, {"cus_a": 0, "cus_b": 9}])
def test_missing_or_stale_peer_fails_before_cursor_or_ledger_persistence(memory_runtime, expected):
    db = Memory()
    with pytest.raises(BankError) as caught:
        asyncio.run(execute(db, expected))
    assert caught.value.code is Code.STATE_CHANGED
    assert db.writes == [] and db.persisted == [] and db.scopes == []
    assert db.versions() == {"cus_a": 0, "cus_b": 0}


def test_blocked_source_instruction_does_not_bump_peer(memory_runtime):
    db = Memory(disabled=True)
    result = asyncio.run(execute(db))
    assert result["status"] == "BLOCKED"
    assert db.versions() == {"cus_a": 1, "cus_b": 0}


def test_peer_target_identity_is_rechecked_after_version_locks(memory_runtime):
    db = Memory()
    db.on_version_lock = lambda value: value.tables["payees"][0].update(target_account_id=None)
    with pytest.raises(BankError) as caught:
        asyncio.run(execute(db))
    assert caught.value.code is Code.STATE_CHANGED and db.writes == [] and db.scopes == []


@pytest.mark.parametrize("expected", [{"cus_b": 0}, {"cus_a": 7, "cus_b": 0}])
def test_source_version_is_also_required_and_checked_under_the_dual_fence(memory_runtime, expected):
    db = Memory()
    with pytest.raises(BankError) as caught:
        asyncio.run(execute(db, expected))
    assert caught.value.code is Code.STATE_CHANGED and db.writes == [] and db.scopes == []


def test_stale_clock_rejects_before_loading_or_persisting_either_customer(memory_runtime):
    db = Memory()
    with pytest.raises(BankError) as caught:
        asyncio.run(execute(db, clock=9))
    assert caught.value.code is Code.STATE_CHANGED and db.writes == [] and db.scopes == []


def test_insufficient_source_funds_do_not_bump_peer(memory_runtime):
    db = Memory()
    accounts = dict(db.state.accounts)
    accounts["acc_a"] = accounts["acc_a"].model_copy(update={"credits_posted_cents": 0})
    db.state = db.state.model_copy(update={"accounts": accounts})
    result = asyncio.run(execute(db))
    assert result["status"] == "FAILED_INSUFFICIENT"
    assert db.versions() == {"cus_a": 1, "cus_b": 0}


def test_callback_expected_version_keys_cannot_expand_the_actual_loaded_scope(memory_runtime):
    db = Memory()
    result = asyncio.run(execute(db, {"cus_a": 0, "cus_b": 0, "cus_unrelated": 123}))
    assert result["status"] == "SUCCESS"
    assert db.scopes == [("cus_a", "cus_b")]
    assert db.versions() == {"cus_a": 1, "cus_b": 1}


def test_another_customer_payee_cannot_supply_a_peer_scope(memory_runtime):
    db = Memory()
    db.tables["payees"][0]["customer_id"] = "cus_unrelated"
    with pytest.raises(BankError) as caught:
        asyncio.run(execute(db))
    assert caught.value.code is Code.NOT_FOUND and db.writes == [] and db.scopes == []
