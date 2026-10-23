"""Core ledger and execution durability. Revision is append-only after acceptance."""

import json
from pathlib import Path

from alembic import op
from sqlalchemy import text

revision = "0001_core"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    for name in ("transaction_sequence", "operation_sequence"):
        bind.execute(text(f"CREATE SEQUENCE {name}"))
    statements = json.loads((Path(__file__).parents[1] / "schema_v1.json").read_text())
    for statement in statements:
        bind.execute(text(statement))
    bind.execute(
        text("""ALTER TABLE accounts ADD CONSTRAINT accounts_current_card_fk
        FOREIGN KEY(current_card_id) REFERENCES cards(id) DEFERRABLE INITIALLY DEFERRED""")
    )
    bind.execute(
        text("""CREATE FUNCTION forbid_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'append-only table' USING ERRCODE='23514'; END $$""")
    )
    for table in ("transfers", "transactions", "operation_events", "plan_executions"):
        bind.execute(
            text(
                f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON "
                f"{table} FOR EACH ROW EXECUTE FUNCTION forbid_mutation()"
            )
        )
    # Migration/admin identity owns schema; runtime cannot create/drop/truncate or disable triggers.
    bind.execute(text("REVOKE CREATE ON SCHEMA public FROM PUBLIC, bank_app"))
    bind.execute(text("GRANT USAGE ON SCHEMA public TO bank_app,bank_ro"))
    bind.execute(text("GRANT SELECT,INSERT,UPDATE ON ALL TABLES IN SCHEMA public TO bank_app"))
    bind.execute(text("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO bank_app"))
    bind.execute(text("GRANT SELECT ON ALL TABLES IN SCHEMA public TO bank_ro"))


def downgrade():
    raise RuntimeError("Financial core migrations are forward-only; never drop ledger data")
