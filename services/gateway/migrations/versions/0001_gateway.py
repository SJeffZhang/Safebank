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
    # Future sequences must also be usable by the runtime role.
    bind.execute(
        text(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            "GRANT USAGE, SELECT ON SEQUENCES TO gateway_app"
        )
    )
    # audit_events is append-only for the runtime role (PRD §7.12).
    bind.execute(text("REVOKE UPDATE, DELETE ON audit_events FROM gateway_app"))
    # Future wave migrations run as the same migration role; keep gateway_app
    # able to DML new tables without an explicit grant in every revision.
    bind.execute(
        text(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO gateway_app"
        )
    )
    # gateway_admin (created by scripts/gateway_database.py) may only reset:
    # TRUNCATE for fixtures, SELECT to inspect, never UPDATE/DELETE rows.
    bind.execute(text("GRANT SELECT, TRUNCATE ON ALL TABLES IN SCHEMA public TO gateway_admin"))
    bind.execute(
        text(
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            "GRANT SELECT, TRUNCATE ON TABLES TO gateway_admin"
        )
    )


def downgrade():
    raise RuntimeError("Gateway migrations are forward-only; never drop audit history")
