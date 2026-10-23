"""Strict tool schemas derived from production contracts, never hand-written.

``report_understanding`` is built from the local ``Understanding`` model (with the
L2-derived ``unclear`` flag removed — the model proposes intents/slots/needs_write only).
Read tools are built from the frozen ``READ_ARGS`` contracts as non-strict OpenAI functions;
their arguments are validated locally by the same contracts before any port is called.
"""

from __future__ import annotations

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


def _lenient(schema: dict) -> dict:
    """Inline ``$defs`` but keep fields optional (no strict required/additionalProperties)."""

    def visit(node):
        if isinstance(node, list):
            return [visit(item) for item in node]
        if not isinstance(node, dict):
            return node
        kept = ("required", "additionalProperties")
        result = {key: visit(value) for key, value in node.items() if key not in kept}
        return result

    return visit(schema)


def read_tools() -> list[dict]:
    tools = []
    for name, model in READ_ARGS.items():
        schema = _lenient(inline(model.model_json_schema()))
        tools.append(
            {
                "type": "function",
                "function": {"name": name, "parameters": schema},
            }
        )
    return tools


def read_tool_names() -> frozenset[str]:
    return frozenset(READ_ARGS)
