"""G4 hint publication and bound G22a views; no database or HTTP listener."""

import asyncio
import json
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from safebank_contracts.plan import PlanDraft

from services.gateway.app.api import plans, sessions
from services.gateway.app.pipeline.context import ResolvedStep
from services.gateway.app.schemas.decision import Decision
from services.gateway.app.schemas.enums import ServiceKind
from services.gateway.app.schemas.identity import Caller
from services.gateway.app.schemas.scope import NormalizedScope
from tests.agent.test_nov1_replan_receipt import replan_state
from tests.gateway.w3a.conftest import w3_state

NOW = datetime(2026, 11, 1, 9, tzinfo=UTC)


class Rows:
    rowcount = 1

    def __init__(self, values=()):
        self.values = list(values)

    def mappings(self):
        return self

    def all(self):
        return self.values

    def one_or_none(self):
        assert len(self.values) <= 1
        return self.values[0] if self.values else None

    def one(self):
        assert len(self.values) == 1
        return self.values[0]


class MemoryBoundary:
    """Replace SQL execution only; production G4 persistence code still runs."""

    def __init__(self):
        fixture = replan_state()
        self.draft = PlanDraft.model_validate(fixture["plan_draft"])
        self.scope = {
            **fixture["decision"]["replan_hints"][0]["scope"],
            "transfers": [{"target_fingerprint": "fp_wang", "amount_cents": 5000000}],
        }
        self.scope = NormalizedScope.model_validate(self.scope).model_dump(mode="json")
        self.tables = {
            "sessions": [
                {
                    "id": "ses_current",
                    "customer_id": "cus_a",
                    "authorization_epoch": 4,
                    "created_at_wall": NOW - timedelta(hours=1),
                    "revoked_at_wall": None,
                }
            ],
            "utterances": [
                {
                    "id": "utt_one",
                    "sid": "ses_current",
                    "kind": "typed",
                    "text": "给王五转50000元",
                    "normalized_scope": self.scope,
                    "echoes_untrusted": False,
                    "source_id": None,
                    "parent_ids": [],
                    "status": "ACTIVE",
                    "parse_status": "SCOPED",
                    "clarification_id": None,
                    "created_at_wall": NOW - timedelta(minutes=1),
                    "expires_at_wall": NOW + timedelta(minutes=5),
                    "authorization_epoch": 4,
                }
            ],
            "plans": [],
            "evaluations": [],
            "hints": [],
        }

    @asynccontextmanager
    async def connect(self):
        yield self

    begin = connect

    async def scalar(self, _statement):
        return None

    async def execute(self, statement):
        if getattr(statement, "is_insert", False):
            row = deepcopy(statement.compile().params)
            self.tables.setdefault(statement.table.name, []).append(row)
            return Rows()
        table = statement.get_final_froms()[0].name
        return Rows(self.tables.get(table, []))


async def run_g4(monkeypatch, boundary, *, decision_mutator=None):
    async def pipeline(ctx):
        template = replan_state()["decision"]
        template.update(plan_id=ctx.plan_id, evaluation_id="evl_placeholder")
        template["replan_hints"][0].update(
            expires_at_wall=(NOW + timedelta(minutes=10)).isoformat()
        )
        if decision_mutator:
            decision_mutator(template)
        ctx.decision = Decision.model_validate(template)
        ctx.snapshot = w3_state()
        args = ctx.plan_draft.steps[0].args.model_dump(mode="json")
        ctx.steps = (
            ResolvedStep("step_one", "transfer", args, "TRANSFER_EXTERNAL", "YELLOW", True),
        )

    async def no_audit(*_args, **_kwargs):
        return None

    async def idempotent(_conn, *, produce, **_kwargs):
        status, result = await produce()
        return status, result, False

    monkeypatch.setattr(plans, "run_pipeline", pipeline)
    monkeypatch.setattr(plans, "audit_append", no_audit)
    monkeypatch.setattr(plans, "request_idempotent", idempotent)
    caller = Caller(
        kind=ServiceKind.A,
        customer_id="cus_a",
        sid="ses_current",
        utts=("utt_one",),
        scopes=frozenset({"assistant"}),
    )
    request = SimpleNamespace(
        state=SimpleNamespace(caller=caller, trace_id="trace_synthetic"),
        app=SimpleNamespace(
            state=SimpleNamespace(
                db=boundary,
                l3=object(),
                wall_now=lambda: NOW,
                settings=SimpleNamespace(profile="B2"),
            )
        ),
    )

    async def body():
        return boundary.draft.model_dump(mode="json")

    request.json = body
    response = await plans.evaluate_plan(request)
    return json.loads(response.body), request


def test_g4_persists_its_returned_hint_for_the_same_session_g22a_view(monkeypatch):
    async def run():
        boundary = MemoryBoundary()
        response, request = await run_g4(monkeypatch, boundary)
        assert len(boundary.tables["hints"]) == 1
        stored = boundary.tables["hints"][0]
        assert stored["id"] == response["replan_hints"][0]["id"]
        assert stored["parent_plan_id"] == response["plan_id"]
        assert len(stored["view_hash"]) == 64
        assert stored["expires_at_wall"] == NOW + timedelta(minutes=5)
        assert (
            response["replan_hints"][0]["expires_at_wall"] == stored["expires_at_wall"].isoformat()
        )
        assert stored["scope_json"] == response["replan_hints"][0]["scope"]
        assert "binding" not in stored["scope_json"] and stored["used_by"] is None

        async def owned_session(*_args, **_kwargs):
            return boundary.tables["sessions"][0]

        monkeypatch.setattr(sessions, "session_for", owned_session)
        view = await sessions.get_hint("ses_current", stored["id"], request)
        assert view == response["replan_hints"][0]
        assert "view_hash" not in view and "parent_plan_id" not in view

    asyncio.run(run())


@pytest.mark.parametrize(
    "fault",
    [
        "session_customer",
        "session_epoch",
        "root_status",
        "root_epoch",
        "root_expired",
        "root_scope",
        "root_source",
        "plan_status",
        "plan_hash",
        "plan_draft",
        "evaluation_id",
        "evaluation_payload",
        "revision",
        "hint_scope",
        "hint_target",
        "hint_source",
        "hint_ttl",
        "label",
        "hash",
        "unicode_hash",
        "used",
        "missing_parent",
        "malformed_scope",
    ],
)
def test_published_hint_binding_rejects_tampering_or_stale_authorization(monkeypatch, fault):
    from services.gateway.app.errors import GatewayError
    from services.gateway.app.hint_sources import validate_hint_source

    async def run():
        boundary = MemoryBoundary()
        await run_g4(monkeypatch, boundary)
        session = boundary.tables["sessions"][0]
        root = boundary.tables["utterances"][0]
        plan = boundary.tables["plans"][0]
        evaluation = boundary.tables["evaluations"][0]
        hint = boundary.tables["hints"][0]
        if fault == "session_customer":
            session["customer_id"] = "cus_other"
        elif fault == "session_epoch":
            session["authorization_epoch"] += 1
        elif fault == "root_status":
            root["status"] = "SUPERSEDED"
        elif fault == "root_epoch":
            root["authorization_epoch"] += 1
        elif fault == "root_expired":
            root["expires_at_wall"] = NOW
        elif fault == "root_scope":
            root["normalized_scope"]["transfers"][0]["amount_cents"] -= 1
        elif fault == "root_source":
            root["source_id"] = "forged_source"
        elif fault == "plan_status":
            plan["status"] = "SUPERSEDED"
        elif fault == "plan_hash":
            plan["plan_hash"] = "other_plan_hash"
        elif fault == "plan_draft":
            plan["plan_draft"]["steps"][0]["args"]["amount_cents"] -= 1
        elif fault == "evaluation_id":
            evaluation["id"] = "evl_other"
        elif fault == "evaluation_payload":
            evaluation["findings"] = [{"code": "RISKY_SEQUENCE", "effect": "DENY"}]
        elif fault == "revision":
            evaluation["revision"] += 1
        elif fault == "hint_scope":
            hint["scope_json"]["transfers"][0]["amount_cents"] = 5000001
        elif fault == "hint_target":
            hint["scope_json"]["transfers"][0]["target_fingerprint"] = "other_target"
        elif fault == "hint_source":
            hint["scope_json"]["from_account_id"] = "other_account"
        elif fault == "hint_ttl":
            hint["expires_at_wall"] = NOW
        elif fault == "label":
            hint["label"] = "最多50000元"
        elif fault == "hash":
            hint["view_hash"] = "0" * 64
        elif fault == "unicode_hash":
            hint["view_hash"] = "非" * 64
        elif fault == "used":
            hint["used_by"] = "utt_already_adopted"
        elif fault == "missing_parent":
            hint["parent_plan_id"] = None
        else:
            hint["scope_json"] = "invalid-scope"
        before = deepcopy(boundary.tables)
        with pytest.raises(GatewayError) as error:
            await validate_hint_source(boundary, session, hint, NOW)
        assert error.value.status == 409
        assert boundary.tables == before

    asyncio.run(run())


@pytest.mark.parametrize("change", ["target", "source", "amount", "new_action"])
def test_g4_never_publishes_a_hint_that_expands_the_original_server_scope(monkeypatch, change):
    from services.gateway.app.errors import GatewayError

    def mutate(decision):
        scope = decision["replan_hints"][0]["scope"]
        if change == "target":
            scope["transfers"][0]["target_fingerprint"] = "another_target"
        elif change == "source":
            scope["from_account_id"] = "another_account"
        elif change == "amount":
            scope["transfers"][0]["amount_cents"] = 5000001
        else:
            scope["action_kinds"].append("PAYEE_ADD")

    async def run():
        boundary = MemoryBoundary()
        with pytest.raises(GatewayError):
            await run_g4(monkeypatch, boundary, decision_mutator=mutate)
        assert boundary.tables["hints"] == []

    asyncio.run(run())


def test_binding_survives_postgres_equivalent_timezone_timestamp_roundtrip(monkeypatch):
    from datetime import timezone

    from services.gateway.app.hint_sources import validate_hint_source

    async def run():
        boundary = MemoryBoundary()
        response, request = await run_g4(monkeypatch, boundary)
        for table in ("hints", "utterances"):
            for row in boundary.tables[table]:
                for key in ("created_at_wall", "expires_at_wall"):
                    row[key] = row[key].astimezone(timezone(timedelta(hours=8)))
        await validate_hint_source(
            boundary, boundary.tables["sessions"][0], boundary.tables["hints"][0], NOW
        )

        async def owned_session(*_args, **_kwargs):
            return boundary.tables["sessions"][0]

        monkeypatch.setattr(sessions, "session_for", owned_session)
        viewed = await sessions.get_hint("ses_current", response["replan_hints"][0]["id"], request)
        assert viewed == response["replan_hints"][0]

    asyncio.run(run())


@pytest.mark.parametrize("race", ["epoch", "superseded", "expired"])
def test_g4_rechecks_the_source_after_pipeline_before_publishing(monkeypatch, race):
    from services.gateway.app.errors import GatewayError

    async def run():
        boundary = MemoryBoundary()

        def change_after_read(_decision):
            if race == "epoch":
                boundary.tables["sessions"][0]["authorization_epoch"] += 1
            elif race == "superseded":
                boundary.tables["utterances"][0]["status"] = "SUPERSEDED"
            else:
                boundary.tables["utterances"][0]["expires_at_wall"] = NOW

        with pytest.raises(GatewayError):
            await run_g4(monkeypatch, boundary, decision_mutator=change_after_read)
        assert boundary.tables["hints"] == []

    asyncio.run(run())
