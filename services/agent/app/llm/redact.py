"""Prompt hygiene: strip ``*_wall`` fields and wrap untrusted values.

Untrusted text is wrapped in a source-tagged marker and escaped so the content cannot close
or open the wrapper tag; this is an assistant-only aid, never an authorization.  JSON
pointers follow RFC 6901 (``~0``/``~1`` unescaping, list indices, compound paths); a pointer
that misses, goes out of bounds, or targets a non-string raises instead of silently skipping.
"""

from __future__ import annotations

import copy
from typing import Any

_OPEN = '<untrusted_data source="{source}">'
_CLOSE = "</untrusted_data>"


def strip_wall(value: Any) -> Any:
    """Recursively remove any mapping key that ends with ``_wall``."""

    if isinstance(value, dict):
        return {k: strip_wall(v) for k, v in value.items() if not k.endswith("_wall")}
    if isinstance(value, list):
        return [strip_wall(v) for v in value]
    return value


def escape_untrusted(text: Any) -> str:
    """Neutralise the wrapper boundary inside untrusted content."""

    return (
        str(text)
        .replace("</untrusted_data", "<\\/untrusted_data")
        .replace("<untrusted_data", "<\\untrusted_data")
    )


def _escape_source(source: str) -> str:
    return (
        str(source)
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def wrap_untrusted(text: Any, source: str) -> str:
    return _OPEN.format(source=_escape_source(source)) + escape_untrusted(text) + _CLOSE


def apply_untrusted_wrapping(data: dict, untrusted_paths: tuple[str, ...], source: str) -> dict:
    """Return a copy of ``data`` with the string value at each JSON pointer wrapped."""

    result = copy.deepcopy(data)
    for pointer in untrusted_paths:
        _wrap_at(result, pointer, source)
    return result


def _tokens(pointer: str) -> list[str]:
    if pointer == "":
        raise ValueError("root JSON pointer cannot wrap a whole document")
    if not pointer.startswith("/"):
        raise ValueError(f"JSON pointer must be empty or start with '/': {pointer!r}")
    return [token.replace("~1", "/").replace("~0", "~") for token in pointer.split("/")[1:]]


def _step(current: Any, token: str, pointer: str) -> Any:
    if isinstance(current, dict):
        if token not in current:
            raise ValueError(f"JSON pointer {pointer!r} key {token!r} not found")
        return current[token]
    if isinstance(current, list):
        if not token.isdigit():
            raise ValueError(f"JSON pointer {pointer!r} segment {token!r} is not a list index")
        index = int(token)
        if not 0 <= index < len(current):
            raise ValueError(f"JSON pointer {pointer!r} index {index} out of range")
        return current[index]
    raise ValueError(f"JSON pointer {pointer!r} traverses a scalar value")


def _wrap_at(node: Any, pointer: str, source: str) -> None:
    tokens = _tokens(pointer)
    if not tokens:
        raise ValueError("JSON pointer must reference at least one segment")
    current = node
    for token in tokens[:-1]:
        current = _step(current, token, pointer)
    leaf = tokens[-1]
    if not isinstance(current, dict) or leaf not in current:
        raise ValueError(f"JSON pointer {pointer!r} not found")
    value = current[leaf]
    if not isinstance(value, str):
        raise ValueError(f"JSON pointer {pointer!r} does not reference a string value")
    current[leaf] = wrap_untrusted(value, source)


def resolve_pointer(data: Any, pointer: str) -> Any:
    """Resolve an RFC 6901 JSON pointer, raising on a missing/out-of-bounds/malformed path."""

    current = data
    for token in _tokens(pointer):
        current = _step(current, token, pointer)
    return current
