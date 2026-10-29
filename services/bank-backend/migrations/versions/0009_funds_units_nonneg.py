"""Relax fund_orders.units to allow zero for PENDING_CONFIRM orders.

The fund lifecycle creates SUB orders in PENDING_CONFIRM state with
``units = 0.0000`` before the confirmation event posts units at NAV, and RED
orders in PENDING_SETTLEMENT with reserved (not yet settled) units.  The
original ``units > 0`` check in 0008 therefore rejects valid lifecycle rows;
0009 relaxes it to ``units >= 0`` (the ``amount_cents > 0`` invariant is kept).
"""

from alembic import op

revision = "0009_funds_units_nonneg"
down_revision = "0008_funds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("fund_orders_units_check", "fund_orders", type_="check")
    op.create_check_constraint("fund_orders_units_check", "fund_orders", "units >= 0")


def downgrade() -> None:
    raise RuntimeError("Financial core migrations are forward-only")
