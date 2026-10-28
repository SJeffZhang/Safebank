"""Risk assessment fact table (append-only).

Adds the risk_assessments table with append-only trigger and runtime grants.
Historical 0001/0002/0003 bytes and schema_v1 remain frozen (other domain branches
also fork from 0003; merge coordinator will re-chain).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision = "0006_risk_assessments"
down_revision = "0003_obligations"
branch_labels = None
depends_on = None

RISK_SCORE_CHECK = "score BETWEEN 6 AND 24"
RISK_LEVEL_CHECK = "level IN ('R1','R2','R3','R4','R5')"


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "risk_assessments",
        sa.Column("id", sa.String(256), primary_key=True),
        sa.Column(
            "customer_id",
            sa.String(256),
            sa.ForeignKey("customers.id", name="risk_assessments_customer_id_fkey"),
            nullable=False,
        ),
        sa.Column("questionnaire_id", sa.String(), nullable=False),
        sa.Column("answers", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("level", sa.String(), nullable=False),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("plan_id", sa.String(256), nullable=False),
        sa.CheckConstraint(RISK_SCORE_CHECK, name="risk_assessments_score_check"),
        sa.CheckConstraint(RISK_LEVEL_CHECK, name="risk_assessments_level_check"),
        sa.CheckConstraint("valid_until > assessed_at", name="risk_assessments_valid_until_check"),
        sa.CheckConstraint(
            "questionnaire_id <> ''", name="risk_assessments_questionnaire_id_check"
        ),
        sa.UniqueConstraint("plan_id", name="risk_assessments_plan_id_key"),
    )
    op.create_index(
        "risk_assessments_customer_time",
        "risk_assessments",
        ["customer_id", "assessed_at", "id"],
    )
    op.create_index(
        "risk_assessments_customer_valid",
        "risk_assessments",
        ["customer_id", "valid_until", "id"],
    )

    bind.execute(
        text(
            "CREATE TRIGGER immutable_risk_assessments BEFORE UPDATE OR DELETE "
            "ON risk_assessments FOR EACH ROW EXECUTE FUNCTION forbid_mutation()"
        )
    )

    # Runtime roles never own the new table and gain only the DML they need.
    bind.execute(text("REVOKE ALL ON risk_assessments FROM bank_app, bank_ro"))
    bind.execute(text("GRANT SELECT, INSERT ON risk_assessments TO bank_app"))
    bind.execute(text("GRANT SELECT ON risk_assessments TO bank_ro"))


def downgrade() -> None:
    raise RuntimeError("Financial core migrations are forward-only")
