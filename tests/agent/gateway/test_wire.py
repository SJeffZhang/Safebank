"""L1-local gateway wire model validation (C1c1 Task 1).

These models mirror the real L2 wire shapes (PRD §9.5) without importing gateway code.
The key rules: legitimate hint/action utterances may have ``text=null`` and every wire
field must survive a round-trip; unknown routing enums, non-integer amounts and missing
plan-binding fields must fail validation rather than be accepted as success.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from services.agent.app.gateway.wire import (
    GatewayClarification,
    GatewayDecision,
    GatewayReadEnvelope,
    GatewayUtterance,
    UnknownExecution,
)


def utterance(id: str, **overrides) -> dict:
    body = {
        "id": id,
        "kind": "typed",
        "text": "给王五转 800",
        "scope": {
            "action_kinds": ["TRANSFER_EXTERNAL"],
            "from_account_id": "acc_a",
            "transfers": [],
            "period": None,
            "channel": None,
            "fields": {},
        },
        "parse_status": "SCOPED",
        "echoes_untrusted": False,
        "parent_ids": [],
        "clarification_id": None,
        "created_at_wall": "2026-10-24T00:00:00Z",
        "expires_at_wall": "2026-10-24T00:10:00Z",
    }
    body.update(overrides)
    return body


def decision(**overrides) -> dict:
    body = {
        "plan_id": "pln_a",
        "evaluation_id": "evl_a",
        "revision": 1,
        "decision": "REQUIRE_APPROVAL",
        "risk": "YELLOW",
        "approval_id": "apv_a",
        "findings": [],
        "replan_hints": [],
        "preflight": {
            "executable": True,
            "steps": [],
            "balances": [],
            "holdings": [],
            "scheduled_reserve_cents": 4500,
        },
        "versions": {"cus_zs": 1},
        "clock_version": 1,
        "policy_version": "sha256:abc",
        "shadow_b1": None,
    }
    body.update(overrides)
    return body


def test_utterance_full_wire_roundtrip():
    parsed = GatewayUtterance.model_validate(utterance("utt_a"))
    assert parsed.id == "utt_a"
    assert parsed.kind == "typed"
    assert parsed.text == "给王五转 800"
    assert parsed.parse_status == "SCOPED"
    assert parsed.scope.action_kinds == ("TRANSFER_EXTERNAL",)


def test_utterance_hint_and_action_null_text_accepted():
    hint = GatewayUtterance.model_validate(utterance("utt_h", kind="hint", text=None))
    action = GatewayUtterance.model_validate(utterance("utt_a", kind="action", text=None))
    assert hint.text is None
    assert action.kind == "action"


def test_utterance_requires_scope_and_status():
    with pytest.raises(ValidationError):
        GatewayUtterance.model_validate(
            {"id": "utt_a", "kind": "typed", "text": "x", "parse_status": "SCOPED"}
        )


def test_utterance_rejects_unknown_extra_field():
    with pytest.raises(ValidationError):
        GatewayUtterance.model_validate(utterance("utt_a", secret="SERVICE_TOKEN"))


def test_read_envelope_preserves_raw_data():
    parsed = GatewayReadEnvelope.model_validate(
        {
            "data": {"accounts": [{"id": "acc_a"}], "daily_outflow_cents": 0},
            "source_ref": "src_1",
            "untrusted_paths": ["accounts.0.display_name"],
            "versions": {"cus_a": 3},
            "observed_at": "2026-10-24T00:00:00Z",
        }
    )
    assert parsed.data["accounts"][0]["id"] == "acc_a"
    assert parsed.source_ref == "src_1"
    assert parsed.versions == {"cus_a": 3}


def test_read_envelope_versions_must_be_integers():
    with pytest.raises(ValidationError):
        GatewayReadEnvelope.model_validate(
            {
                "data": {},
                "source_ref": "src_1",
                "versions": {"cus_a": "3"},
                "observed_at": "2026-10-24T00:00:00Z",
            }
        )


def test_decision_full_wire_and_plan_binding_required():
    parsed = GatewayDecision.model_validate(decision())
    assert parsed.plan_id == "pln_a"
    assert parsed.evaluation_id == "evl_a"
    assert parsed.revision == 1
    assert parsed.decision == "REQUIRE_APPROVAL"
    assert parsed.risk == "YELLOW"
    assert parsed.approval_id == "apv_a"
    assert parsed.preflight.scheduled_reserve_cents == 4500
    assert parsed.versions == {"cus_zs": 1}


def test_decision_missing_plan_binding_fails():
    with pytest.raises(ValidationError):
        GatewayDecision.model_validate(decision(plan_id=None))


def test_decision_unknown_decision_value_fails():
    with pytest.raises(ValidationError):
        GatewayDecision.model_validate(decision(decision="MAYBE"))


def test_decision_unknown_risk_fails():
    with pytest.raises(ValidationError):
        GatewayDecision.model_validate(decision(risk="ORANGE"))


def test_decision_float_reserve_cents_fails():
    with pytest.raises(ValidationError):
        GatewayDecision.model_validate(
            decision(preflight={"executable": True, "scheduled_reserve_cents": 4500.5})
        )


def test_decision_float_balance_cents_fails():
    preflight = {
        "executable": True,
        "steps": [],
        "balances": [
            {
                "account_id": "acc_a",
                "balance_before_cents": 1000.5,
                "balance_after_cents": 0,
                "available_before_cents": 0,
                "available_after_cents": 0,
                "daily_used_before_cents": 0,
                "daily_used_after_cents": 0,
            }
        ],
        "holdings": [],
        "scheduled_reserve_cents": 0,
    }
    with pytest.raises(ValidationError):
        GatewayDecision.model_validate(decision(preflight=preflight))


def test_decision_replan_finding_requires_resolver():
    finding = {"code": "ENTITY_UNSEEN", "effect": "REPLAN", "resolver": None}
    with pytest.raises(ValidationError):
        GatewayDecision.model_validate(decision(findings=[finding]))


def test_decision_non_replan_finding_rejects_resolver():
    finding = {"code": "AMOUNT_ABOVE_YELLOW_CAP", "effect": "RISK_YELLOW", "resolver": "agent"}
    with pytest.raises(ValidationError):
        GatewayDecision.model_validate(decision(findings=[finding]))


def test_decision_shadow_b1_is_recursive_gateway_decision():
    with pytest.raises(ValidationError):
        GatewayDecision.model_validate(decision(shadow_b1={"decision": "MAYBE", "risk": "ORANGE"}))


def test_utterance_kind_is_open_string():
    parsed = GatewayUtterance.model_validate(utterance("utt_a", kind="future_kind"))
    assert parsed.kind == "future_kind"


def test_utterance_parse_status_is_closed():
    with pytest.raises(ValidationError):
        GatewayUtterance.model_validate(utterance("utt_a", parse_status="GARBAGE"))


def test_utterance_scope_transfer_amount_must_be_cents():
    scope = {
        "action_kinds": ["TRANSFER_EXTERNAL"],
        "from_account_id": "acc_a",
        "transfers": [{"target_fingerprint": "fp_1", "amount_cents": 1.5}],
    }
    with pytest.raises(ValidationError):
        GatewayUtterance.model_validate(utterance("utt_a", scope=scope))


def test_utterance_scope_transfer_roundtrips_legit_fields():
    scope = {
        "action_kinds": ["TRANSFER_EXTERNAL"],
        "from_account_id": "acc_a",
        "transfers": [
            {"target_fingerprint": "fp_1", "amount_cents": 80000, "from_account_id": "acc_a"}
        ],
        "period": "ONCE",
        "channel": "app",
        "fields": {"alias": "王五"},
    }
    parsed = GatewayUtterance.model_validate(utterance("utt_a", scope=scope))
    assert parsed.scope.transfers[0].amount_cents == 80000
    assert parsed.scope.transfers[0].from_account_id == "acc_a"
    assert parsed.scope.fields == {"alias": "王五"}


def test_clarification_kind_is_closed():
    with pytest.raises(ValidationError):
        GatewayClarification.model_validate(
            {
                "id": "clr_1",
                "kind": "GARBAGE",
                "parent_utterance_ids": ["utt_a"],
                "required_fields": ["payee_id"],
                "partial_scope": {"action_kinds": []},
                "candidates": [],
                "expires_at_wall": "2026-10-24T00:10:00Z",
            }
        )


def test_finding_code_is_closed():
    with pytest.raises(ValidationError):
        GatewayDecision.model_validate(
            decision(findings=[{"code": "NOT_A_REAL_CODE", "effect": "INFO"}])
        )


def test_clarification_null_shape_is_rejected_but_handled_by_client():
    # The G21 wire value is ``{clarification: {...}|null}``; the model only validates the
    # non-null member.  The client turns the null member into ``None`` (see client tests).
    assert (
        GatewayClarification.model_validate(
            {
                "id": "clr_1",
                "kind": "SLOTS",
                "parent_utterance_ids": ["utt_a"],
                "required_fields": ["payee_id"],
                "prompt_template_id": "select_missing_slot",
                "partial_scope": {"action_kinds": []},
                "candidates": [
                    {
                        "field": "payee_id",
                        "entity_id": "pye_b",
                        "label": "王五",
                        "source_ref": "src_1",
                    }
                ],
                "expires_at_wall": "2026-10-24T00:10:00Z",
            }
        ).id
        == "clr_1"
    )


def test_unknown_execution_shape():
    parsed = UnknownExecution.model_validate({"plan_id": "pln_a", "status": "UNKNOWN"})
    assert parsed.plan_id == "pln_a"
    with pytest.raises(ValidationError):
        UnknownExecution.model_validate({"plan_id": "pln_a", "status": "EXECUTED"})
