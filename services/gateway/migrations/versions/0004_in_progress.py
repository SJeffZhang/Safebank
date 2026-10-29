"""Add IN_PROGRESS status to event_decisions for the stale-claim pattern.

The scheduled-authorize handler (S1) no longer holds an advisory lock on a pooled
connection across L3 HTTP calls.  Instead it writes an IN_PROGRESS claim in a
short locked transaction, releases the connection, performs all L3 I/O without
any held lock, then updates the row to a terminal status in a second short
transaction.
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_in_progress"
down_revision = "0003_plan_draft"
branch_labels = None
depends_on = None


def upgrade():
    # Drop old CHECK, re-add with IN_PROGRESS included.
    op.execute(
        sa.text(
            "ALTER TABLE event_decisions DROP CONSTRAINT IF EXISTS event_decisions_status_check"
        )
    )
    op.create_check_constraint(
        "event_decisions_status_check",
        "event_decisions",
        sa.text(
            "status IN ("
            "'IN_PROGRESS',"
            "'SUCCESS','BLOCKED','FAILED_INSUFFICIENT','SKIPPED_REAPPROVAL'"
            ")"
        ),
    )


def downgrade():
    raise RuntimeError("Gateway migrations are forward-only; never drop audit history")
