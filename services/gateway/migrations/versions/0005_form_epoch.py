"""Capture the authorization epoch when a FORM plan is permanently bound.

Historical agent plans remain valid. Historical FORM plans without captured epoch
fail closed; G4 did not support FORM creation before this component.
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_form_epoch"
down_revision = "0004_in_progress"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("plans", sa.Column("authorization_epoch", sa.BigInteger(), nullable=True))


def downgrade():
    raise RuntimeError("Gateway migrations are forward-only; never drop audit history")
