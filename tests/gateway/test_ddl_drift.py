"""Frozen 0001 plus the explicit 0002 delta must match current metadata.

The database autogeneration test separately compares latest migrated PostgreSQL
with the complete current metadata. Historical migration bytes remain frozen.
"""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

from sqlalchemy import CheckConstraint, MetaData, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from services.gateway.app import tables

ROOT = Path(__file__).resolve().parents[2]


def _render(metadata: MetaData) -> list[str]:
    statements = [
        str(CreateTable(table).compile(dialect=postgresql.dialect()))
        for table in metadata.sorted_tables
    ]
    indexes = []
    for table in metadata.sorted_tables:
        for index in table.indexes:
            indexes.append(str(CreateIndex(index).compile(dialect=postgresql.dialect())))
    # Table order is topological (FK-safe); Table.indexes is an unordered set,
    # so sort only the index statements for a deterministic comparison.
    return statements + sorted(indexes)


def test_frozen_ddl_matches_metadata():
    frozen = json.loads((ROOT / "services/gateway/migrations/schema_v1.json").read_text())
    source = ROOT / "services/gateway/migrations/schema_v1.json"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == (
        "3e78fa43cd1f1efcddc973fa34119a0bceb8d1fcf8ce638b7546df6f1769b100"
    )
    # deepcopy preserves constraint creation order; to_metadata reconstructs
    # unordered constraints and changes only their textual DDL order.
    historical = deepcopy(tables.metadata)
    additions = {
        "evaluations": {"view_json": "JSONB"},
        "otp_challenges": {"code_ciphertext": "TEXT"},
        "execution_jobs": {
            "authorization_evidence": "JSONB",
            "deadline_cap_wall": "TIMESTAMP WITH TIME ZONE",
        },
    }
    for name, columns in additions.items():
        table = historical.tables[name]
        for column, expected_type in columns.items():
            assert table.c[column].nullable is True
            assert str(table.c[column].type.compile(dialect=postgresql.dialect())) == expected_type
            # SQLAlchemy's public table.columns collection is read-only. Mutate
            # only the test-local clone, never the production metadata.
            table._columns.remove(table.c[column])
    job = historical.tables["execution_jobs"]
    checks = [c for c in job.constraints if isinstance(c, CheckConstraint)]
    assert len(checks) == 1
    assert str(checks[0].sqltext) == (
        "status IN ('EXECUTING','FENCING','PAUSED','UNKNOWN','EXECUTED','FAILED','CANCELLED')"
    )
    checks[0].sqltext = text("status IN ('EXECUTING','UNKNOWN','EXECUTED','FAILED','CANCELLED')")
    assert frozen == _render(historical)
