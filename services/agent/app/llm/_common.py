"""Shared helpers for the LLM adapters."""

from __future__ import annotations

import json
import uuid
from typing import Any

from langchain_core.messages import AIMessage

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class LlmToolError(Exception):
    """The model produced no usable single tool call for the required tool."""


def new_ulid() -> str:
    """128-bit UUID4 encoded as a 26-char Crockford ULID (matches §9.1 running-ID format)."""

    value = uuid.uuid4().int
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def single_tool_call(message: AIMessage, name: str) -> dict[str, Any]:
    """Return the parsed args of the single, correctly-named tool call."""

    calls = message.tool_calls or []
    if not calls and getattr(message, "invalid_tool_calls", None):
        raise LlmToolError("model returned malformed tool call arguments")
    if len(calls) != 1:
        raise LlmToolError(f"expected exactly one {name!r} call, got {len(calls)}")
    call = calls[0]
    if call.get("name") != name:
        raise LlmToolError(f"expected {name!r} call, got {call.get('name')!r}")
    arguments = call.get("args")
    if isinstance(arguments, str):
        arguments = json.loads(arguments)
    if not isinstance(arguments, dict):
        raise LlmToolError("tool call arguments are not an object")
    return arguments
