"""S1d wire compatibility; detection and policy aggregation belong to W2–W4."""

import json

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from safebank_contracts.codes import Code
from safebank_contracts.plan import Violation

from services.gateway.app.schemas.decision import Decision, FindingView


def payloads(code):
    finding = {
        "code": code,
        "effect": "RISK_YELLOW",
        "step_id": "s1",
        "message": "备注或别名含敏感内容",
    }
    return (
        (FindingView, finding),
        (Violation, {"code": code, "step_id": "s1", "message": finding["message"], "hard": False}),
        (
            Decision,
            {
                "plan_id": "pln_memo",
                "evaluation_id": "evl_memo",
                "revision": 1,
                "decision": "REQUIRE_APPROVAL",
                "risk": "YELLOW",
                "findings": [finding],
            },
        ),
    )


@pytest.mark.parametrize("model,data", payloads("MEMO_SENSITIVE_CONTENT"))
def test_memo_code_round_trips_existing_wire_models(model, data):
    value = model.model_validate(data)
    encoded = value.model_dump_json()
    assert model.model_validate_json(encoded) == value
    Draft202012Validator(model.model_json_schema()).validate(json.loads(encoded))
    if model is Decision:
        assert value.risk.value == "YELLOW"
        assert value.findings[0].effect.value == "RISK_YELLOW"
        assert value.findings[0].code is Code.MEMO_SENSITIVE_CONTENT
    else:
        assert value.code is Code.MEMO_SENSITIVE_CONTENT


@pytest.mark.parametrize("model,data", payloads("UNREGISTERED_MEMO_CODE"))
def test_unknown_code_stays_rejected(model, data):
    with pytest.raises(ValidationError):
        model.model_validate(data)
    with pytest.raises(ValidationError):
        model.model_validate_json(json.dumps(data))
