"""Identify event runs by business source and instant, not recurring payload hash."""

from alembic import op

revision = "0010_event_identity"
down_revision = "0009_funds_units_nonneg"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing duplicate runs fail migration instead of silently dropping history.
    op.drop_constraint("event_jobs_payload_hash_key", "event_jobs", type_="unique")
    op.create_unique_constraint(
        "event_jobs_source_instant_key", "event_jobs", ["kind", "source_id", "due_at"]
    )
    op.create_index("event_jobs_payload_hash", "event_jobs", ["payload_hash"])


def downgrade() -> None:
    raise RuntimeError("Financial core migrations are forward-only")
