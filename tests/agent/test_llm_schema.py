"""Schema-level checks for the C1b strict tool generation."""

from __future__ import annotations

from jsonschema import Draft202012Validator
from safebank_contracts.tools import READ_ARGS, WRITE_ARGS

from services.agent.app.graph.state import Understanding
from services.agent.app.llm.schema import read_tools, understanding_tool


def test_understanding_tool_is_strict_and_excludes_unclear():
    tool = understanding_tool()
    assert tool["function"]["name"] == "report_understanding"
    assert tool["function"]["strict"] is True
    schema = tool["function"]["parameters"]
    Draft202012Validator.check_schema(schema)
    assert "unclear" not in schema["properties"]
    assert "unclear" not in schema["required"]
    # slots is an array of objects, not a free-key mapping
    assert schema["properties"]["slots"]["type"] == "array"
    assert schema["properties"]["slots"]["items"]["type"] == "object"
    assert set(schema["required"]) == {"intents", "slots", "needs_write"}


def test_understanding_tool_accepts_prd_shape():
    schema = understanding_tool()["function"]["parameters"]
    sample = {
        "intents": ["transfer"],
        "slots": [
            {"intent_index": 0, "name": "payee", "value": "王五", "span": [3, 5]},
            {"intent_index": 0, "name": "amount_cents", "value": "80000", "span": [6, 9]},
        ],
        "needs_write": True,
    }
    Draft202012Validator(schema).validate(sample)
    Understanding.model_validate(sample)


def test_read_tools_cover_exactly_read_args_and_are_not_strict():
    tools = read_tools()
    names = [tool["function"]["name"] for tool in tools]
    assert len(names) == 18
    assert set(names) == set(READ_ARGS)
    assert not set(names) & set(WRITE_ARGS)
    for tool in tools:
        assert "strict" not in tool["function"]
        Draft202012Validator.check_schema(tool["function"]["parameters"])


def test_read_tools_preserve_required_and_nullable_semantics():
    tools = {tool["function"]["name"]: tool["function"]["parameters"] for tool in read_tools()}
    accounts = tools["get_accounts"]
    assert accounts["required"] == ["history_days"]
    payees = tools["list_payees"]
    assert "query" in payees["required"]  # nullable but no default → required per contract
    assert "limit" not in payees["required"]  # defaulted
    assert "cursor" not in payees["required"]  # defaulted None
    upcoming = tools["upcoming_charges"]
    assert "days" in upcoming["required"]
