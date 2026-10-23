"""Schema-valid legacy fixtures that never create an evaluation."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select

from services.gateway.app import tables as t


async def orphan(conn, *, job_status="EXECUTING", plan_status="EXECUTING", legacy_null=True):
    suffix = uuid4().hex
    ids = {name: f"{name}_{suffix}" for name in ("pln", "ses", "utt")}
    now = datetime.now(UTC)
    await conn.execute(
        t.sessions.insert().values(
            id=ids["ses"],
            customer_id="cus_orphan",
            created_at_wall=now,
        )
    )
    await conn.execute(
        t.utterances.insert().values(
            id=ids["utt"],
            sid=ids["ses"],
            kind="typed",
            text="cancel",
            normalized_scope={},
            status="ACTIVE",
            authorization_epoch=0,
            created_at_wall=now,
            expires_at_wall=now + timedelta(minutes=10),
        )
    )
    await conn.execute(
        t.plans.insert().values(
            id=ids["pln"],
            customer_id="cus_orphan",
            sid=ids["ses"],
            origin="agent",
            request_id=ids["pln"],
            request_hash="hash",
            plan_hash="hash",
            steps_hash="hash",
            resolved_steps=[],
            utterance_ids=[ids["utt"]],
            scope_ids=[ids["utt"]],
            status=plan_status,
            created_at_wall=now,
        )
    )
    await conn.execute(
        t.execution_jobs.insert().values(
            plan_id=ids["pln"],
            status=job_status,
            l3_attempt=3,
            retry_count=2,
            last_error={"old": "failure"},
            result=None,
            created_at_wall=now,
            updated_at_wall=now,
            deadline_wall=None if legacy_null else now + timedelta(minutes=2),
            deadline_cap_wall=None if legacy_null else now + timedelta(minutes=10),
            authorization_evidence=None if legacy_null else {"legacy": "opaque"},
        )
    )
    await conn.execute(
        t.authorization_claims.insert().values(
            scope_id=ids["utt"],
            plan_id=ids["pln"],
            status="CLAIMED",
        )
    )
    return ids, now


def identity(ids):
    from services.gateway.app.state import OrphanExecutionIdentity

    return OrphanExecutionIdentity("cus_orphan", ids["pln"], "agent", 3)


async def snapshot(conn, ids):
    plan = (await conn.execute(select(t.plans).where(t.plans.c.id == ids["pln"]))).mappings().one()
    job = (
        (
            await conn.execute(
                select(t.execution_jobs).where(t.execution_jobs.c.plan_id == ids["pln"])
            )
        )
        .mappings()
        .one()
    )
    claims = (
        (
            await conn.execute(
                select(t.authorization_claims)
                .where(t.authorization_claims.c.plan_id == ids["pln"])
                .order_by(t.authorization_claims.c.scope_id)
            )
        )
        .mappings()
        .all()
    )
    utterance = (
        (await conn.execute(select(t.utterances).where(t.utterances.c.id == ids["utt"])))
        .mappings()
        .one()
    )
    return {
        "plan": dict(plan),
        "job": dict(job),
        "claims": [dict(c) for c in claims],
        "utterance": dict(utterance),
    }
