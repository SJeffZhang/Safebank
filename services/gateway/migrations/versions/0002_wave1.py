"""Add wave-one persistence without rewriting existing gateway data.

Nullable fields preserve historical 0001 rows; runtime submission helpers reject
legacy jobs whose authorization evidence or immutable cap is absent.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002_wave1"
down_revision = "0001_gateway"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("evaluations", sa.Column("view_json", JSONB(), nullable=True))
    op.add_column("otp_challenges", sa.Column("code_ciphertext", sa.Text(), nullable=True))
    op.add_column("execution_jobs", sa.Column("authorization_evidence", JSONB(), nullable=True))
    op.add_column(
        "execution_jobs", sa.Column("deadline_cap_wall", sa.DateTime(timezone=True), nullable=True)
    )
    op.drop_constraint("execution_jobs_status_check", "execution_jobs", type_="check")
    op.create_check_constraint(
        "execution_jobs_status_check",
        "execution_jobs",
        "status IN ('EXECUTING','FENCING','PAUSED','UNKNOWN','EXECUTED','FAILED','CANCELLED')",
    )


def downgrade():
    raise RuntimeError("Gateway migrations are forward-only; never drop audit history")
