"""Real isolated PostgreSQL migration checks; never uses business databases."""

import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from services.gateway.app import tables as t
from services.gateway.app.db import GATEWAY_REVISION, engine, ready
from services.gateway.app.schemas.enums import ExecutionJobStatus
from tests.gateway.db.conftest import ROOT, _provision

NEW_COLUMNS = {
    "evaluations": {"view_json": "JSONB"},
    "otp_challenges": {"code_ciphertext": "TEXT"},
    "execution_jobs": {"authorization_evidence": "JSONB", "deadline_cap_wall": "TIMESTAMP"},
    "plans": {"plan_draft": "JSONB", "authorization_epoch": "BIGINT"},
}


def test_wave1_metadata_requires_incremental_schema():
    assert GATEWAY_REVISION == "0005_form_epoch"
    assert {"FENCING", "PAUSED"} <= {v.value for v in ExecutionJobStatus}
    for table, columns in NEW_COLUMNS.items():
        for name in columns:
            assert name in t.metadata.tables[table].c
            assert t.metadata.tables[table].c[name].nullable


def test_wave1_column_types_status_and_runtime_permissions(gateway_db):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        async with db.connect() as conn:
            assert await ready(db)
            for table, columns in NEW_COLUMNS.items():
                actual = await conn.run_sync(lambda c, table=table: inspect(c).get_columns(table))
                actual = {c["name"]: c for c in actual}
                for name, type_ in columns.items():
                    assert actual[name]["nullable"] is True
                    assert str(actual[name]["type"]) == type_
                    if type_ == "TIMESTAMP":
                        assert actual[name]["type"].timezone is True
            owners = (
                (
                    await conn.execute(
                        text("SELECT DISTINCT tableowner FROM pg_tables WHERE schemaname='public'")
                    )
                )
                .scalars()
                .all()
            )
            assert owners == ["postgres"]
        async with db.begin() as conn:
            now = datetime.now(UTC)
            await conn.execute(
                t.sessions.insert().values(
                    id="w_schema_s", customer_id="cus_s", created_at_wall=now
                )
            )
            await conn.execute(
                t.plans.insert().values(
                    id="w_schema_p",
                    customer_id="cus_s",
                    sid="w_schema_s",
                    origin="agent",
                    request_id="r",
                    request_hash="h",
                    plan_hash="h",
                    steps_hash="h",
                    resolved_steps=[],
                    status="EXECUTING",
                    created_at_wall=now,
                )
            )
            await conn.execute(
                t.execution_jobs.insert().values(
                    plan_id="w_schema_p",
                    status="EXECUTING",
                    l3_attempt=1,
                    created_at_wall=now,
                    updated_at_wall=now,
                )
            )
        for status in ExecutionJobStatus:
            async with db.begin() as conn:
                await conn.execute(
                    t.execution_jobs.update()
                    .where(t.execution_jobs.c.plan_id == "w_schema_p")
                    .values(status=status.value)
                )
        for sql in [
            "UPDATE execution_jobs SET status='BAD' WHERE plan_id='w_schema_p'",
            "ALTER TABLE execution_jobs ADD COLUMN unauthorized text",
            "TRUNCATE audit_events",
            "UPDATE audit_events SET hash='tampered'",
            "DELETE FROM audit_events",
        ]:
            async with db.begin() as conn:
                with pytest.raises(DBAPIError):
                    await conn.execute(text(sql))
        await db.dispose()

    asyncio.run(run())


def test_upgrade_0001_preserves_data_and_readiness():
    if os.getenv("GATEWAY_DB_TESTS") != "1":
        pytest.skip("requires isolated PostgreSQL")
    old = _provision("0001_gateway")

    async def run():
        db = engine(old.admin_url, poolclass=NullPool)
        assert not await ready(db)
        async with db.begin() as conn:
            statements = [
                "INSERT INTO sessions(id,customer_id,created_at_wall) "
                "VALUES('old_s','old_c',now())",
                "INSERT INTO "
                "plans(id,customer_id,sid,origin,request_id,request_hash,plan_hash,"
                "steps_hash,resolved_steps,status,created_at_wall) "
                "VALUES('old_p','old_c','old_s','agent','old_r','h','h','h','[]',"
                "'EXECUTING',now())",
                "INSERT INTO "
                "evaluations(id,plan_id,revision,decision,risk,policy_version,created_at_wall) "
                "VALUES('old_e','old_p',1,'{}','YELLOW','v1',now())",
                "INSERT INTO "
                "approvals(id,plan_id,evaluation_id,customer_id,sid,plan_hash,"
                "view_hash,binding_mac,expires_at_wall,status) "
                "VALUES('old_a','old_p','old_e','old_c','old_s','h','v','mac',now(),'PENDING')",
                "INSERT INTO "
                "otp_challenges(approval_id,code_digest,view_hash,expires_at_wall) "
                "VALUES('old_a','digest','v',now())",
                "INSERT INTO "
                "execution_jobs(plan_id,status,l3_attempt,created_at_wall,updated_at_wall) "
                "VALUES('old_p','EXECUTING',1,now(),now())",
                "INSERT INTO authorization_claims(scope_id,plan_id,status) "
                "VALUES('old_u','old_p','CLAIMED')",
                "INSERT INTO "
                "audit_events(id,customer_id,event_type,payload_digest,payload,"
                "prev_hash,hash,created_at_wall) "
                "VALUES('old_audit','old_c','old','d','{}','p','h',now())",
            ]
            for sql in statements:
                await conn.execute(text(sql))
            names = [
                "sessions",
                "plans",
                "evaluations",
                "approvals",
                "otp_challenges",
                "execution_jobs",
                "authorization_claims",
                "audit_events",
            ]
            before = {
                name: (
                    await conn.execute(text(f"SELECT row_to_json(x) FROM {name} x"))
                ).scalar_one()
                for name in names
            }
        env = {
            **os.environ,
            "GATEWAY_MIGRATION_DATABASE_URL": old.admin_url,
            "PYTHONPATH": os.pathsep.join(
                str(ROOT / p) for p in (".", "packages/contracts/src", "packages/bankcore/src")
            ),
        }
        for _ in range(2):
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "alembic",
                    "-c",
                    "services/gateway/alembic.ini",
                    "upgrade",
                    "head",
                ],
                cwd=ROOT,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
        assert await ready(db)
        async with db.connect() as conn:
            for name in names:
                after = (
                    await conn.execute(text(f"SELECT row_to_json(x) FROM {name} x"))
                ).scalar_one()
                for column in NEW_COLUMNS.get(name, {}):
                    assert after.pop(column) is None
                assert after == before[name]
        await db.dispose()

    try:
        asyncio.run(run())
    finally:
        subprocess.run(["docker", "rm", "-f", old.name], check=True, capture_output=True)
