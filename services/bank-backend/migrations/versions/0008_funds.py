"""Funds fact tables (PRD §8.3): holdings, fund_orders, event_executions.

Schema-only migration — no seed rows.  ``holdings`` tracks per-owner units and
cost with reserved sub-ledgers; ``fund_orders`` is the SUB/RED order log whose
lifecycle is driven by the fund components; ``event_executions`` records the
terminal outcome of each fund lifecycle event and is append-only.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision = "0008_funds"
down_revision = "0007_products"
branch_labels = None
depends_on = None

FUND_ORDER_TYPE_CHECK = "type IN ('SUB','RED')"
FUND_ORDER_STATUS_CHECK = (
    "status IN ('PENDING_CONFIRM','PENDING_SETTLEMENT','SETTLED','FAILED','EXPIRED')"
)
EVENT_EXECUTION_STATUS_CHECK = (
    "status IN ('SUCCESS','FAILED_INSUFFICIENT','BLOCKED','SKIPPED_REAPPROVAL')"
)


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "holdings",
        sa.Column("customer_id", sa.String(256), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("account_id", sa.String(256), nullable=False),
        sa.Column(
            "product_id",
            sa.String(256),
            sa.ForeignKey("products.id", name="holdings_product_id_fkey"),
            nullable=False,
        ),
        sa.Column("units", sa.Numeric(18, 4), nullable=False),
        sa.Column("reserved_units", sa.Numeric(18, 4), nullable=False),
        sa.Column("cost_cents", sa.BigInteger(), nullable=False),
        sa.Column("reserved_cost_cents", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "units >= reserved_units AND reserved_units >= 0", name="holdings_units_check"
        ),
        sa.CheckConstraint(
            "cost_cents >= reserved_cost_cents AND reserved_cost_cents >= 0",
            name="holdings_cost_check",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "customer_id"],
            ["accounts.id", "accounts.customer_id"],
            name="holdings_account_customer_fkey",
        ),
        sa.UniqueConstraint(
            "customer_id", "account_id", "product_id", name="holdings_owner_product_key"
        ),
    )

    op.create_table(
        "fund_orders",
        sa.Column("id", sa.String(256), primary_key=True),
        sa.Column("customer_id", sa.String(256), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("account_id", sa.String(256), nullable=False),
        sa.Column(
            "product_id",
            sa.String(256),
            sa.ForeignKey("products.id", name="fund_orders_product_id_fkey"),
            nullable=False,
        ),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("units", sa.Numeric(18, 4), nullable=False),
        sa.Column("nav", sa.Numeric(18, 4), nullable=True),
        sa.Column("reserved_cost_cents", sa.BigInteger(), nullable=False),
        sa.Column("pending_transfer_id", sa.String(256), nullable=True),
        sa.Column("plan_id", sa.String(256), nullable=False),
        sa.Column("step_id", sa.String(256), nullable=False),
        sa.Column("trade_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirm_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settle_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(), nullable=True),
        sa.CheckConstraint(FUND_ORDER_TYPE_CHECK, name="fund_orders_type_check"),
        sa.CheckConstraint(FUND_ORDER_STATUS_CHECK, name="fund_orders_status_check"),
        sa.CheckConstraint("amount_cents > 0", name="fund_orders_amount_cents_check"),
        sa.CheckConstraint("units > 0", name="fund_orders_units_check"),
        sa.CheckConstraint(
            "reserved_cost_cents >= 0", name="fund_orders_reserved_cost_cents_check"
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "customer_id"],
            ["accounts.id", "accounts.customer_id"],
            name="fund_orders_account_customer_fkey",
        ),
    )

    op.create_table(
        "event_executions",
        sa.Column(
            "event_id",
            sa.String(256),
            sa.ForeignKey("event_jobs.id", name="event_executions_event_id_fkey"),
            primary_key=True,
        ),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("authorization_evidence", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("result_json", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("committed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(EVENT_EXECUTION_STATUS_CHECK, name="event_executions_status_check"),
    )

    bind.execute(
        text(
            "CREATE TRIGGER immutable_event_executions BEFORE UPDATE OR DELETE ON "
            "event_executions FOR EACH ROW EXECUTE FUNCTION forbid_mutation()"
        )
    )

    # Runtime grants — holdings/fund_orders are mutable business tables;
    # event_executions is append-only (terminal rows, no UPDATE/DELETE).
    bind.execute(
        text("REVOKE ALL ON holdings, fund_orders, event_executions FROM bank_app, bank_ro")
    )
    bind.execute(text("GRANT SELECT, INSERT, UPDATE ON holdings, fund_orders TO bank_app"))
    bind.execute(text("GRANT SELECT, INSERT ON event_executions TO bank_app"))
    bind.execute(text("GRANT SELECT ON holdings, fund_orders, event_executions TO bank_ro"))


def downgrade() -> None:
    raise RuntimeError("Financial core migrations are forward-only")
