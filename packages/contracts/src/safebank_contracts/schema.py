"""Generate strict tool schema from the production Pydantic models, not P0 probes."""

from copy import deepcopy

from .plan import PlanBody


def inline(schema: dict) -> dict:
    definitions = schema.get("$defs", {})

    def visit(node):
        if isinstance(node, list):
            return [visit(x) for x in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return visit(deepcopy(definitions[node["$ref"].split("/")[-1]]))
        result = {
            k: visit(v)
            for k, v in node.items()
            if k
            not in (
                "$defs",
                "title",
                "default",
                "discriminator",
                "minLength",
                "maxLength",
                "minItems",
                "maxItems",
            )
        }
        if result.get("format") in ("date-time", "date", "time"):
            result.pop("format")
        if "const" in result:
            result["enum"] = [result.pop("const")]
        if "oneOf" in result:
            result["anyOf"] = result.pop("oneOf")
        if "prefixItems" in result:
            # Source spans have exactly two homogeneous integers; runtime validates the length.
            choices = result.pop("prefixItems")
            result["items"] = choices[0]
        if result.get("type") == "object":
            result["required"] = list(result.get("properties", {}))
            result["additionalProperties"] = False
        return result

    return visit(schema)


def plan_tool() -> dict:
    schema = inline(PlanBody.model_json_schema())
    steps = schema["properties"]["steps"]["items"]["anyOf"]
    schema["properties"]["steps"]["items"]["anyOf"] = [
        v for v in steps if v["properties"]["tool"]["enum"] != ["submit_risk_assessment"]
    ]

    def entity(allow_ref):
        variants = [
            {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["id"]},
                    "id": {"type": "string"},
                },
                "required": ["kind", "id"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {"kind": {"type": "string", "enum": ["none"]}},
                "required": ["kind"],
                "additionalProperties": False,
            },
        ]
        if allow_ref:
            variants.append(
                {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["ref"]},
                        "ref": {"type": "string"},
                    },
                    "required": ["kind", "ref"],
                    "additionalProperties": False,
                }
            )
        return {"anyOf": variants}

    for variant in schema["properties"]["steps"]["items"]["anyOf"]:
        props = variant["properties"]["args"]["properties"]
        for key in ("payee_id", "to_account_id"):
            if key in props:
                props[key] = entity(key == "payee_id")
        for key in ("memo", "phone", "alias", "end_at"):
            if key in props:
                props[key] = {"type": "string"}
        tool = variant["properties"]["tool"]["enum"][0]
        if tool == "update_card_limits":
            for key in ("single_limit_cents", "daily_limit_cents"):
                props[key] = {"type": "integer", "minimum": -1}
        if tool == "update_card_controls":
            for key in ("online", "overseas", "atm", "quickpass"):
                props[key] = {"type": "string", "enum": ["KEEP", "ON", "OFF"]}

    # LLM cannot manufacture app_form action provenance.
    def strip_actions(node):
        if isinstance(node, dict):
            if "anyOf" in node:
                node["anyOf"] = [
                    v
                    for v in node["anyOf"]
                    if v.get("properties", {}).get("kind", {}).get("enum") != ["action"]
                ]
            for value in node.values():
                strip_actions(value)
        elif isinstance(node, list):
            for value in node:
                strip_actions(value)

    strip_actions(schema)
    return {
        "type": "function",
        "function": {"name": "submit_action_plan", "strict": True, "parameters": schema},
    }


def from_strict_plan(data: dict) -> dict:
    result = deepcopy(data)
    for step in result["steps"]:
        args = step["args"]
        for key in ("payee_id", "to_account_id"):
            if key in args:
                entity = args[key]
                if entity["kind"] == "none":
                    args[key] = None
                elif entity["kind"] == "id":
                    args[key] = entity["id"]
                else:
                    args[key] = {"ref": entity["ref"]}
        for key in ("memo", "phone", "alias", "end_at"):
            if args.get(key) == "__NULL__":
                args[key] = None
        if step["tool"] == "update_card_limits":
            for key in ("single_limit_cents", "daily_limit_cents"):
                if args[key] == -1:
                    args[key] = None
        if step["tool"] == "update_card_controls":
            for key in ("online", "overseas", "atm", "quickpass"):
                args[key] = {"KEEP": None, "ON": True, "OFF": False}[args[key]]
    return result


def to_strict_plan(data: dict) -> dict:
    result = deepcopy(data)
    for step in result["steps"]:
        args = step["args"]
        for key in ("payee_id", "to_account_id"):
            if key in args:
                value = args[key]
                args[key] = (
                    {"kind": "none"}
                    if value is None
                    else (
                        {"kind": "id", "id": value}
                        if isinstance(value, str)
                        else {"kind": "ref", "ref": value["ref"]}
                    )
                )
        for key in ("memo", "phone", "alias", "end_at"):
            if key in args and args[key] is None:
                args[key] = "__NULL__"
        if step["tool"] == "update_card_limits":
            for key in ("single_limit_cents", "daily_limit_cents"):
                if args[key] is None:
                    args[key] = -1
        if step["tool"] == "update_card_controls":
            for key in ("online", "overseas", "atm", "quickpass"):
                args[key] = "KEEP" if args[key] is None else ("ON" if args[key] else "OFF")
    return result
