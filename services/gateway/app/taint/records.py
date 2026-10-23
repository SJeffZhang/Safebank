"""Session-scoped read ledger and RFC 6901 pointer helpers."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from ..tables import read_records, sessions


async def load_records(conn, sid: str | None, customer_id: str | None) -> dict[str, dict]:
    if conn is None:
        return {}
    rows = await conn.execute(
        select(read_records)
        .join(sessions, sessions.c.id == read_records.c.sid)
        .where(
            read_records.c.sid == sid,
            sessions.c.customer_id == customer_id,
            sessions.c.revoked_at_wall.is_(None),
        )
    )
    return {row["id"]: dict(row) for row in rows.mappings()}


def pointer(data: Any, path: str) -> Any:
    if path == "":
        return data
    if not path.startswith("/"):
        raise ValueError("JSON Pointer must be absolute")
    value = data
    for part in path[1:].split("/"):
        # Reject malformed escape sequences and non-canonical array indices.
        if "~" in part.replace("~0", "").replace("~1", ""):
            raise ValueError("invalid pointer escape")
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            if not part.isascii() or not part.isdecimal() or str(int(part)) != part:
                raise ValueError("invalid array index")
            value = value[int(part)]
        elif isinstance(value, dict):
            value = value[part]
        else:
            raise ValueError("pointer crosses scalar")
    return value


def paths(data: Any, names: frozenset[str], prefix: str = "") -> tuple[str, ...]:
    found = []
    if isinstance(data, dict):
        for key, value in data.items():
            path = prefix + "/" + key.replace("~", "~0").replace("/", "~1")
            if key in names:
                found.append(path)
            else:
                found.extend(paths(value, names, path))
    elif isinstance(data, (tuple, list)):
        for index, value in enumerate(data):
            found.extend(paths(value, names, prefix + "/" + str(index)))
    return tuple(found)


def contains_id(data: Any, entity_id: str, untrusted: tuple[str, ...], prefix: str = "") -> bool:
    if any(prefix == path or prefix.startswith(path + "/") for path in untrusted):
        return False
    if isinstance(data, dict):
        return any(contains_id(v, entity_id, untrusted, prefix + "/" + k) for k, v in data.items())
    if isinstance(data, list):
        return any(
            contains_id(v, entity_id, untrusted, prefix + "/" + str(i)) for i, v in enumerate(data)
        )
    return isinstance(data, str) and data == entity_id
