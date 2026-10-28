"""Split request and item storage (PRD §8.3 C4).

Adds split_requests and split_items tables with ownership-scoped foreign keys,
status/paid-at consistency checks, and runtime grants. Historical 0001/0002/0003
bytes and schema_v1 remain frozen.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision = "0005_splits"
down_revision = "0003_obligations"
branch_labels = None
depends_on = None

SPLIT_ITEM_STATUS_CHECK = "status IN ('UNPAID','PAID')"
SPLIT_ITEM_PAID_CHECK = (
    "(status='PAID' AND paid_at IS NOT NULL) OR (status='UNPAID' AND paid_at IS NULL)"
)


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "split_requests",
        sa.Column("id", sa.String(256), primary_key=True),
        sa.Column(
            "customer_id",
            sa.String(256),
            sa.ForeignKey("customers.id", name="split_requests_customer_id_fkey"),
            nullable=False,
        ),
        sa.Column("account_id", sa.String(256), nullable=False),
        sa.Column("total_cents", sa.BigInteger(), nullable=False),
        sa.Column("include_self", sa.Boolean(), nullable=False),
        sa.Column("self_share_cents", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "total_cents>0 AND total_cents<=100000000",
            name="split_requests_total_cents_check",
        ),
        sa.CheckConstraint("self_share_cents>=0", name="split_requests_self_share_cents_check"),
        sa.CheckConstraint(
            "(include_self IS TRUE AND self_share_cents>=0) OR "
            "(include_self IS FALSE AND self_share_cents=0)",
            name="split_requests_include_self_check",
        ),
        sa.ForeignKeyConstraint(
            ["account_id", "customer_id"],
            ["accounts.id", "accounts.customer_id"],
            name="split_requests_account_customer_fkey",
        ),
        sa.UniqueConstraint(
            "id", "customer_id", "account_id", name="split_requests_id_customer_account_key"
        ),
    )

    op.create_table(
        "split_items",
        sa.Column("id", sa.String(256), primary_key=True),
        sa.Column("request_id", sa.String(256), nullable=False),
        sa.Column(
            "customer_id",
            sa.String(256),
            sa.ForeignKey("customers.id", name="split_items_customer_id_fkey"),
            nullable=False,
        ),
        sa.Column("account_id", sa.String(256), nullable=False),
        sa.Column(
            "payee_id",
            sa.String(256),
            nullable=False,
        ),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payment_event_id", sa.String(256), nullable=True),
        sa.CheckConstraint("amount_cents>0", name="split_items_amount_cents_check"),
        sa.CheckConstraint(SPLIT_ITEM_STATUS_CHECK, name="split_items_status_check"),
        sa.CheckConstraint(SPLIT_ITEM_PAID_CHECK, name="split_items_paid_check"),
        sa.ForeignKeyConstraint(
            ["request_id", "customer_id", "account_id"],
            [
                "split_requests.id",
                "split_requests.customer_id",
                "split_requests.account_id",
            ],
            name="split_items_request_owner_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["payee_id", "customer_id"],
            ["payees.id", "payees.customer_id"],
            name="split_items_payee_customer_fkey",
        ),
        sa.UniqueConstraint("request_id", "payee_id", name="split_items_request_payee_key"),
    )

    # Runtime roles never own the new tables and gain only the DML they need.
    bind.execute(text("REVOKE ALL ON split_requests, split_items FROM bank_app, bank_ro"))
    bind.execute(text("GRANT SELECT, INSERT ON split_requests TO bank_app"))
    bind.execute(text("GRANT SELECT, INSERT, UPDATE ON split_items TO bank_app"))
    bind.execute(text("GRANT SELECT ON split_requests, split_items TO bank_ro"))


def downgrade() -> None:
    raise RuntimeError("Financial core migrations are forward-only")
