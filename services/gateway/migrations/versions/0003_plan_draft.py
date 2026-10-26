"""Add plan_draft column so execute-time replay can reconstruct source provenance.

G8 EXECUTE_REPLAY needs the original PlanDraft (arg_sources, depends_on, …)
to re-run stages ①–④.  Existing rows are left NULL; G8 will return 503 for
any plan whose plan_draft is absent.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003_plan_draft"
down_revision = "0002_wave1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("plans", sa.Column("plan_draft", JSONB(), nullable=True))


def downgrade():
    raise RuntimeError("Gateway migrations are forward-only; never drop audit history")