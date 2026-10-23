import json

import pytest
from jsonschema import Draft202012Validator, ValidationError

from scripts.deepseek_probe import experiments, load_key, obj, plan_schema


def test_all_probe_schemas_are_valid_and_write_catalog_excludes_assessment():
    for _, schema, *_ in experiments():
        Draft202012Validator.check_schema(schema)
    variants = plan_schema()["properties"]["steps"]["items"]["anyOf"]
    assert len(variants) == 13
    assert "submit_risk_assessment" not in json.dumps(variants)
    with pytest.raises(ValidationError):
        Draft202012Validator(obj({"amount_cents": {"type": "integer"}})).validate(
            {"amount_cents": 1.5}
        )


def test_missing_key_fails_without_network(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        load_key()
