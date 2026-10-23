import json

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError
from safebank_contracts.plan import PlanDraft
from safebank_contracts.schema import from_strict_plan, plan_tool, to_strict_plan
from safebank_contracts.tools import READ_ARGS, WRITE_ARGS
from safebank_contracts.views import READ_RESULTS, WRITE_RESULTS

from tests.contracts.samples import READ_GOOD, WRITE_GOOD, step


@pytest.mark.parametrize("tool", list(READ_ARGS))
def test_each_read_input(tool):
    obj = READ_ARGS[tool](**READ_GOOD[tool])
    assert READ_ARGS[tool].model_validate_json(obj.model_dump_json()) == obj
    with pytest.raises(ValidationError):
        READ_ARGS[tool](**(READ_GOOD[tool] | {"unexpected": True}))


@pytest.mark.parametrize("tool", list(WRITE_ARGS))
def test_each_write_input(tool):
    obj = WRITE_ARGS[tool](**WRITE_GOOD[tool])
    assert WRITE_ARGS[tool].model_validate_json(obj.model_dump_json()) == obj
    with pytest.raises(ValidationError):
        WRITE_ARGS[tool](**(WRITE_GOOD[tool] | {"unexpected": True}))


def sample(schema, defs):
    if "$ref" in schema:
        return sample(defs[schema["$ref"].split("/")[-1]], defs)
    if "anyOf" in schema:
        return sample(schema["anyOf"][0], defs)
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "object":
        return {k: sample(schema["properties"][k], defs) for k in schema.get("required", [])}
    if kind == "array":
        return []
    if kind == "null":
        return None
    if kind == "boolean":
        return False
    if kind in ("integer", "number"):
        return schema.get("minimum", 1)
    if schema.get("format") == "date-time":
        return "2026-10-23T12:00:00+08:00"
    if schema.get("format") == "date":
        return "2026-10-23"
    if "pattern" in schema or kind == "string":
        return "1.0000" if "decimal" in schema.get("pattern", "").lower() else "test"
    return "1.0000"


@pytest.mark.parametrize("model", list(READ_RESULTS.values()) + list(WRITE_RESULTS.values()))
def test_each_result_requires_documented_fields(model):
    schema = model.model_json_schema()

    # Exact-decimal view fields use numeric string schema; supply a valid canonical string.
    def fill(node, defs):
        if "$ref" in node:
            return fill(defs[node["$ref"].split("/")[-1]], defs)
        if "anyOf" in node:
            if any(x.get("type") == "null" for x in node["anyOf"]):
                return None
            return fill(node["anyOf"][0], defs)
        if node.get("type") == "object":
            return {k: fill(node["properties"][k], defs) for k in node.get("required", [])}
        if node.get("type") == "string" and "pattern" in node:
            return "1.0000"
        return sample(node, defs)

    data = fill(schema, schema.get("$defs", {}))
    obj = model.model_validate(data)
    assert model.model_validate_json(obj.model_dump_json()) == obj
    with pytest.raises(ValidationError):
        model.model_validate(data | {"unexpected": "value"})


def draft(steps):
    return PlanDraft(
        request_id="req_a",
        session_id="ses_a",
        utterance_ids=["utt_a"],
        intents=["transfer"],
        steps=steps,
        rationale="test",
    )


def test_plan_dag_refs_and_sources():
    first = step("add_payee")
    second = step(step_id="s2", payee_id={"ref": "s1"}) | {"depends_on": ["s1"]}
    draft([first, second])
    invalid = [
        [second, first],
        [first, first],
        [step(payee_id="pye_a", to_account_id="acc_b")],
        [step(amount_cents=1.5)],
        [
            step()
            | {
                "arg_sources": [
                    {
                        "param": "amount_cents",
                        "source": {"kind": "utterance", "ref": "utt_a", "span": [0, 1]},
                    }
                ]
                * 2
            }
        ],
    ]
    for steps in invalid:
        with pytest.raises(ValidationError):
            draft(steps)
    with pytest.raises(ValidationError):
        PlanDraft(**(draft([step()]).model_dump(mode="json") | {"origin": "app_form"}))


def test_production_strict_schema():
    tool = plan_tool()
    schema = tool["function"]["parameters"]
    assert len(schema["properties"]["steps"]["items"]["anyOf"]) == 13
    assert "submit_risk_assessment" not in json.dumps(schema)
    assert "customer_id" not in schema["properties"]
    Draft202012Validator.check_schema(schema)
    data = draft([step()]).model_dump(
        mode="json", exclude={"request_id", "session_id", "utterance_ids"}
    )
    strict = to_strict_plan(data)
    Draft202012Validator(schema).validate(strict)
    assert from_strict_plan(strict) == data


def test_recorded_live_schema_matches_current_production_schema():
    from pathlib import Path

    evidence = Path("docs/acceptance/p1-contracts-live")
    assert json.loads((evidence / "schema.json").read_text()) == plan_tool()
    results = json.loads((evidence / "results.json").read_text())
    assert len(results) == 3 and all(r["http_status"] == 200 and r["valid"] for r in results)
