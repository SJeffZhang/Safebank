"""Gateway schema (PRD §7.2) with role isolation for the audit hash chain.

Runs as a privileged migration role (not ``gateway_app``) so that the runtime
role never owns the tables. ``gateway_app`` receives full DML on mutable tables
but only INSERT / SELECT on ``audit_events`` (append-only), matching the
bank-backend role/bootstrap split.
"""

import json
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0001_gateway"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    bind.execute(text("CREATE SEQUENCE audit_sequence"))
    statements = json.loads((Path(__file__).parents[1] / "schema_v1.json").read_text())
    for statement in statements:
        bind.execute(text(statement))
    bind.execute(text("REVOKE CREATE ON SCHEMA public FROM PUBLIC, gateway_app"))
    bind.execute(text("GRANT USAGE ON SCHEMA public TO gateway_app"))
    bind.execute(
        text("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO gateway_app")
    )
    bind.execute(text("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO gateway_app"))
    # audit_events is append-only for the runtime role (PRD §7.12).
    bind.execute(text("REVOKE UPDATE, DELETE ON audit_events FROM gateway_app"))


def downgrade():
    raise RuntimeError("Gateway migrations are forward-only; never drop audit history")
