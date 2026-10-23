"""Persist raw request identity separately from resolved business steps."""

from alembic import op
from sqlalchemy import Column, String, text

revision = "0002_identity"
down_revision = "0001_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # P1 has not shipped; refuse to reinterpret an existing execution's digest.
    bind = op.get_bind()
    for table in ("execution_intents", "plan_executions"):
        if bind.scalar(text(f"SELECT count(*) FROM {table}")):
            raise RuntimeError("Identity migration requires empty P1 execution tables")
        op.add_column(table, Column("request_digest", String, nullable=False))


def downgrade() -> None:
    raise RuntimeError("Financial core migrations are forward-only")
