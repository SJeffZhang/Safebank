"""Bound hint state for revocation gates; not a financial/G4 pipeline acceptance.

Only test-owned rows are seeded. Existing roots, jobs, claims and UI rows are
never updated or revived. The real publisher computes the private commitment.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from safebank_bankcore.ids import digest
from safebank_bankcore.references import request_digest
from safebank_contracts.plan import PlanDraft
from sqlalchemy import select

from services.gateway.app import tables as t
from services.gateway.app.api.plans import _persist_plan
from services.gateway.app.evaluation.repo import append_evaluation
from services.gateway.app.hint_sources import publish_replan_hints, validate_hint_source
from services.gateway.app.pipeline.context import EvalContext
from services.gateway.app.schemas.decision import Decision
from services.gateway.app.schemas.enums import ServiceKind, UtteranceStatus
from services.gateway.app.schemas.identity import Caller
from services.gateway.app.schemas.scope import AuthorizationInputs
from services.gateway.app.utterance.loader import as_ref
from services.gateway.app.utterance.parser import parse
from services.gateway.app.utterance.service import new_id
from tests.gateway.w1.fixtures import bank_state


@dataclass(frozen=True)
class BoundHint:
    id: str
    root_id: str
    plan_id: str
    expires_at_wall: datetime


async def bound_transfer_hint(
    db, sid: str, now: datetime, *, root_id: str | None = None
) -> BoundHint:
    """Publish a 300-yuan shrink from one current 800-yuan test transfer root.

    With no root_id, add an independent SCOPED root to exercise the barrier
    even when the old CLARIFYING/SUPERSEDED root cannot authorize a new hint.
    This synthetic state does not claim G3 would grant another root over a job.
    """
    async with db.begin() as conn:
        session = (
            (await conn.execute(select(t.sessions).where(t.sessions.c.id == sid).with_for_update()))
            .mappings()
            .one()
        )
        if root_id is None:
            root_id = new_id("utt")
            parsed = parse("给王五800", bank_state(), session["customer_id"])
            assert parsed.status.value == "SCOPED"
            await conn.execute(
                t.utterances.insert().values(
                    id=root_id,
                    sid=sid,
                    kind="typed",
                    text="给王五800",
                    normalized_scope=parsed.scope.model_dump(mode="json"),
                    echoes_untrusted=False,
                    source_id=None,
                    parent_ids=[],
                    status="ACTIVE",
                    parse_status="SCOPED",
                    clarification_id=None,
                    authorization_epoch=session["authorization_epoch"],
                    created_at_wall=now,
                    expires_at_wall=now + timedelta(minutes=10),
                )
            )
        root = (
            (await conn.execute(select(t.utterances).where(t.utterances.c.id == root_id)))
            .mappings()
            .one()
        )
        ref = as_ref(root)
        assert len(ref.scope.transfers) == 1 and ref.scope.transfers[0].amount_cents == 80000
        candidate = ref.scope.model_copy(
            update={
                "transfers": (ref.scope.transfers[0].model_copy(update={"amount_cents": 30000}),)
            }
        )
        request_id, plan_id, evaluation_id = new_id("req"), new_id("pln"), new_id("evl")
        draft = PlanDraft.model_validate(
            {
                "request_id": request_id,
                "session_id": sid,
                "utterance_ids": [root_id],
                "intents": [],
                "rationale": "Synthetic saved REPLAN state for revocation boundary only",
                "steps": [
                    {
                        "step_id": "s1",
                        "tool": "transfer",
                        "depends_on": [],
                        "arg_sources": [],
                        "args": {
                            "from_account_id": "acc_zs",
                            "payee_id": "pye_ww",
                            "to_account_id": None,
                            "amount_cents": 80000,
                            "memo": None,
                        },
                    }
                ],
            }
        )
        decision = Decision.model_validate(
            {
                "plan_id": plan_id,
                "evaluation_id": evaluation_id,
                "revision": 1,
                "decision": "REPLAN",
                "risk": "RED",
                "policy_version": "v1",
                "findings": [
                    {
                        "code": "INSUFFICIENT_FUNDS",
                        "effect": "REPLAN",
                        "step_id": "s1",
                        "resolver": "user",
                    }
                ],
                "preflight": {
                    "executable": False,
                    "steps": [{"step_id": "s1", "status": "FAILED"}],
                },
                "replan_hints": [
                    {
                        "id": new_id("hnt"),
                        "label": "publisher replaces this label",
                        "scope": candidate.model_dump(mode="json"),
                        "expires_at_wall": (now + timedelta(minutes=5)).isoformat(),
                    }
                ],
            }
        )
        steps_hash = request_digest(draft.steps)
        await _persist_plan(
            conn,
            plan_id=plan_id,
            customer_id=session["customer_id"],
            sid=sid,
            request_id=request_id,
            request_hash=digest({"request_id": request_id, "steps_hash": steps_hash}),
            steps_hash=steps_hash,
            resolved_steps=[
                {
                    "step_id": "s1",
                    "tool": "transfer",
                    "args": draft.steps[0].args.model_dump(mode="json"),
                }
            ],
            utterance_ids=[root_id],
            scope_ids=[root_id],
            status="REPLAN",
            now=now,
            plan_draft=draft.model_dump(mode="json"),
        )
        await append_evaluation(
            conn,
            plan_id=plan_id,
            evaluation_id=evaluation_id,
            revision=1,
            decision={"decision": "REPLAN"},
            risk="RED",
            findings=[item.model_dump(mode="json") for item in decision.findings],
            preflight=decision.preflight.model_dump(mode="json"),
            versions={session["customer_id"]: 1},
            policy_version="v1",
            clock_version=1,
            created_at_wall=now,
        )
        auth = AuthorizationInputs(
            sid=sid,
            customer_id=session["customer_id"],
            utterance_ids=(root_id,),
            scope_ids=(root_id,),
            authorization_epoch=session["authorization_epoch"],
            utterances={root_id: ref},
            statuses={root_id: UtteranceStatus.ACTIVE},
        )
        context = EvalContext(
            conn=conn,
            session_id=sid,
            customer_id=session["customer_id"],
            plan_id=plan_id,
            origin="agent",
            plan_draft=draft,
            authorization=auth,
            decision=decision,
            identity=Caller(
                kind=ServiceKind.A,
                customer_id=session["customer_id"],
                sid=sid,
                utts=(root_id,),
                scopes=frozenset({"assistant"}),
            ),
        )
        hints = await publish_replan_hints(
            conn, context, session, evaluation_id=evaluation_id, revision=1, now=now
        )
        assert len(hints) == 1
        row = (
            (await conn.execute(select(t.hints).where(t.hints.c.id == hints[0].id)))
            .mappings()
            .one()
        )
        await validate_hint_source(conn, session, row, now)
        assert row["used_by"] is None and len(row["view_hash"]) == 64
        return BoundHint(row["id"], root_id, plan_id, row["expires_at_wall"])
