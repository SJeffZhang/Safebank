"""Event notification templates/identity tested without DB or provider IO."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql


class NotificationConn:
    def __init__(self, error=None):
        self.statements = []
        self.rows = {}
        self.error = error

    async def execute(self, statement):
        if self.error:
            raise self.error
        compiled = statement.compile(dialect=postgresql.dialect())
        self.statements.append((str(compiled), compiled.params))
        values = compiled.params
        key = (values["event_id"], values["type"])
        self.rows.setdefault(key, values)
        return SimpleNamespace(rowcount=1)


def event(kind, **overrides):
    value = {
        "event_id": "evt_authoritative",
        "customer_id": "cus_one",
        "kind": kind,
        "source_id": "source_one",
        "source_plan_id": "pln_authoritative",
        "payload": {
            "customer_id": "cus_other",
            "plan_id": "pln_untrusted",
            "memo": "EVIL自由文本",
            "merchant": "EVIL商户",
        },
    }
    value.update(overrides)
    return value


def run(conn, definition, status):
    from services.gateway.app.event_notifications import notify_event_result

    return asyncio.run(notify_event_result(conn, event=definition, status=status))


@pytest.mark.parametrize(
    "kind,status,notification_type,phrase",
    [
        ("SCHEDULED_TRANSFER", "SUCCESS", "SCHEDULE_RESULT", "定时转账已完成"),
        ("SCHEDULED_TRANSFER", "BLOCKED", "SCHEDULE_RESULT", "定时转账未执行"),
        ("SCHEDULED_TRANSFER", "FAILED_INSUFFICIENT", "SCHEDULE_RESULT", "余额不足"),
        ("SCHEDULED_TRANSFER", "SKIPPED_REAPPROVAL", "SCHEDULE_RESULT", "重新确认"),
        ("MERCHANT_CHARGE", "BLOCKED", "CHARGE_BLOCKED", "代扣未执行"),
        ("MERCHANT_CHARGE", "FAILED_INSUFFICIENT", "CHARGE_BLOCKED", "余额不足"),
        ("MERCHANT_CHARGE", "SKIPPED_REAPPROVAL", "CHARGE_BLOCKED", "重新确认"),
        ("FUND_CONFIRM", "SUCCESS", "FUND_RESULT", "确认处理"),
        ("FUND_SETTLE", "SUCCESS", "FUND_RESULT", "结算处理"),
        ("FUND_EXPIRY", "SUCCESS", "FUND_RESULT", "到期处理"),
        ("FUND_CONFIRM", "BLOCKED", "FUND_RESULT", "未完成"),
        ("FUND_SETTLE", "FAILED_INSUFFICIENT", "FUND_RESULT", "未完成"),
        ("FUND_EXPIRY", "BLOCKED", "FUND_RESULT", "未完成"),
        ("REMINDER", "SUCCESS", "CHARGE_UPCOMING", "未来三天"),
    ],
)
def test_event_types_render_fixed_templates_for_authoritative_customer(
    kind, status, notification_type, phrase
):
    conn = NotificationConn()
    run(conn, event(kind), status)
    assert len(conn.rows) == 1
    row = next(iter(conn.rows.values()))
    assert row["type"] == notification_type and row["customer_id"] == "cus_one"
    assert row["event_id"] == "evt_authoritative"
    assert phrase in row["payload"]["title"] + row["payload"]["body"]
    assert row["payload"]["related_plan_id"] == "pln_authoritative"
    assert (
        "EVIL" not in str(row) and "cus_other" not in str(row) and "pln_untrusted" not in str(row)
    )
    assert "ON CONFLICT (event_id, type) DO NOTHING" in conn.statements[0][0]


@pytest.mark.parametrize(
    "kind,status",
    [
        ("MERCHANT_CHARGE", "SUCCESS"),
        ("REMINDER", "BLOCKED"),
        ("REMINDER", "RETRYING"),
        ("FUND_CONFIRM", "IN_PROGRESS"),
        ("SCHEDULED_TRANSFER", "RETRYING"),
        ("DEMO_EVENT", "SUCCESS"),
    ],
)
def test_nonterminal_or_nonnotifying_events_do_not_notify(kind, status):
    conn = NotificationConn()
    run(conn, event(kind), status)
    assert conn.statements == []


@pytest.mark.parametrize(
    "kind,order_status,phrase",
    [
        ("FUND_CONFIRM", "SETTLED", "申购已确认"),
        ("FUND_SETTLE", "SETTLED", "赎回资金已到账"),
        ("FUND_EXPIRY", "EXPIRED", "理财订单已过期"),
    ],
)
def test_specific_fund_outcome_requires_authoritative_actual_order_state(
    kind, order_status, phrase
):
    conn = NotificationConn()
    definition = event(
        kind,
        execution={
            "event_id": "evt_authoritative",
            "customer_id": "cus_one",
            "result": {"order_status": order_status, "no_op": False},
        },
    )
    run(conn, definition, "SUCCESS")
    assert phrase in next(iter(conn.rows.values()))["payload"]["title"]
    # A completed expiry sweep can be a no-op on a previously settled order.
    definition["event_id"] = "evt_noop"
    definition["execution"].update(event_id="evt_noop")
    definition["execution"]["result"]["no_op"] = True
    run(conn, definition, "SUCCESS")
    assert len(conn.rows) == 1


def test_replay_has_stable_identity_and_cannot_change_owner_from_payload():
    conn = NotificationConn()
    first = event("SCHEDULED_TRANSFER")
    run(conn, first, "SUCCESS")
    run(conn, deepcopy(first), "SUCCESS")
    assert len(conn.rows) == 1
    second = event("SCHEDULED_TRANSFER", event_id="evt_other", customer_id="cus_other")
    run(conn, second, "SUCCESS")
    assert {row["customer_id"] for row in conn.rows.values()} == {"cus_one", "cus_other"}


def test_related_plan_never_comes_from_payload_or_source_id():
    conn = NotificationConn()
    definition = event("FUND_EXPIRY")
    definition.pop("source_plan_id")
    run(conn, definition, "SUCCESS")
    assert "related_plan_id" not in next(iter(conn.rows.values()))["payload"]


@pytest.mark.parametrize(
    "fault",
    ["owner_missing", "event_missing", "source_missing", "execution_owner", "execution_event"],
)
def test_incomplete_or_cross_customer_definition_fails_closed(fault):
    conn = NotificationConn()
    definition = event("FUND_CONFIRM")
    if fault == "owner_missing":
        definition.pop("customer_id")
    elif fault == "event_missing":
        definition.pop("event_id")
    elif fault == "source_missing":
        definition.pop("source_id")
    else:
        definition["execution"] = (
            {"customer_id": "cus_other"}
            if fault == "execution_owner"
            else {"event_id": "evt_other"}
        )
    with pytest.raises(ValueError):
        run(conn, definition, "SUCCESS")
    assert conn.statements == []


def test_notification_write_failure_propagates_to_producer_transaction():
    with pytest.raises(RuntimeError, match="transaction failed"):
        run(
            NotificationConn(RuntimeError("transaction failed")),
            event("SCHEDULED_TRANSFER"),
            "SUCCESS",
        )


def test_inconsistent_terminal_result_cannot_notify_success():
    definition = event("SCHEDULED_TRANSFER", execution={"status": "BLOCKED"})
    conn = NotificationConn()
    with pytest.raises(ValueError, match="outcome mismatch"):
        run(conn, definition, "SUCCESS")
    assert conn.rows == {}


def test_fund_expiry_sweep_on_a_settled_order_never_claims_expired():
    conn = NotificationConn()
    definition = event("FUND_EXPIRY", execution={"result": {"order_status": "SETTLED"}})
    run(conn, definition, "SUCCESS")
    payload = next(iter(conn.rows.values()))["payload"]
    assert "已过期" not in payload["title"] + payload["body"]
    assert "到期处理" in payload["title"]


def test_failed_fund_event_still_notifies_when_no_effects_were_applied():
    conn = NotificationConn()
    definition = event(
        "FUND_SETTLE", execution={"status": "FAILED_INSUFFICIENT", "result": {"no_op": True}}
    )
    run(conn, definition, "FAILED_INSUFFICIENT")
    assert len(conn.rows) == 1
    assert "未完成" in next(iter(conn.rows.values()))["payload"]["title"]
