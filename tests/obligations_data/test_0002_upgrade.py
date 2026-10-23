"""0002-with-real-data upgrade preservation gate (proposal §5 / task §A).

The historical 0002 migration refuses non-empty execution tables, so this test
seeds a fresh 0002 database through the real P1 ledger/persist path plus the
exact execution-row shapes P1's ``ExecutionService`` writes (raw SQL, source:
``services/bank-backend/app/execution.py``), captures a baseline of every old
table / sequence / privilege, upgrades to 0003 twice, and verifies the data is
byte-for-byte preserved before re-running prepare/query/reconcile/replay.
"""

import asyncio
import json
from datetime import date, datetime, time
from decimal import Decimal

from app.db import engine
from app.execution import ExecutionService
from app.state import persist
from safebank_bankcore.ledger import post_transfer
from safebank_bankcore.references import request_digest
from safebank_contracts.enums import Risk
from safebank_contracts.plan import (
    Execute,
    InteractiveEvidence,
    PlanBody,
    Prepare,
    Reconcile,
)
from safebank_contracts.state import LedgerTransfer
from sqlalchemy import text
from sqlalchemy.pool import NullPool

from tests.contracts.samples import step
from tests.obligations_data.conftest import _migrate
from tests.obligations_data.fixtures import KEY, NOW, install_core

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


async def _seed_0002(admin) -> None:
    steps = _steps()
    req = request_digest(steps)
    committed_evidence = json.dumps(_evidence("hash_committed").model_dump(mode="json"))
    empty = "{}"
    history = "[]"
    deadline = datetime.fromisoformat("2099-01-01T00:00:00Z")
    created = NOW
    result_json = (
        '{"plan_id":"pln_committed","status":"EXECUTED","steps":[],"versions":{},'
        '"committed_at":null,"error_code":null,"replayed":false}'
    )
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

        # Execution rows shaped exactly like P1 ExecutionService writes them.
        await conn.execute(
            text(
                "INSERT INTO execution_intents (customer_id, plan_id, attempt, plan_hash, "
                "steps_digest, request_digest, authorization_evidence, evidence_history, "
                "status, deadline_wall, deadline_cap_wall, created_at_wall) VALUES "
                "('cus_zs','pln_prepared',1,'hash_prepared','digest_prepared',:req,"
                "CAST(:evidence AS jsonb),CAST(:history AS jsonb),'PREPARED',"
                ":deadline,:deadline,:created)"
            ),
            {
                "req": req,
                "evidence": empty,
                "history": history,
                "deadline": deadline,
                "created": created,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO execution_intents (customer_id, plan_id, attempt, plan_hash, "
                "steps_digest, request_digest, authorization_evidence, evidence_history, "
                "status, deadline_wall, deadline_cap_wall, created_at_wall) VALUES "
                "('cus_zs','pln_committed',1,'hash_committed','digest_committed',:req,"
                "CAST(:evidence AS jsonb),CAST(:history AS jsonb),'COMMITTED',"
                ":deadline,:deadline,:created)"
            ),
            {
                "req": req,
                "evidence": committed_evidence,
                "history": history,
                "deadline": deadline,
                "created": created,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO plan_executions (customer_id, plan_id, plan_hash, steps_digest, "
                "request_digest, authorization_evidence, result_json, committed_at) VALUES "
                "('cus_zs','pln_committed','hash_committed','digest_committed',:req,"
                "CAST(:evidence AS jsonb),CAST(:result AS jsonb),:created)"
            ),
            {
                "req": req,
                "evidence": committed_evidence,
                "result": result_json,
                "created": created,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO execution_intents (customer_id, plan_id, attempt, plan_hash, "
                "steps_digest, request_digest, authorization_evidence, evidence_history, "
                "status, deadline_wall, deadline_cap_wall, created_at_wall) VALUES "
                "('cus_zs','pln_cancelled',1,'hash_cancelled','digest_cancelled',:req,"
                "CAST(:evidence AS jsonb),CAST(:history AS jsonb),'CANCELLED',"
                ":deadline,:deadline,:created)"
            ),
            {
                "req": req,
                "evidence": empty,
                "history": history,
                "deadline": deadline,
                "created": created,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO operation_events (event_id, customer_id, plan_id, step_id, "
                "action_kind, target_fingerprint, amount_cents, occurred_at, payload) VALUES "
                "('evt_committed','cus_zs','pln_committed','s1','transfer',NULL,100,:created,"
                "CAST(:payload AS jsonb))"
            ),
            {"created": created, "payload": empty},
        )
        await conn.execute(
            text(
                "INSERT INTO outbox (event_id, payload) VALUES "
                "('evt_committed', CAST(:payload AS jsonb))"
            ),
            {"payload": empty},
        )

        # The committed execution bumped scoped customer versions and the clock once.
        await conn.execute(
            text(
                "UPDATE state_versions SET version = version + 1 "
                "WHERE customer_id IN ('cus_zs','cus_ww')"
            )
        )
        await conn.execute(text("UPDATE clock SET version = version + 1"))


def test_0002_data_survives_0003_upgrade_and_execution_replays(obligations_0002_db):
    async def check():
        admin = engine(obligations_0002_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_0002_db.runtime_url, poolclass=NullPool)
        await _seed_0002(admin)

        async with admin.connect() as conn:
            baseline = await _capture(conn)

        _migrate(obligations_0002_db, "head")
        _migrate(obligations_0002_db, "head")

        async with admin.connect() as conn:
            after = await _capture(conn)
            assert (
                await conn.scalar(text("SELECT version_num FROM alembic_version"))
                == "0003_obligations"
            )
        assert after == baseline

        svc = ExecutionService(runtime, KEY)
        steps = _steps()
        deadline = "2099-01-01T00:00:00Z"
        prepared = await svc.prepare(
            Prepare(
                customer_id="cus_zs",
                plan_id="pln_new",
                attempt=1,
                plan_hash="hash_new",
                steps=steps,
                deadline_wall=deadline,
                deadline_cap_wall=deadline,
                authorization_evidence=_evidence("hash_new"),
            )
        )
        assert prepared["status"] == "PREPARED"

        queried = await svc.query("cus_zs", "pln_committed")
        assert queried["status"] == "COMMITTED"
        assert queried["result"] is not None

        reconciled = await svc.reconcile(
            "pln_prepared",
            Reconcile(customer_id="cus_zs", plan_hash="hash_prepared", cancel_if_uncommitted=True),
        )
        assert reconciled["status"] == "CANCELLED"

        replayed = await svc.execute(
            Execute(
                customer_id="cus_zs",
                plan_id="pln_committed",
                attempt=1,
                plan_hash="hash_committed",
                steps=steps,
                expected_versions={"cus_zs": 1, "cus_ww": 1},
                clock_version=2,
                authorization_evidence=_evidence("hash_committed"),
            )
        )
        assert replayed.replayed is True

        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())
