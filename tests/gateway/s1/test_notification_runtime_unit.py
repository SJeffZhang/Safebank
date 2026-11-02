"""Producer transaction/normalization tests with no DB, HTTP or provider IO."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from services.gateway.app.errors import GatewayError
from services.gateway.app.internal import scheduled as s1


class MemoryOutcomeDB:
    def __init__(self):
        self.state = {"status": "IN_PROGRESS", "notifications": {}, "audit": []}
        self.fail_notification = False
        self.conn = None

    @asynccontextmanager
    async def begin(self):
        before = deepcopy(self.state)
        self.conn = SimpleNamespace(execute=self.execute)
        try:
            yield self.conn
        except BaseException:
            self.state = before
            raise

    async def execute(self, statement, params=None):
        if params is not None:
            if self.state["status"] != "IN_PROGRESS":
                return SimpleNamespace(rowcount=0)
            self.state["status"] = params["status"]
            return SimpleNamespace(rowcount=1)
        if self.fail_notification:
            self.fail_notification = False
            raise RuntimeError("notification write failed")
        values = statement.compile(dialect=postgresql.dialect()).params
        self.state["notifications"].setdefault((values["event_id"], values["type"]), values)
        return SimpleNamespace(rowcount=1)


def definition(kind="FUND_SETTLE"):
    return {
        "event_id": "evt_one",
        "customer_id": "cus_one",
        "kind": kind,
        "source_id": "source_one",
    }


async def finalize(db, monkeypatch, *, event=None, result=None):
    async def audit(conn, **kwargs):
        assert conn is db.conn
        db.state["audit"].append(kwargs)

    monkeypatch.setattr(s1, "audit_append", audit)
    return await s1._finalize_system_result(
        db,
        SimpleNamespace(wall_now=lambda: "synthetic-wall"),
        "evt_one",
        "cus_one",
        result or {"status": "SUCCESS", "result": {"order_status": "SETTLED"}},
        None,
        event=event or definition(),
    )


@pytest.mark.parametrize(
    "kind,notification",
    [
        ("FUND_SETTLE", "FUND_RESULT"),
        ("REMINDER", "CHARGE_UPCOMING"),
        ("SCHEDULED_TRANSFER", "SCHEDULE_RESULT"),
    ],
)
def test_finalization_persists_audit_and_actual_notification_on_same_connection(
    monkeypatch, kind, notification
):
    async def check():
        db = MemoryOutcomeDB()
        result = await finalize(db, monkeypatch, event=definition(kind))
        assert result["status"] == db.state["status"] == "SUCCESS"
        assert len(db.state["audit"]) == 1
        assert next(iter(db.state["notifications"].values()))["type"] == notification

    asyncio.run(check())


def test_notification_failure_rolls_back_terminal_and_audit_then_recovery_emits_once(monkeypatch):
    async def check():
        db = MemoryOutcomeDB()
        db.fail_notification = True
        with pytest.raises(GatewayError):
            await finalize(db, monkeypatch)
        assert db.state == {"status": "IN_PROGRESS", "notifications": {}, "audit": []}
        actual = {"status": "SUCCESS", "replayed": True, "result": {"order_status": "SETTLED"}}
        await finalize(db, monkeypatch, result=actual)
        await finalize(db, monkeypatch, result=actual)
        assert len(db.state["notifications"]) == len(db.state["audit"]) == 1

    asyncio.run(check())


@pytest.mark.parametrize(
    "legacy,expected", [("EXECUTED", "SUCCESS"), ("FAILED", "FAILED_INSUFFICIENT")]
)
def test_legacy_status_is_normalized_in_notification_execution_as_well_as_decision(
    monkeypatch, legacy, expected
):
    async def check():
        db = MemoryOutcomeDB()
        result = await finalize(db, monkeypatch, result={"status": legacy, "result": {}})
        assert result["status"] == db.state["status"] == expected
        assert len(db.state["notifications"]) == 1

    asyncio.run(check())


@pytest.mark.parametrize("field,value", [("event_id", "evt_other"), ("customer_id", "cus_other")])
def test_l3_result_identity_mismatch_cannot_commit_notification(monkeypatch, field, value):
    async def check():
        db = MemoryOutcomeDB()
        with pytest.raises((ValueError, GatewayError)):
            await finalize(
                db, monkeypatch, result={"status": "SUCCESS", field: value, "result": {}}
            )
        assert db.state["status"] == "IN_PROGRESS"
        assert not db.state["notifications"]

    asyncio.run(check())


def test_fund_noop_does_not_notify_but_still_finalizes_audit(monkeypatch):
    async def check():
        db = MemoryOutcomeDB()
        await finalize(
            db,
            monkeypatch,
            result={"status": "SUCCESS", "result": {"order_status": "SETTLED", "no_op": True}},
        )
        assert db.state["status"] == "SUCCESS" and len(db.state["audit"]) == 1
        assert not db.state["notifications"]

    asyncio.run(check())


@pytest.mark.parametrize(
    "status,expected", [("BLOCKED", "REJECTED"), ("RETRYING", "RETRYING"), ("SUCCESS", "SUCCESS")]
)
def test_d4_return_shape_preserves_non_success_and_maps_only_blocked(status, expected):
    from services.gateway.app.api.demo import _demo_result

    source = {"status": status, "code": "SPLIT_INVALID"}
    public = _demo_result(source)
    assert public["status"] == expected and public["code"] == "SPLIT_INVALID"
    assert source["status"] == status


def reminder_case():
    from app.reminders import build_reminder_job
    from safebank_bankcore.grants import instruction_scope

    from tests.bankcore.fixtures import NOW
    from tests.bankcore.obligations_fixtures import (
        raw_billing,
        raw_mandate,
        raw_merchant,
        raw_projection_state,
    )

    billing, mandate = raw_billing(), raw_mandate()
    state = raw_projection_state(
        billings=(billing,), mandates=(mandate,), merchants=(raw_merchant(),)
    )
    job = build_reminder_job(
        billing=billing.model_dump(mode="python"),
        mandate=mandate.model_dump(mode="python"),
        scan_at=NOW,
    )
    scope = instruction_scope(mandate.model_dump(mode="python"), billing.model_dump(mode="python"))
    event = {
        "event_id": job["id"],
        "kind": "REMINDER",
        "customer_id": "cus_a",
        "source_id": billing.id,
        "billing_id": billing.id,
        "instruction_id": mandate.id,
        "source_plan_id": mandate.source_plan_id,
        "grant_scope_hash": mandate.grant_scope_hash,
        "grant_scope": scope,
        "payload_hash": job["payload_hash"],
        "payload": job["immutable_payload"]["payload"],
        "due_at": NOW.isoformat(),
        "account_id": billing.account_id,
        "amount_cents": billing.amount_cents,
    }
    grant = {
        "instruction_id": mandate.id,
        "customer_id": "cus_a",
        "source_plan_id": mandate.source_plan_id,
        "scope_hash": mandate.grant_scope_hash,
        "scope_json": {"instruction_scope": scope},
        "status": "ACTIVE",
        "revoked_at": None,
        "approved_risk": "YELLOW",
    }
    return event, grant, state


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "no_grant",
        "revoked",
        "revoked_at",
        "customer",
        "scope_hash",
        "source_plan",
        "grant_scope",
        "cancelled",
        "amount",
        "charge_due",
        "not_due",
        "already_due",
        "payload_owner",
    ],
)
def test_s1_reminder_requires_current_matching_unrevoked_grant_and_snapshot(monkeypatch, fault):
    async def check():
        from datetime import timedelta

        from tests.bankcore.fixtures import NOW

        event, grant, state = reminder_case()
        if fault == "no_grant":
            grant = None
        elif fault == "revoked":
            grant["status"] = "REVOKED"
        elif fault == "revoked_at":
            grant["revoked_at"] = NOW
        elif fault == "customer":
            grant["customer_id"] = "cus_other"
        elif fault == "scope_hash":
            grant["scope_hash"] = "wrong"
        elif fault == "source_plan":
            grant["source_plan_id"] = "pln_other"
        elif fault == "grant_scope":
            grant["scope_json"] = {"instruction_scope": {}}
        elif fault in ("cancelled", "amount", "charge_due"):
            obligations = state.obligations
            if fault == "cancelled":
                items = dict(obligations.standing_instructions)
                items[event["instruction_id"]] = items[event["instruction_id"]].model_copy(
                    update={"status": "DELETED"}
                )
                obligations = obligations.model_copy(update={"standing_instructions": items})
            else:
                items = dict(obligations.billings)
                field, value = (
                    ("amount_cents", 4000)
                    if fault == "amount"
                    else ("next_due_at", NOW + timedelta(days=7))
                )
                items[event["source_id"]] = items[event["source_id"]].model_copy(
                    update={field: value}
                )
                obligations = obligations.model_copy(update={"billings": items})
            state = state.model_copy(update={"obligations": obligations})
        elif fault == "not_due":
            state = state.model_copy(update={"sim_now": NOW - timedelta(seconds=1)})
        elif fault == "already_due":
            state = state.model_copy(
                update={"sim_now": state.obligations.billings[event["source_id"]].next_due_at}
            )
        elif fault == "payload_owner":
            event["payload"]["customer_id"] = "cus_other"
        calls = []

        @asynccontextmanager
        async def connect():
            yield object()

        async def load_grant(*_):
            return grant

        async def snapshot(*_):
            return state.model_dump(mode="json")

        async def execute(event_id, body):
            calls.append(body)
            return {
                "event_id": event_id,
                "customer_id": "cus_a",
                "status": "SUCCESS",
                "result": {"status": "SUCCESS"},
            }

        async def blocked(*args, **kwargs):
            return {"status": "BLOCKED"}

        async def finalized(*args, **kwargs):
            assert kwargs["event"] == event
            return {"status": args[4]["status"]}

        monkeypatch.setattr(s1, "_load_grant", load_grant)
        monkeypatch.setattr(s1, "_block", blocked)
        monkeypatch.setattr(s1, "_finalize_system_result", finalized)
        app = SimpleNamespace(
            db=SimpleNamespace(connect=connect),
            l3=SimpleNamespace(snapshot=snapshot, event_execute=execute),
        )
        request = SimpleNamespace(
            app=SimpleNamespace(state=app), state=SimpleNamespace(trace_id=None)
        )
        result = await s1._handle_reminder_event(request, event)
        assert result["status"] == ("SUCCESS" if fault is None else "BLOCKED")
        assert len(calls) == (1 if fault is None else 0)
        if calls:
            assert calls[0]["expected_versions"] == dict(state.versions)
            assert calls[0]["clock_version"] == state.clock_version
            assert calls[0]["expected_payload_hash"] == event["payload_hash"]
            assert calls[0]["authorization_evidence"] == {
                "type": "SYSTEM_REMINDER",
                "billing_id": event["source_id"],
                "instruction_id": event["instruction_id"],
            }

    asyncio.run(check())
