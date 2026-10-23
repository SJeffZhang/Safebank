"""Frozen DDL must match the SQLAlchemy table metadata (P0-6 drift guard)."""

import json
from pathlib import Path

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from services.gateway.app import tables

ROOT = Path(__file__).resolve().parents[2]


def _render() -> list[str]:
    statements = [
        str(CreateTable(table).compile(dialect=postgresql.dialect()))
        for table in tables.metadata.sorted_tables
    ]
    indexes = []
    for table in tables.metadata.sorted_tables:
        for index in table.indexes:
            indexes.append(str(CreateIndex(index).compile(dialect=postgresql.dialect())))
    # Table order is topological (FK-safe); Table.indexes is an unordered set,
    # so sort only the index statements for a deterministic comparison.
    return statements + sorted(indexes)


def test_frozen_ddl_matches_metadata():
    frozen = json.loads((ROOT / "services/gateway/migrations/schema_v1.json").read_text())
    assert frozen == _render()
