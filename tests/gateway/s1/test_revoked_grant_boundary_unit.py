"""Revocation/backfill boundary with real S1 and bankcore, no service or database."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest
from safebank_bankcore.grants import instruction_scope
from safebank_bankcore.ids import digest
from sqlalchemy import Select
from sqlalchemy.dialects import postgresql

from services.gateway.app.internal import scheduled as s1
from tests.bankcore.fixtures import NOW
from tests.bankcore.obligations_fixtures import (
    raw_billing,
    raw_mandate,
    raw_merchant,
    raw_projection_state,
)
from tests.gateway.s1.conftest import make_event_def


class Rows:
    def __init__(self, values=()):
        self.values = values
        self.rowcount = 1

    def mappings(self):
        return self

    def first(self):
        return self.values[0] if self.values else None


class GrantDB:
    def __init__(self, grant=None):
        self.grant = deepcopy(grant)
        self.notifications = {}
        self.terminal = None
        self.audit = []

    @asynccontextmanager
    async def connect(self):
        yield self

    @asynccontextmanager
    async def begin(self):
        yield self

    async def execute(self, statement, params=None):
        if isinstance(statement, Select):
            assert statement.get_final_froms()[0].name == "scheduled_grants"
            return Rows([self.grant] if self.grant else [])
        if params is not None:
            assert "UPDATE event_decisions" in str(statement)
            self.terminal = (params["status"], params["code"])
            return Rows()
        values = statement.compile(dialect=postgresql.dialect()).params
        if statement.table.name == "scheduled_grants":
            if self.grant is None:
                self.grant = values
        else:
            assert statement.table.name == "notifications"
            self.notifications.setdefault((values["event_id"], values["type"]), values)
        return Rows()


def merchant_case(*, tombstone=False, bank_status="DELETED"):
    billing, mandate = raw_billing(), raw_mandate(status=bank_status)
    state = raw_projection_state(
        billings=(billing,), mandates=(mandate,), merchants=(raw_merchant(),)
    )
    state = state.model_copy(update={"sim_now": billing.next_due_at})
    scope = instruction_scope(mandate.model_dump(mode="python"), billing.model_dump(mode="python"))
    payload = f"{billing.id}|{billing.amount_cents}|{billing.account_id}"
    event = {
        "event_id": "evt_cancelled_charge",
        "kind": "MERCHANT_CHARGE",
        "customer_id": billing.customer_id,
        "account_id": billing.account_id,
        "amount_cents": billing.amount_cents,
        "source_id": billing.id,
        "billing_id": billing.id,
        "merchant_id": billing.merchant_id,
        "instruction_id": mandate.id,
        "source_plan_id": mandate.source_plan_id,
        "grant_scope_hash": mandate.grant_scope_hash,
        "grant_scope": scope,
        "due_at": billing.next_due_at.isoformat(),
        "period_key": billing.next_due_at.isoformat(),
        "payload": payload,
        "payload_hash": digest(payload),
    }
    grant = {
        "instruction_id": mandate.id,
        "customer_id": mandate.customer_id,
        "source_plan_id": mandate.source_plan_id,
        "scope_hash": mandate.grant_scope_hash,
        "scope_json": {"instruction_scope": scope},
        "approved_risk": "YELLOW",
        "status": "REVOKED",
        "revoked_at": NOW,
        "created_at": NOW,
    }
    if tombstone:
        grant.update(
            source_plan_id="pln_cancel",
            approved_risk="RED",
            scope_hash=digest(["revoked-grant", mandate.customer_id, mandate.id]),
            scope_json={
                "revocation": {
                    "plan_id": "pln_cancel",
                    "step_id": "stp_cancel",
                    "tool": "cancel_subscription",
                }
            },
        )
    return event, grant, state


async def authorize(monkeypatch, event, grant, state):
    db, calls = GrantDB(grant), []

    async def claimed(*args):
        return s1._ClaimResult.CLAIMED, None

    async def audit(conn, **kwargs):
        db.audit.append(kwargs)

    async def definition(event_id):
        return deepcopy(event)

    async def snapshot(body):
        calls.append(("snapshot", body))
        return state.model_dump(mode="json")

    async def block(event_id, body):
        calls.append(("block", body))
        return {
            "event_id": event_id,
            "customer_id": event["customer_id"],
            "status": body["status"],
            "result": {"code": body["code"]},
        }

    async def execute(event_id, body):
        calls.append(("execute", body))
        return {"event_id": event_id, "customer_id": event["customer_id"], "status": "SUCCESS"}

    monkeypatch.setattr(s1, "_claim_event", claimed)
    monkeypatch.setattr(s1, "audit_append", audit)
    app = SimpleNamespace(
        db=db,
        wall_now=lambda: NOW,
        l3=SimpleNamespace(
            event=definition, snapshot=snapshot, event_block=block, event_execute=execute
        ),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=app), state=SimpleNamespace(trace_id=None))
    result = await s1.authorize_event(request, s1.AuthorizeRequest(event_id=event["event_id"]))
    return result, db, calls


@pytest.mark.parametrize("tombstone", [False, True])
def test_legitimate_cancelled_mandate_emits_not_active_and_charge_blocked(monkeypatch, tombstone):
    async def check():
        event, grant, state = merchant_case(tombstone=tombstone)
        result, db, calls = await authorize(monkeypatch, event, grant, state)
        assert result["status"] == "BLOCKED" and result["code"] == "MANDATE_NOT_ACTIVE"
        assert db.terminal == ("BLOCKED", "MANDATE_NOT_ACTIVE")
        assert (event["event_id"], "CHARGE_BLOCKED") in db.notifications
        assert not any(name == "execute" for name, _ in calls)
        assert state.accounts["acc_a"].balance_cents == 600000

    asyncio.run(check())


def test_backfill_returns_persisted_revocation_after_insert_conflict():
    async def check():
        event = make_event_def(event_id="evt_backfill", source_id="si_backfill")
        existing = {"instruction_id": "si_backfill", "customer_id": "cus_a", "status": "REVOKED"}
        db = GrantDB(existing)

        async def execution(*_):
            return {
                "status": "COMMITTED",
                "result": {
                    "status": "EXECUTED",
                    "steps": [
                        {
                            "status": "EXECUTED",
                            "tool": "schedule_transfer",
                            "created_ids": {"instruction_id": "si_backfill"},
                            "result": {
                                "instruction_id": "si_backfill",
                                "grant_scope_hash": event["grant_scope_hash"],
                            },
                        }
                    ],
                },
            }

        actual = await s1._backfill_grant(
            db, SimpleNamespace(execution=execution), event, lambda: NOW
        )
        assert actual == existing and actual["status"] == "REVOKED"
        assert db.grant == existing

    asyncio.run(check())


@pytest.mark.parametrize("tombstone", [False, True])
@pytest.mark.parametrize(
    "fault",
    [
        "bank_active",
        "event_customer",
        "grant_customer",
        "grant_source",
        "grant_hash",
        "grant_instruction",
        "scope",
        "event_hash",
        "event_source",
        "event_source_plan",
        "event_account",
        "event_amount",
        "event_payload",
        "event_payload_hash",
        "event_period",
        "missing_revocation_time",
        "future_due",
    ],
)
def test_revoked_grant_forgery_or_active_bank_never_executes(monkeypatch, tombstone, fault):
    async def check():
        event, grant, state = merchant_case(
            tombstone=tombstone, bank_status="ACTIVE" if fault == "bank_active" else "DELETED"
        )
        if fault == "event_customer":
            event["customer_id"] = "cus_other"
        elif fault == "grant_customer":
            grant["customer_id"] = "cus_other"
        elif fault == "grant_source":
            grant["source_plan_id"] = "pln_other"
        elif fault == "grant_hash":
            grant["scope_hash"] = "wrong"
        elif fault == "grant_instruction":
            grant["instruction_id"] = "si_other"
        elif fault == "scope":
            grant["scope_json"] = {"instruction_scope": {"kind": "MERCHANT_MANDATE"}}
        elif fault == "event_hash":
            event["grant_scope_hash"] = "wrong"
        elif fault == "event_source":
            event["source_id"] = "bill_other"
        elif fault == "event_source_plan":
            event["source_plan_id"] = "pln_other"
        elif fault == "event_account":
            event["account_id"] = "acc_b"
        elif fault == "event_amount":
            event["amount_cents"] = 4000
        elif fault == "event_payload":
            event["payload"] = "forged"
        elif fault == "event_payload_hash":
            event["payload_hash"] = "wrong"
        elif fault == "event_period":
            event["period_key"] = "2027-01-01T06:00:00+08:00"
        elif fault == "missing_revocation_time":
            grant["revoked_at"] = None
        elif fault == "future_due":
            state = state.model_copy(update={"sim_now": NOW})
        result, db, calls = await authorize(monkeypatch, event, grant, state)
        assert result["code"] == "SCHEDULED_BYPASS"
        assert db.terminal == ("BLOCKED", "SCHEDULED_BYPASS")
        assert not any(name == "execute" for name, _ in calls)

    asyncio.run(check())


def test_active_status_with_revocation_timestamp_cannot_execute(monkeypatch):
    async def check():
        event, grant, state = merchant_case(bank_status="ACTIVE")
        grant["status"] = "ACTIVE"
        result, _, calls = await authorize(monkeypatch, event, grant, state)
        assert result["code"] == "SCHEDULED_BYPASS"
        assert not any(name == "execute" for name, _ in calls)

    asyncio.run(check())


def test_current_active_original_grant_keeps_its_execute_path(monkeypatch):
    async def check():
        event, grant, state = merchant_case(bank_status="ACTIVE")
        grant.update(status="ACTIVE", revoked_at=None)
        result, _, calls = await authorize(monkeypatch, event, grant, state)
        assert result["status"] == "SUCCESS"
        assert sum(name == "execute" for name, _ in calls) == 1

    asyncio.run(check())


@pytest.mark.parametrize(
    "field,value",
    [
        ("tool", "cancel_scheduled"),
        ("step_id", ""),
        ("plan_id", "pln_wrong"),
        ("extra", "unrecognized"),
    ],
)
def test_malformed_tombstone_marker_is_not_a_cancellation_binding(monkeypatch, field, value):
    async def check():
        event, grant, state = merchant_case(tombstone=True)
        grant["scope_json"]["revocation"][field] = value
        result, _, calls = await authorize(monkeypatch, event, grant, state)
        assert result["code"] == "SCHEDULED_BYPASS"
        assert not any(name == "execute" for name, _ in calls)

    asyncio.run(check())


def test_tombstone_cannot_be_relabelled_active_creation_consent(monkeypatch):
    async def check():
        event, grant, state = merchant_case(tombstone=True, bank_status="ACTIVE")
        grant.update(status="ACTIVE", revoked_at=None)
        result, _, calls = await authorize(monkeypatch, event, grant, state)
        assert result["code"] == "SCHEDULED_BYPASS"
        assert not any(name == "execute" for name, _ in calls)

    asyncio.run(check())


def test_revoked_original_seed_consent_keeps_deleted_mandate_semantics(monkeypatch):
    from safebank_bankcore.grants import scope_hash, seed_consent

    async def check():
        event, grant, state = merchant_case()
        consent = seed_consent(event["grant_scope"], None)
        hash_ = scope_hash(consent)
        grant.update(scope_json={"seed_consent": consent}, scope_hash=hash_)
        event["grant_scope_hash"] = hash_
        obligations = state.obligations
        mandates = dict(obligations.standing_instructions)
        mandates[event["instruction_id"]] = mandates[event["instruction_id"]].model_copy(
            update={"grant_scope_hash": hash_}
        )
        state = state.model_copy(
            update={
                "obligations": obligations.model_copy(update={"standing_instructions": mandates})
            }
        )
        result, _, calls = await authorize(monkeypatch, event, grant, state)
        assert result["code"] == "MANDATE_NOT_ACTIVE"
        assert not any(name == "execute" for name, _ in calls)

    asyncio.run(check())


def test_backfill_new_grant_returns_the_actual_inserted_row():
    async def check():
        event = make_event_def(event_id="evt_new", source_id="si_new")
        db = GrantDB()

        async def execution(*_):
            return {
                "status": "COMMITTED",
                "result": {
                    "status": "EXECUTED",
                    "steps": [
                        {
                            "status": "EXECUTED",
                            "tool": "schedule_transfer",
                            "created_ids": {"instruction_id": "si_new"},
                            "result": {
                                "instruction_id": "si_new",
                                "grant_scope_hash": event["grant_scope_hash"],
                            },
                        }
                    ],
                },
            }

        actual = await s1._backfill_grant(
            db, SimpleNamespace(execution=execution), event, lambda: NOW
        )
        assert actual == db.grant
        assert actual["status"] == "ACTIVE" and actual["instruction_id"] == "si_new"

    asyncio.run(check())
