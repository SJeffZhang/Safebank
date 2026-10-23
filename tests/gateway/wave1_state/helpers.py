from datetime import UTC, datetime, timedelta
from uuid import uuid4

from safebank_contracts.enums import Risk
from safebank_contracts.plan import InteractiveEvidence

from services.gateway.app import state
from services.gateway.app import tables as t


async def seed(conn, *, job_status="EXECUTING", plan_status="EXECUTING"):
    suffix = uuid4().hex
    plan_id, sid, scope_id, eval_id = (f"{p}_{suffix}" for p in ("pln", "ses", "utt", "evl"))
    now = datetime.now(UTC)
    cap = now + timedelta(minutes=10)
    evidence = InteractiveEvidence(
        type="INTERACTIVE",
        origin="agent",
        scope_ids=(scope_id,),
        utterance_ids=(scope_id,),
        evaluation_id=eval_id,
        approved_evaluation_id=None,
        plan_hash="hash",
        approval_id=None,
        confirmed_view_hash=None,
        final_view_hash="view",
        risk=Risk.GREEN,
        confirmed_at_wall=None,
        authorization_epoch=0,
    )
    await conn.execute(
        t.sessions.insert().values(id=sid, customer_id="cus_state", created_at_wall=now)
    )
    await conn.execute(
        t.utterances.insert().values(
            id=scope_id,
            sid=sid,
            kind="typed",
            text="freeze",
            normalized_scope={},
            status="ACTIVE",
            created_at_wall=now,
            expires_at_wall=cap,
            authorization_epoch=0,
        )
    )
    await conn.execute(
        t.plans.insert().values(
            id=plan_id,
            customer_id="cus_state",
            sid=sid,
            origin="agent",
            request_id=plan_id,
            request_hash="hash",
            plan_hash="hash",
            steps_hash="hash",
            resolved_steps=[],
            utterance_ids=[scope_id],
            scope_ids=[scope_id],
            status=plan_status,
            created_at_wall=now,
        )
    )
    await conn.execute(
        t.evaluations.insert().values(
            id=eval_id,
            plan_id=plan_id,
            revision=1,
            decision={"decision": "ALLOW"},
            risk="GREEN",
            policy_version="v1",
            view_hash="view",
            view_json={},
            created_at_wall=now,
        )
    )
    await conn.execute(
        t.execution_jobs.insert().values(
            plan_id=plan_id,
            status=job_status,
            l3_attempt=1,
            created_at_wall=now,
            updated_at_wall=now,
            deadline_wall=cap,
            deadline_cap_wall=cap,
            authorization_evidence=evidence.model_dump(mode="json"),
        )
    )
    await conn.execute(
        t.authorization_claims.insert().values(scope_id=scope_id, plan_id=plan_id, status="CLAIMED")
    )
    expected = state.ExecutionIdentity(
        customer_id="cus_state", plan_id=plan_id, origin="agent", attempt=1, revision=1
    )
    return expected, evidence, now, cap
