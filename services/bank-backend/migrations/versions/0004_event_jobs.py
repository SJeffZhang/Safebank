"""event_jobs table for scheduled events (standing instructions, fund lifecycle, etc.)

PRD §8.3 – C3 is the sole owner; the same table is reused by fund components
later.  This migration only creates the table; loaders, persist and read/write
dispatch belong to subsequent tasks.

event_executions is not included here – it will be added by a later migration.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision = "0004_event_jobs"
down_revision = "0003_obligations"
branch_labels = None
depends_on = None

EVENT_KIND_CHECK = (
    "kind IN ("
    "'FUND_CONFIRM','FUND_SETTLE','FUND_EXPIRY',"
    "'SCHEDULED_TRANSFER','MERCHANT_CHARGE','REMINDER','DEMO_EVENT'"
    ")"
)
EVENT_STATUS_CHECK = "status IN ('PENDING','RUNNING','DONE','RETRYING')"


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "event_jobs",
        sa.Column("id", sa.String(256), primary_key=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("immutable_payload", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("payload_hash", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(256), nullable=False),
        sa.Column("period_key", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("lease_until_wall", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint(EVENT_KIND_CHECK, name="event_jobs_kind_check"),
        sa.CheckConstraint(EVENT_STATUS_CHECK, name="event_jobs_status_check"),
        sa.CheckConstraint("rank >= 0", name="event_jobs_rank_check"),
        sa.CheckConstraint("attempt_count >= 0", name="event_jobs_attempt_count_check"),
        sa.UniqueConstraint("payload_hash", name="event_jobs_payload_hash_key"),
        sa.UniqueConstraint("source_id", "period_key", name="event_jobs_source_period_key"),
    )
    op.create_index(
        "event_jobs_pending_due",
        "event_jobs",
        ["status", "due_at", "rank", "id"],
        postgresql_where=text("status = 'PENDING'"),
    )

    # Runtime roles – bank_app can insert / update (lease, status, attempts),
    # bank_ro is read-only.
    bind.execute(text("REVOKE ALL ON event_jobs FROM bank_app, bank_ro"))
    bind.execute(text("GRANT SELECT, INSERT, UPDATE ON event_jobs TO bank_app"))
    bind.execute(text("GRANT SELECT ON event_jobs TO bank_ro"))


def downgrade() -> None:
    raise RuntimeError("Financial core migrations are forward-only")
