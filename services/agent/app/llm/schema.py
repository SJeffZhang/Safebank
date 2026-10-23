"""Strict tool schemas derived from production contracts, never hand-written.

``report_understanding`` is built from the local ``Understanding`` model (with the
L2-derived ``unclear`` flag removed — the model proposes intents/slots/needs_write only).
Read tools are built from the frozen ``READ_ARGS`` contracts as non-strict OpenAI functions;
their arguments are validated locally by the same contracts before any port is called.
"""

from __future__ import annotations

from copy import deepcopy

from safebank_contracts.schema import inline
from safebank_contracts.tools import READ_ARGS

from services.agent.app.graph.state import Understanding

UNDERSTANDING_TOOL = "report_understanding"


def understanding_tool() -> dict:
    schema = inline(Understanding.model_json_schema())
    schema["properties"].pop("unclear", None)
    if "required" in schema:
        schema["required"] = [key for key in schema["required"] if key != "unclear"]
    return {
        "type": "function",
        "function": {"name": UNDERSTANDING_TOOL, "strict": True, "parameters": schema},
    }


def _read_schema(model) -> dict:
    """Inline ``$defs`` while keeping the production Pydantic semantics.

    Unlike the strict ``inline`` (which forces every property ``required``), a non-strict
    read tool must keep the contract's real ``required`` (non-default fields only),
    ``additionalProperties``, ``default`` and nullable ``anyOf`` so the model sees that e.g.
    ``get_accounts.history_days`` is required while ``list_payees.query`` is optional.
    """

    raw = model.model_json_schema()

    def visit(node):
        if isinstance(node, list):
            return [visit(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return visit(deepcopy(raw["$defs"][node["$ref"].split("/")[-1]]))
        stripped = {
            "$defs",
            "title",
            "discriminator",
            "minLength",
            "maxLength",
            "minItems",
            "maxItems",
        }
        result = {key: visit(value) for key, value in node.items() if key not in stripped}
        if result.get("format") in ("date-time", "date", "time"):
            result.pop("format")
        if "const" in result:
            result["enum"] = [result.pop("const")]
        if "oneOf" in result:
            result["anyOf"] = result.pop("oneOf")
        if "prefixItems" in result:
            result["items"] = result.pop("prefixItems")[0]
        return result

    return visit(raw)


def read_tools() -> list[dict]:
    tools = []
    for name, model in READ_ARGS.items():
        tools.append(
            {
                "type": "function",
                "function": {"name": name, "parameters": _read_schema(model)},
            }
        )
    return tools


def read_tool_names() -> frozenset[str]:
    return frozenset(READ_ARGS)
