"""Prompt hygiene: strip ``*_wall`` fields and wrap untrusted values.

Untrusted text is wrapped in a source-tagged marker and escaped so the content cannot close
or open the wrapper tag; this is an assistant-only aid, never an authorization.
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


def wrap_untrusted(text: Any, source: str) -> str:
    return _OPEN.format(source=source) + escape_untrusted(text) + _CLOSE


def apply_untrusted_wrapping(data: dict, untrusted_paths: tuple[str, ...], source: str) -> dict:
    """Return a copy of ``data`` with the string values at each JSON pointer wrapped."""

    result = copy.deepcopy(data)
    for pointer in untrusted_paths:
        _wrap_at(result, pointer, source)
    return result


def _wrap_at(node: Any, pointer: str, source: str) -> None:
    parts = [part for part in pointer.split("/") if part]
    if not parts:
        return
    current = node
    for part in parts[:-1]:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit():
            current = current[int(part)]
        else:
            return
    leaf = parts[-1]
    if isinstance(current, dict) and leaf in current and isinstance(current[leaf], str):
        current[leaf] = wrap_untrusted(current[leaf], source)
