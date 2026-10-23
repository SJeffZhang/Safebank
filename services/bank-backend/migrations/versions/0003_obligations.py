"""Obligation storage: merchant bills, standing instructions and run history.

Adds the four obligation tables, parent candidate keys for ownership-scoped
composite foreign keys, the append-only trigger on instruction_runs and the
runtime grants. Historical 0001/0002 bytes and schema_v1 remain frozen.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision = "0003_obligations"
down_revision = "0002_identity"
branch_labels = None
depends_on = None

BILLING_PERIOD_CHECK = "period IN ('WEEKLY','MONTHLY')"
BILLING_ANCHORS_CHECK = (
    "(period='MONTHLY' AND anchor_day IS NOT NULL AND anchor_day BETWEEN 1 AND 31 "
    "AND weekday IS NULL) OR (period='WEEKLY' AND weekday IS NOT NULL "
    "AND weekday BETWEEN 0 AND 6 AND anchor_day IS NULL)"
)
BILLING_DUE_TIME_CHECK = "(next_due_at AT TIME ZONE 'Asia/Shanghai')::time = time_of_day"
BILLING_DUE_CALENDAR_CHECK = (
    "(period='WEEKLY' AND EXTRACT(DOW FROM (next_due_at AT TIME ZONE "
    "'Asia/Shanghai'))::int = ((weekday + 1) % 7)) OR (period='MONTHLY' AND "
    "EXTRACT(DAY FROM (next_due_at AT TIME ZONE 'Asia/Shanghai'))::int = "
    "LEAST(anchor_day, EXTRACT(DAY FROM (date_trunc('month', (next_due_at AT TIME "
    "ZONE 'Asia/Shanghai')) + interval '1 month') - interval '1 day')::int))"
)

SI_SCHEDULED_CHECK = (
    "(kind='SCHEDULED_TRANSFER' AND amount_type='FIXED' AND recurrence='PERIODIC' "
    "AND (payee_id IS NULL) <> (to_account_id IS NULL) AND to_account_id IS "
    "DISTINCT FROM from_account_id AND merchant_id IS NULL AND billing_id IS NULL "
    "AND amount_cents IS NOT NULL AND amount_cents>0 AND amount_cents<=100000000 "
    "AND max_amount_cents IS NULL AND period IN ('ONCE','WEEKLY','MONTHLY') "
    "AND time_of_day IS NOT NULL AND (status='ACTIVE' AND next_run_at IS NOT NULL "
    "OR status<>'ACTIVE') AND ((period='ONCE' AND anchor_day IS NULL AND weekday "
    "IS NULL) OR (period='WEEKLY' AND weekday IS NOT NULL AND weekday BETWEEN 0 "
    "AND 6 AND anchor_day IS NULL) OR (period='MONTHLY' AND anchor_day IS NOT NULL "
    "AND anchor_day BETWEEN 1 AND 31 AND weekday IS NULL)))"
)
SI_MANDATE_CHECK = (
    "(kind='MERCHANT_MANDATE' AND amount_type='DUES' AND recurrence='AS_PER_DUES' "
    "AND payee_id IS NULL AND to_account_id IS NULL AND merchant_id IS NOT NULL "
    "AND billing_id IS NOT NULL AND amount_cents IS NULL AND max_amount_cents IS "
    "NOT NULL AND max_amount_cents>0 AND max_amount_cents<=100000000 AND period "
    "IS NULL AND anchor_day IS NULL AND weekday IS NULL AND time_of_day IS NULL "
    "AND next_run_at IS NULL)"
)

RUN_SOURCE_CHECK = (
    "(kind='SCHEDULED_TRANSFER' AND instruction_id IS NOT NULL AND billing_id IS "
    "NULL) OR (kind='MERCHANT_MANDATE' AND billing_id IS NOT NULL)"
)
RUN_SUCCESS_CHECK = (
    "(status='SUCCESS' AND instruction_id IS NOT NULL AND transfer_id IS NOT NULL "
    "AND code IS NULL) OR (status<>'SUCCESS' AND transfer_id IS NULL AND code IS "
    "NOT NULL AND code<>'')"
)


def upgrade() -> None:
    bind = op.get_bind()

    op.execute(
        text(
            "ALTER TABLE accounts ADD CONSTRAINT accounts_id_customer_id_key "
            "UNIQUE (id, customer_id)"
        )
    )
    op.execute(
        text("ALTER TABLE payees ADD CONSTRAINT payees_id_customer_id_key UNIQUE (id, customer_id)")
    )
    op.execute(
        text(
            "ALTER TABLE transfers ADD CONSTRAINT transfers_id_debit_account_id_key "
            "UNIQUE (id, debit_account_id)"
        )
    )

    op.create_table(
        "merchants",
        sa.Column("id", sa.String(256), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("mcc", sa.String(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("usage_expected", sa.Boolean(), nullable=False),
    )

    op.create_table(
        "merchant_billing",
        sa.Column("id", sa.String(256), primary_key=True),
        sa.Column("customer_id", sa.String(256), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("account_id", sa.String(256), nullable=False),
        sa.Column(
            "merchant_id",
            sa.String(256),
            sa.ForeignKey("merchants.id", name="merchant_billing_merchant_id_fkey"),
            nullable=False,
        ),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("period", sa.String(), nullable=False),
        sa.Column("anchor_day", sa.Integer(), nullable=True),
        sa.Column("weekday", sa.Integer(), nullable=True),
        sa.Column("time_of_day", sa.Time(), nullable=False),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "amount_cents>0 AND amount_cents<=100000000", name="merchant_billing_amount_cents_check"
        ),
        sa.CheckConstraint(BILLING_PERIOD_CHECK, name="merchant_billing_period_check"),
        sa.CheckConstraint(BILLING_ANCHORS_CHECK, name="merchant_billing_period_anchors_check"),
        sa.CheckConstraint(BILLING_DUE_TIME_CHECK, name="merchant_billing_due_time_check"),
        sa.CheckConstraint(BILLING_DUE_CALENDAR_CHECK, name="merchant_billing_due_calendar_check"),
        sa.ForeignKeyConstraint(
            ["account_id", "customer_id"],
            ["accounts.id", "accounts.customer_id"],
            name="merchant_billing_account_customer_fkey",
        ),
        sa.UniqueConstraint(
            "id", "customer_id", "account_id", name="merchant_billing_id_customer_account_key"
        ),
        sa.UniqueConstraint(
            "id",
            "customer_id",
            "account_id",
            "merchant_id",
            name="merchant_billing_id_customer_account_merchant_key",
        ),
    )
    op.create_index(
        "merchant_billing_customer_due",
        "merchant_billing",
        ["customer_id", "next_due_at", "id"],
    )

    op.create_table(
        "standing_instructions",
        sa.Column("id", sa.String(256), primary_key=True),
        sa.Column(
            "customer_id",
            sa.String(256),
            sa.ForeignKey("customers.id", name="standing_instructions_customer_id_fkey"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("amount_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("from_account_id", sa.String(256), nullable=False),
        sa.Column("payee_id", sa.String(256), nullable=True),
        sa.Column("to_account_id", sa.String(256), nullable=True),
        sa.Column(
            "merchant_id",
            sa.String(256),
            sa.ForeignKey("merchants.id", name="standing_instructions_merchant_id_fkey"),
            nullable=True,
        ),
        sa.Column("billing_id", sa.String(256), nullable=True),
        sa.Column("amount_cents", sa.BigInteger(), nullable=True),
        sa.Column("max_amount_cents", sa.BigInteger(), nullable=True),
        sa.Column("recurrence", sa.String(), nullable=False),
        sa.Column("period", sa.String(), nullable=True),
        sa.Column("anchor_day", sa.Integer(), nullable=True),
        sa.Column("weekday", sa.Integer(), nullable=True),
        sa.Column("time_of_day", sa.Time(), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("priority", sa.String(), nullable=False),
        sa.Column("grant_scope_hash", sa.String(64), nullable=False),
        sa.Column("source_plan_id", sa.String(256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('SCHEDULED_TRANSFER','MERCHANT_MANDATE')",
            name="standing_instructions_kind_check",
        ),
        sa.CheckConstraint(
            "amount_type IN ('FIXED','DUES')", name="standing_instructions_amount_type_check"
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE','DISABLED','DELETED')", name="standing_instructions_status_check"
        ),
        sa.CheckConstraint(
            "recurrence IN ('PERIODIC','AS_PER_DUES')",
            name="standing_instructions_recurrence_check",
        ),
        sa.CheckConstraint(
            "priority IN ('URGENT','HIGH','MEDIUM','LOW')",
            name="standing_instructions_priority_check",
        ),
        sa.CheckConstraint(
            "grant_scope_hash ~ '^[0-9a-f]{64}$'",
            name="standing_instructions_grant_scope_hash_check",
        ),
        sa.CheckConstraint(
            SI_SCHEDULED_CHECK + " OR " + SI_MANDATE_CHECK,
            name="standing_instructions_kind_combo_check",
        ),
        sa.ForeignKeyConstraint(
            ["from_account_id", "customer_id"],
            ["accounts.id", "accounts.customer_id"],
            name="standing_instructions_from_account_customer_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["payee_id", "customer_id"],
            ["payees.id", "payees.customer_id"],
            name="standing_instructions_payee_customer_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["to_account_id", "customer_id"],
            ["accounts.id", "accounts.customer_id"],
            name="standing_instructions_to_account_customer_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["billing_id", "customer_id", "from_account_id", "merchant_id"],
            [
                "merchant_billing.id",
                "merchant_billing.customer_id",
                "merchant_billing.account_id",
                "merchant_billing.merchant_id",
            ],
            name="standing_instructions_billing_owner_fkey",
        ),
        sa.UniqueConstraint(
            "id",
            "customer_id",
            "from_account_id",
            "kind",
            name="standing_instructions_id_customer_account_kind_key",
        ),
        sa.UniqueConstraint(
            "id",
            "customer_id",
            "from_account_id",
            "billing_id",
            name="standing_instructions_id_customer_account_billing_key",
        ),
    )
    op.create_index(
        "standing_instructions_customer_status_next",
        "standing_instructions",
        ["customer_id", "status", "next_run_at", "id"],
    )
    op.create_index(
        "one_active_mandate_per_billing",
        "standing_instructions",
        ["billing_id"],
        unique=True,
        postgresql_where=text("billing_id IS NOT NULL AND status = 'ACTIVE'"),
    )

    op.create_table(
        "instruction_runs",
        sa.Column("id", sa.String(256), primary_key=True),
        sa.Column(
            "customer_id",
            sa.String(256),
            sa.ForeignKey("customers.id", name="instruction_runs_customer_id_fkey"),
            nullable=False,
        ),
        sa.Column("account_id", sa.String(256), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("instruction_id", sa.String(256), nullable=True),
        sa.Column("billing_id", sa.String(256), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_id", sa.String(256), nullable=False, unique=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("code", sa.String(), nullable=True),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("transfer_id", sa.String(256), nullable=True),
        sa.CheckConstraint(
            "kind IN ('SCHEDULED_TRANSFER','MERCHANT_MANDATE')", name="instruction_runs_kind_check"
        ),
        sa.CheckConstraint(
            "status IN ('SUCCESS','FAILED_INSUFFICIENT','BLOCKED','SKIPPED_REAPPROVAL')",
            name="instruction_runs_status_check",
        ),
        sa.CheckConstraint(
            "amount_cents>0 AND amount_cents<=100000000",
            name="instruction_runs_amount_cents_check",
        ),
        sa.CheckConstraint(RUN_SOURCE_CHECK, name="instruction_runs_source_check"),
        sa.CheckConstraint(RUN_SUCCESS_CHECK, name="instruction_runs_success_check"),
        sa.ForeignKeyConstraint(
            ["account_id", "customer_id"],
            ["accounts.id", "accounts.customer_id"],
            name="instruction_runs_account_customer_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["instruction_id", "customer_id", "account_id", "kind"],
            [
                "standing_instructions.id",
                "standing_instructions.customer_id",
                "standing_instructions.from_account_id",
                "standing_instructions.kind",
            ],
            name="instruction_runs_instruction_owner_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["billing_id", "customer_id", "account_id"],
            [
                "merchant_billing.id",
                "merchant_billing.customer_id",
                "merchant_billing.account_id",
            ],
            name="instruction_runs_billing_owner_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["instruction_id", "customer_id", "account_id", "billing_id"],
            [
                "standing_instructions.id",
                "standing_instructions.customer_id",
                "standing_instructions.from_account_id",
                "standing_instructions.billing_id",
            ],
            name="instruction_runs_instruction_billing_fkey",
        ),
        sa.ForeignKeyConstraint(
            ["transfer_id", "account_id"],
            ["transfers.id", "transfers.debit_account_id"],
            name="instruction_runs_transfer_account_fkey",
        ),
        sa.UniqueConstraint(
            "instruction_id", "due_at", name="instruction_runs_instruction_due_key"
        ),
        sa.UniqueConstraint("billing_id", "due_at", name="instruction_runs_billing_due_key"),
    )
    op.create_index("instruction_runs_customer_due", "instruction_runs", ["customer_id", "due_at"])
    op.create_index(
        "instruction_runs_transfer_unique",
        "instruction_runs",
        ["transfer_id"],
        unique=True,
        postgresql_where=text("transfer_id IS NOT NULL"),
    )

    bind.execute(
        text(
            "CREATE TRIGGER immutable_instruction_runs BEFORE UPDATE OR DELETE ON "
            "instruction_runs FOR EACH ROW EXECUTE FUNCTION forbid_mutation()"
        )
    )

    # Runtime roles never own or drop the new tables and gain only the DML they need.
    bind.execute(
        text(
            "REVOKE ALL ON merchants, merchant_billing, standing_instructions, "
            "instruction_runs FROM bank_app, bank_ro"
        )
    )
    bind.execute(text("GRANT SELECT ON merchants TO bank_app, bank_ro"))
    bind.execute(text("GRANT SELECT, INSERT, UPDATE ON merchant_billing TO bank_app"))
    bind.execute(text("GRANT SELECT, INSERT, UPDATE ON standing_instructions TO bank_app"))
    bind.execute(text("GRANT SELECT, INSERT ON instruction_runs TO bank_app"))
    bind.execute(
        text("GRANT SELECT ON merchant_billing, standing_instructions, instruction_runs TO bank_ro")
    )


def downgrade() -> None:
    raise RuntimeError("Financial core migrations are forward-only")
