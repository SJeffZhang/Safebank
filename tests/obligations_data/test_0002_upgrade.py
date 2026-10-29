"""0002-with-real-data upgrade preservation gate (proposal §5 / task §A).

The historical 0002 migration refuses non-empty execution tables, so this test
seeds a fresh 0002 database through the *real* P1 execution path: posted/pending
ledger via the shared pure ledger + persist, then the unmodified
``ExecutionService`` (``prepare(attempt1)`` → ``prepare(attempt2)`` → ``execute``
for a committed plan, plus ``prepare``/``reconcile`` for prepared/cancelled
plans). Because S3a's production loader reads the 0003 obligation tables, the test
patches ``app.execution.load_bank_state`` to the frozen pre-S3a loader
(``tests/obligations_data/legacy_loader.py``, sourced from
``cca074b:services/bank-backend/app/state.py``) only while seeding 0002 state.
It then captures a baseline of every old table / sequence / privilege, upgrades to
0003 twice, verifies byte-for-byte preservation, and re-runs
query/replay/reconcile against the original real tasks.
"""

import asyncio
import json
from datetime import date, datetime, time
from decimal import Decimal

import app.execution as execution
from app.db import engine
from app.execution import ExecutionService
from app.state import persist
from safebank_bankcore.ledger import post_transfer
from safebank_contracts.enums import Risk
from safebank_contracts.plan import Execute, InteractiveEvidence, PlanBody, Prepare, Reconcile
from safebank_contracts.state import LedgerTransfer
from sqlalchemy import text
from sqlalchemy.pool import NullPool

from tests.contracts.samples import step
from tests.obligations_data.conftest import _migrate
from tests.obligations_data.fixtures import KEY, NOW, install_core
from tests.obligations_data.legacy_loader import load_bank_state as legacy_load_bank_state

OLD_TABLES = [
    "customers",
    "accounts",
    "cards",
    "payees",
    "transfers",
    "transactions",
    "clock",
    "state_versions",
    "execution_intents",
    "plan_executions",
    "operation_events",
    "outbox",
]


def _steps():
    return PlanBody(
        intents=(),
        steps=[step(from_account_id="acc_zs_chk", payee_id="pye_zs_ww", to_account_id=None)],
        rationale="test",
    ).steps


def _evidence(plan_hash: str) -> InteractiveEvidence:
    return InteractiveEvidence(
        type="INTERACTIVE",
        origin="agent",
        scope_ids=("utt_1",),
        utterance_ids=("utt_1",),
        evaluation_id="evl_1",
        approved_evaluation_id=None,
        plan_hash=plan_hash,
        approval_id=None,
        confirmed_view_hash=None,
        final_view_hash=plan_hash,
        risk=Risk.GREEN,
        confirmed_at_wall=None,
        authorization_epoch=0,
    )


def _json_default(value):
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"not JSON serializable: {type(value)}")


async def _capture(conn):
    result = {}
    for table in OLD_TABLES:
        rows = (await conn.execute(text(f"SELECT * FROM {table}"))).mappings().all()
        result[table] = sorted(
            json.dumps(dict(r), sort_keys=True, default=_json_default) for r in rows
        )
    for sequence in ("transaction_sequence", "operation_sequence"):
        row = (
            (await conn.execute(text(f"SELECT last_value, is_called FROM {sequence}")))
            .mappings()
            .one()
        )
        result[sequence] = dict(row)
    for table in OLD_TABLES:
        for privilege in ("SELECT", "INSERT", "UPDATE"):
            result[f"app_{table}_{privilege}"] = await conn.scalar(
                text(f"SELECT has_table_privilege('bank_app','{table}','{privilege}')")
            )
        result[f"ro_{table}_SELECT"] = await conn.scalar(
            text(f"SELECT has_table_privilege('bank_ro','{table}','SELECT')")
        )
    return result


async def _seed_0002(admin, runtime) -> None:
    async with admin.begin() as conn:
        state = await install_core(conn)

        # Real posted + pending ledger data via the same pure ledger/persist path.
        pending = LedgerTransfer(
            id="trf_pending",
            debit_account_id="acc_zs_chk",
            credit_account_id="acc_ww_chk",
            amount_cents=100,
            code="P2P_INTERNAL",
            phase="PENDING",
            pending_id=None,
            plan_id="pln_pending",
            step_id="s1",
            occurred_at=NOW,
            expires_at="2026-10-16T09:00:00+08:00",
        )
        state, effects, errors = post_transfer(state, pending, NOW)
        assert errors == ()
        await persist(conn, effects)
        posted = LedgerTransfer(
            id="trf_posted",
            debit_account_id="acc_zs_chk",
            credit_account_id="acc_ww_chk",
            amount_cents=40,
            code="P2P_INTERNAL",
            phase="POST_PENDING",
            pending_id="trf_pending",
            plan_id="pln_pending",
            step_id="s1",
            occurred_at=NOW,
            expires_at=None,
        )
        state, effects, errors = post_transfer(state, posted, NOW)
        assert errors == ()
        await persist(conn, effects)

    svc = ExecutionService(runtime, KEY)
    steps = _steps()
    deadline = "2099-01-01T00:00:00Z"

    # Only the loader is a test-history stand-in; prepare/execute/query/reconcile
    # and resolve_scope/persist are the unmodified production code.
    original = execution.load_bank_state
    execution.load_bank_state = legacy_load_bank_state
    try:
        committed_evidence = _evidence("hash_committed")
        await svc.prepare(
            Prepare(
                customer_id="cus_zs",
                plan_id="pln_committed",
                attempt=1,
                plan_hash="hash_committed",
                steps=steps,
                deadline_wall=deadline,
                deadline_cap_wall=deadline,
                authorization_evidence=committed_evidence,
            )
        )
        await svc.prepare(
            Prepare(
                customer_id="cus_zs",
                plan_id="pln_committed",
                attempt=2,
                plan_hash="hash_committed",
                steps=steps,
                deadline_wall=deadline,
                deadline_cap_wall=deadline,
                authorization_evidence=committed_evidence,
            )
        )
        committed = await svc.execute(
            Execute(
                customer_id="cus_zs",
                plan_id="pln_committed",
                attempt=2,
                plan_hash="hash_committed",
                steps=steps,
                expected_versions={"cus_zs": 0, "cus_ww": 0},
                clock_version=1,
                authorization_evidence=committed_evidence,
            )
        )
        assert committed.status == "EXECUTED"
        assert committed.versions == {"cus_zs": 1, "cus_ww": 1}

        await svc.prepare(
            Prepare(
                customer_id="cus_zs",
                plan_id="pln_prepared",
                attempt=1,
                plan_hash="hash_prepared",
                steps=steps,
                deadline_wall=deadline,
                deadline_cap_wall=deadline,
                authorization_evidence=_evidence("hash_prepared"),
            )
        )

        await svc.prepare(
            Prepare(
                customer_id="cus_zs",
                plan_id="pln_cancelled",
                attempt=1,
                plan_hash="hash_cancelled",
                steps=steps,
                deadline_wall=deadline,
                deadline_cap_wall=deadline,
                authorization_evidence=_evidence("hash_cancelled"),
            )
        )
        await svc.reconcile(
            "pln_cancelled",
            Reconcile(customer_id="cus_zs", plan_hash="hash_cancelled", cancel_if_uncommitted=True),
        )
    finally:
        execution.load_bank_state = original


def test_0002_data_survives_0003_upgrade_and_execution_replays(obligations_0002_db):
    async def check():
        admin = engine(obligations_0002_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_0002_db.runtime_url, poolclass=NullPool)
        await _seed_0002(admin, runtime)

        async with admin.connect() as conn:
            baseline = await _capture(conn)

        _migrate(obligations_0002_db, "head")
        _migrate(obligations_0002_db, "head")

        async with admin.connect() as conn:
            after = await _capture(conn)
            assert (
                await conn.scalar(text("SELECT version_num FROM alembic_version")) == "0009_funds_units_nonneg"
            )
        assert after == baseline

        async with admin.connect() as conn:
            history = await conn.scalar(
                text(
                    "SELECT evidence_history FROM execution_intents "
                    "WHERE customer_id='cus_zs' AND plan_id='pln_committed'"
                )
            )
            result_json = await conn.scalar(
                text(
                    "SELECT result_json FROM plan_executions "
                    "WHERE customer_id='cus_zs' AND plan_id='pln_committed'"
                )
            )
        # Real re-approval (attempt+1) must archive a non-empty evidence_history, and the
        # committed result must carry the real bumped versions (not hand-written placeholders).
        assert history
        assert result_json["versions"] == {"cus_zs": 1, "cus_ww": 1}

        svc = ExecutionService(runtime, KEY)
        steps = _steps()

        queried = await svc.query("cus_zs", "pln_committed")
        assert queried["status"] == "COMMITTED"
        assert queried["result"] is not None

        replayed = await svc.execute(
            Execute(
                customer_id="cus_zs",
                plan_id="pln_committed",
                attempt=2,
                plan_hash="hash_committed",
                steps=steps,
                expected_versions={"cus_zs": 1, "cus_ww": 1},
                clock_version=2,
                authorization_evidence=_evidence("hash_committed"),
            )
        )
        assert replayed.replayed is True

        reconciled = await svc.reconcile(
            "pln_prepared",
            Reconcile(customer_id="cus_zs", plan_hash="hash_prepared", cancel_if_uncommitted=True),
        )
        assert reconciled["status"] == "CANCELLED"

        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())
