"""Products fact table (PRD §8.3): public fund metadata, no holdings/orders.

Schema-only migration — product rows and per-product INTERNAL settlement accounts
are installed by an explicit data seed, not by this migration.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision = "0007_products"
down_revision = "0004_event_jobs"
branch_labels = None
depends_on = None

PRODUCT_TYPE_CHECK = "product_type IN ('MMF','BOND','MIXED','EQUITY')"


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "products",
        sa.Column("id", sa.String(256), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("product_type", sa.String(), nullable=False),
        sa.Column("risk_level", sa.Integer(), nullable=False),
        sa.Column("risk_label", sa.Text(), nullable=False),
        sa.Column("base_nav", sa.Numeric(18, 4), nullable=False),
        sa.Column("base_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirm_days", sa.Integer(), nullable=False),
        sa.Column("settle_days", sa.Integer(), nullable=False),
        sa.Column("min_amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("risk_disclosure", sa.Text(), nullable=False),
        sa.Column("disclosure_version", sa.String(), nullable=False),
        sa.CheckConstraint(PRODUCT_TYPE_CHECK, name="products_product_type_check"),
        sa.CheckConstraint("risk_level BETWEEN 1 AND 5", name="products_risk_level_check"),
        sa.CheckConstraint("base_nav > 0", name="products_base_nav_check"),
        sa.CheckConstraint("confirm_days >= 0", name="products_confirm_days_check"),
        sa.CheckConstraint("settle_days >= 0", name="products_settle_days_check"),
        sa.CheckConstraint("min_amount_cents > 0", name="products_min_amount_cents_check"),
    )

    bind.execute(
        text(
            "CREATE TRIGGER immutable_products BEFORE UPDATE OR DELETE ON "
            "products FOR EACH ROW EXECUTE FUNCTION forbid_mutation()"
        )
    )

    bind.execute(text("REVOKE ALL ON products FROM bank_app, bank_ro"))
    bind.execute(text("GRANT SELECT ON products TO bank_app, bank_ro"))


def downgrade() -> None:
    raise RuntimeError("Financial core migrations are forward-only")
