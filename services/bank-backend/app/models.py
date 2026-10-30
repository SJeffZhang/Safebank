"""PostgreSQL core schema (PRD §8.3). Migrations own DDL; runtime only DML."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()


def idcol(name="id", **kw):
    return Column(name, String(256), **kw)


def timestamp(name, **kw):
    return Column(name, DateTime(timezone=True), **kw)


customers = Table(
    "customers",
    metadata,
    idcol(primary_key=True),
    Column("display_name", Text, nullable=False),
    Column("phone", Text, nullable=False),
    Column("risk_level", Integer),
    timestamp("risk_assessed_at"),
    Column("risk_valid_until", String),
)
accounts = Table(
    "accounts",
    metadata,
    idcol(primary_key=True),
    Column("customer_id", ForeignKey("customers.id")),
    Column("type", String, nullable=False),
    Column("display_name", Text, nullable=False),
    Column("account_no", String, nullable=False, unique=True),
    Column("currency", String, nullable=False, server_default="CNY"),
    Column("ledger", String, nullable=False, server_default="CNY"),
    Column("flags", Integer, nullable=False, server_default="1"),
    Column("current_card_id", String),
    Column("single_limit_cents", BigInteger, nullable=False),
    Column("daily_limit_cents", BigInteger, nullable=False),
    *(
        Column(k, BigInteger, nullable=False, server_default="0")
        for k in (
            "debits_posted_cents",
            "credits_posted_cents",
            "debits_pending_cents",
            "credits_pending_cents",
        )
    ),
    CheckConstraint(
        "type IN ('CHECKING','SAVINGS','INTERNAL') AND currency='CNY' AND ledger='CNY'"
    ),
    CheckConstraint(
        "(customer_id IS NULL AND type='INTERNAL') OR (customer_id IS NOT "
        "NULL AND type<>'INTERNAL')"
    ),
    CheckConstraint(
        "debits_posted_cents>=0 AND credits_posted_cents>=0 AND "
        "debits_pending_cents>=0 AND credits_pending_cents>=0"
    ),
    CheckConstraint(
        "customer_id IS NULL OR "
        "debits_posted_cents::numeric+debits_pending_cents <= "
        "credits_posted_cents"
    ),
    CheckConstraint("single_limit_cents>0 AND daily_limit_cents>=single_limit_cents"),
    UniqueConstraint("id", "customer_id", name="accounts_id_customer_id_key"),
)
cards = Table(
    "cards",
    metadata,
    idcol(primary_key=True),
    Column("account_id", ForeignKey("accounts.id"), nullable=False),
    Column("last4", String(4), nullable=False),
    Column("status", String, nullable=False),
    *(
        Column(k, BigInteger, nullable=False)
        for k in (
            "single_limit_cents",
            "daily_limit_cents",
            "max_single_limit_cents",
            "max_daily_limit_cents",
        )
    ),
    Column("controls", JSONB, nullable=False),
    Column("replaces_id", ForeignKey("cards.id"), unique=True),
    timestamp("created_at", nullable=False),
    CheckConstraint("status IN ('ACTIVE','FROZEN','LOST')"),
    CheckConstraint(
        "single_limit_cents>0 AND daily_limit_cents>=single_limit_cents "
        "AND daily_limit_cents<=max_daily_limit_cents AND "
        "single_limit_cents<=max_single_limit_cents"
    ),
)
Index(
    "one_current_card_per_account",
    cards.c.account_id,
    unique=True,
    postgresql_where=cards.c.status != "LOST",
)
payees = Table(
    "payees",
    metadata,
    idcol(primary_key=True),
    Column("customer_id", ForeignKey("customers.id"), nullable=False),
    Column("name", Text, nullable=False),
    Column("alias", Text),
    Column("phone", Text),
    Column("bank_name", Text, nullable=False),
    Column("account_no", String, nullable=False),
    Column("target_account_id", ForeignKey("accounts.id")),
    Column("target_fingerprint", String, nullable=False),
    timestamp("created_at", nullable=False),
    UniqueConstraint("customer_id", "target_fingerprint"),
    UniqueConstraint("id", "customer_id", name="payees_id_customer_id_key"),
)
transfers = Table(
    "transfers",
    metadata,
    idcol(primary_key=True),
    Column("debit_account_id", ForeignKey("accounts.id"), nullable=False),
    Column("credit_account_id", ForeignKey("accounts.id"), nullable=False),
    Column("amount_cents", BigInteger, nullable=False),
    Column("code", String, nullable=False),
    Column("phase", String, nullable=False),
    Column("pending_id", ForeignKey("transfers.id")),
    Column("reverses_id", ForeignKey("transfers.id"), unique=True),
    Column("plan_id", String),
    Column("event_id", String),
    Column("step_id", String, nullable=False),
    Column("leg", String, nullable=False),
    timestamp("occurred_at", nullable=False),
    timestamp("expires_at"),
    CheckConstraint("amount_cents>0 AND debit_account_id<>credit_account_id"),
    CheckConstraint("phase IN ('POSTED','PENDING','POST_PENDING','VOID_PENDING')"),
    CheckConstraint(
        "(phase IN ('POST_PENDING','VOID_PENDING') AND pending_id IS NOT "
        "NULL) OR (phase IN ('POSTED','PENDING') AND pending_id IS NULL)"
    ),
    CheckConstraint("phase<>'PENDING' OR expires_at>occurred_at"),
    UniqueConstraint("id", "debit_account_id", name="transfers_id_debit_account_id_key"),
)
Index(
    "one_pending_resolution",
    transfers.c.pending_id,
    unique=True,
    postgresql_where=transfers.c.pending_id.is_not(None),
)
transactions = Table(
    "transactions",
    metadata,
    idcol(primary_key=True),
    Column("transfer_id", ForeignKey("transfers.id"), nullable=False),
    Column("customer_id", ForeignKey("customers.id"), nullable=False),
    Column("account_id", ForeignKey("accounts.id"), nullable=False),
    Column("direction", String, nullable=False),
    Column("kind", String, nullable=False),
    Column("phase", String, nullable=False),
    Column("status", String, nullable=False),
    Column("amount_cents", BigInteger, nullable=False),
    Column("balance_after_cents", BigInteger, nullable=False),
    Column("available_after_cents", BigInteger, nullable=False),
    Column("counterparty", Text, nullable=False),
    Column("memo", Text),
    Column("category", String, nullable=False),
    Column("merchant_id", String),
    Column("payee_id", String),
    Column("card_id", String),
    Column("channel", String),
    timestamp("occurred_at", nullable=False),
    Column(
        "sequence",
        BigInteger,
        primary_key=False,
        nullable=False,
        server_default=text("nextval('transaction_sequence')"),
    ),
    CheckConstraint(
        "direction IN ('IN','OUT') AND status IN ('POSTED','PENDING','SETTLED','RELEASED')"
    ),
    CheckConstraint("amount_cents>0 AND available_after_cents>=0 AND balance_after_cents>=0"),
    UniqueConstraint("transfer_id", "account_id", "phase"),
)
Index(
    "transactions_customer_time",
    transactions.c.customer_id,
    transactions.c.occurred_at,
    transactions.c.id,
)
clock = Table(
    "clock",
    metadata,
    Column("id", Integer, primary_key=True),
    timestamp("sim_now", nullable=False),
    Column("version", BigInteger, nullable=False),
    Column("dataset_revision", String, nullable=False),
    CheckConstraint("id=1 AND version>=0"),
)
state_versions = Table(
    "state_versions",
    metadata,
    Column("customer_id", ForeignKey("customers.id"), primary_key=True),
    Column("version", BigInteger, nullable=False),
    CheckConstraint("version>=0"),
)
execution_intents = Table(
    "execution_intents",
    metadata,
    idcol("customer_id", primary_key=True),
    idcol("plan_id", primary_key=True),
    Column("attempt", Integer, nullable=False),
    Column("plan_hash", String, nullable=False),
    Column("steps_digest", String, nullable=False),
    Column("request_digest", String, nullable=False),
    Column("authorization_evidence", JSONB, nullable=False),
    Column("evidence_history", JSONB, nullable=False),
    Column("status", String, nullable=False),
    timestamp("deadline_wall", nullable=False),
    timestamp("deadline_cap_wall", nullable=False),
    timestamp("created_at_wall", nullable=False),
    CheckConstraint("status IN ('PREPARED','COMMITTED','CANCELLED') AND attempt>=1"),
    CheckConstraint("deadline_wall<=deadline_cap_wall"),
)
plan_executions = Table(
    "plan_executions",
    metadata,
    idcol("customer_id", primary_key=True),
    idcol("plan_id", primary_key=True),
    Column("plan_hash", String, nullable=False),
    Column("steps_digest", String, nullable=False),
    Column("request_digest", String, nullable=False),
    Column("authorization_evidence", JSONB, nullable=False),
    Column("result_json", JSONB, nullable=False),
    timestamp("committed_at", nullable=False),
)
operation_events = Table(
    "operation_events",
    metadata,
    idcol("event_id", primary_key=True),
    Column("customer_id", ForeignKey("customers.id"), nullable=False),
    Column("plan_id", String, nullable=False),
    Column("step_id", String, nullable=False),
    Column("action_kind", String, nullable=False),
    Column("target_fingerprint", String),
    Column("amount_cents", BigInteger, nullable=False),
    timestamp("occurred_at", nullable=False),
    Column(
        "commit_seq",
        BigInteger,
        nullable=False,
        server_default=text("nextval('operation_sequence')"),
    ),
    Column("payload", JSONB, nullable=False),
)
Index("ops_customer_time", operation_events.c.customer_id, operation_events.c.occurred_at)
outbox = Table(
    "outbox",
    metadata,
    Column("event_id", ForeignKey("operation_events.event_id"), primary_key=True),
    Column("payload", JSONB, nullable=False),
    timestamp("delivered_at_wall"),
)

merchants = Table(
    "merchants",
    metadata,
    idcol(primary_key=True),
    Column("name", Text, nullable=False),
    Column("mcc", String, nullable=False),
    Column("category", Text, nullable=False),
    Column("usage_expected", Boolean, nullable=False),
)

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

merchant_billing = Table(
    "merchant_billing",
    metadata,
    idcol(primary_key=True),
    Column("customer_id", ForeignKey("customers.id"), nullable=False),
    Column("account_id", String(256), nullable=False),
    Column(
        "merchant_id",
        ForeignKey("merchants.id", name="merchant_billing_merchant_id_fkey"),
        nullable=False,
    ),
    Column("amount_cents", BigInteger, nullable=False),
    Column("period", String, nullable=False),
    Column("anchor_day", Integer),
    Column("weekday", Integer),
    Column("time_of_day", Time, nullable=False),
    timestamp("next_due_at", nullable=False),
    Column("active", Boolean, nullable=False),
    timestamp("created_at", nullable=False),
    CheckConstraint(
        "amount_cents>0 AND amount_cents<=100000000", name="merchant_billing_amount_cents_check"
    ),
    CheckConstraint(BILLING_PERIOD_CHECK, name="merchant_billing_period_check"),
    CheckConstraint(BILLING_ANCHORS_CHECK, name="merchant_billing_period_anchors_check"),
    CheckConstraint(BILLING_DUE_TIME_CHECK, name="merchant_billing_due_time_check"),
    CheckConstraint(BILLING_DUE_CALENDAR_CHECK, name="merchant_billing_due_calendar_check"),
    ForeignKeyConstraint(
        ["account_id", "customer_id"],
        ["accounts.id", "accounts.customer_id"],
        name="merchant_billing_account_customer_fkey",
    ),
    UniqueConstraint(
        "id", "customer_id", "account_id", name="merchant_billing_id_customer_account_key"
    ),
    UniqueConstraint(
        "id",
        "customer_id",
        "account_id",
        "merchant_id",
        name="merchant_billing_id_customer_account_merchant_key",
    ),
)
Index(
    "merchant_billing_customer_due",
    merchant_billing.c.customer_id,
    merchant_billing.c.next_due_at,
    merchant_billing.c.id,
)

SI_KIND_CHECK = "kind IN ('SCHEDULED_TRANSFER','MERCHANT_MANDATE')"
SI_STATUS_CHECK = "status IN ('ACTIVE','DISABLED','DELETED')"
SI_AMOUNT_TYPE_CHECK = "amount_type IN ('FIXED','DUES')"
SI_RECURRENCE_CHECK = "recurrence IN ('PERIODIC','AS_PER_DUES')"
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
SI_KIND_COMBO_CHECK = SI_SCHEDULED_CHECK + " OR " + SI_MANDATE_CHECK
standing_instructions = Table(
    "standing_instructions",
    metadata,
    idcol(primary_key=True),
    Column(
        "customer_id",
        ForeignKey("customers.id", name="standing_instructions_customer_id_fkey"),
        nullable=False,
    ),
    Column("kind", String, nullable=False),
    Column("amount_type", String, nullable=False),
    Column("status", String, nullable=False),
    Column("from_account_id", String(256), nullable=False),
    Column("payee_id", String(256)),
    Column("to_account_id", String(256)),
    Column(
        "merchant_id", ForeignKey("merchants.id", name="standing_instructions_merchant_id_fkey")
    ),
    Column("billing_id", String(256)),
    Column("amount_cents", BigInteger),
    Column("max_amount_cents", BigInteger),
    Column("recurrence", String, nullable=False),
    Column("period", String),
    Column("anchor_day", Integer),
    Column("weekday", Integer),
    Column("time_of_day", Time),
    timestamp("next_run_at"),
    timestamp("end_at"),
    Column("priority", String, nullable=False),
    Column("grant_scope_hash", String(64), nullable=False),
    Column("source_plan_id", String(256), nullable=False),
    timestamp("created_at", nullable=False),
    CheckConstraint(SI_KIND_CHECK, name="standing_instructions_kind_check"),
    CheckConstraint(SI_AMOUNT_TYPE_CHECK, name="standing_instructions_amount_type_check"),
    CheckConstraint(SI_STATUS_CHECK, name="standing_instructions_status_check"),
    CheckConstraint(SI_RECURRENCE_CHECK, name="standing_instructions_recurrence_check"),
    CheckConstraint(
        "priority IN ('URGENT','HIGH','MEDIUM','LOW')", name="standing_instructions_priority_check"
    ),
    CheckConstraint(
        "grant_scope_hash ~ '^[0-9a-f]{64}$'",
        name="standing_instructions_grant_scope_hash_check",
    ),
    CheckConstraint(
        SI_KIND_COMBO_CHECK,
        name="standing_instructions_kind_combo_check",
    ),
    ForeignKeyConstraint(
        ["from_account_id", "customer_id"],
        ["accounts.id", "accounts.customer_id"],
        name="standing_instructions_from_account_customer_fkey",
    ),
    ForeignKeyConstraint(
        ["payee_id", "customer_id"],
        ["payees.id", "payees.customer_id"],
        name="standing_instructions_payee_customer_fkey",
    ),
    ForeignKeyConstraint(
        ["to_account_id", "customer_id"],
        ["accounts.id", "accounts.customer_id"],
        name="standing_instructions_to_account_customer_fkey",
    ),
    ForeignKeyConstraint(
        ["billing_id", "customer_id", "from_account_id", "merchant_id"],
        [
            "merchant_billing.id",
            "merchant_billing.customer_id",
            "merchant_billing.account_id",
            "merchant_billing.merchant_id",
        ],
        name="standing_instructions_billing_owner_fkey",
    ),
    UniqueConstraint(
        "id",
        "customer_id",
        "from_account_id",
        "kind",
        name="standing_instructions_id_customer_account_kind_key",
    ),
    UniqueConstraint(
        "id",
        "customer_id",
        "from_account_id",
        "billing_id",
        name="standing_instructions_id_customer_account_billing_key",
    ),
)
Index(
    "standing_instructions_customer_status_next",
    standing_instructions.c.customer_id,
    standing_instructions.c.status,
    standing_instructions.c.next_run_at,
    standing_instructions.c.id,
)
Index(
    "one_active_mandate_per_billing",
    standing_instructions.c.billing_id,
    unique=True,
    postgresql_where=(standing_instructions.c.billing_id.isnot(None))
    & (standing_instructions.c.status == "ACTIVE"),
)

RUN_KIND_CHECK = "kind IN ('SCHEDULED_TRANSFER','MERCHANT_MANDATE')"
RUN_STATUS_CHECK = "status IN ('SUCCESS','FAILED_INSUFFICIENT','BLOCKED','SKIPPED_REAPPROVAL')"
RUN_SOURCE_CHECK = (
    "(kind='SCHEDULED_TRANSFER' AND instruction_id IS NOT NULL AND billing_id IS "
    "NULL) OR (kind='MERCHANT_MANDATE' AND billing_id IS NOT NULL)"
)
RUN_SUCCESS_CHECK = (
    "(status='SUCCESS' AND instruction_id IS NOT NULL AND transfer_id IS NOT NULL "
    "AND code IS NULL) OR (status<>'SUCCESS' AND transfer_id IS NULL AND code IS "
    "NOT NULL AND code<>'')"
)

instruction_runs = Table(
    "instruction_runs",
    metadata,
    idcol(primary_key=True),
    Column(
        "customer_id",
        ForeignKey("customers.id", name="instruction_runs_customer_id_fkey"),
        nullable=False,
    ),
    Column("account_id", String(256), nullable=False),
    Column("kind", String, nullable=False),
    Column("instruction_id", String(256)),
    Column("billing_id", String(256)),
    timestamp("due_at", nullable=False),
    Column("event_id", String(256), nullable=False, unique=True),
    Column("status", String, nullable=False),
    Column("code", String),
    Column("amount_cents", BigInteger, nullable=False),
    Column("transfer_id", String(256)),
    CheckConstraint(RUN_KIND_CHECK, name="instruction_runs_kind_check"),
    CheckConstraint(RUN_STATUS_CHECK, name="instruction_runs_status_check"),
    CheckConstraint(
        "amount_cents>0 AND amount_cents<=100000000",
        name="instruction_runs_amount_cents_check",
    ),
    CheckConstraint(RUN_SOURCE_CHECK, name="instruction_runs_source_check"),
    CheckConstraint(RUN_SUCCESS_CHECK, name="instruction_runs_success_check"),
    ForeignKeyConstraint(
        ["account_id", "customer_id"],
        ["accounts.id", "accounts.customer_id"],
        name="instruction_runs_account_customer_fkey",
    ),
    ForeignKeyConstraint(
        ["instruction_id", "customer_id", "account_id", "kind"],
        [
            "standing_instructions.id",
            "standing_instructions.customer_id",
            "standing_instructions.from_account_id",
            "standing_instructions.kind",
        ],
        name="instruction_runs_instruction_owner_fkey",
    ),
    ForeignKeyConstraint(
        ["billing_id", "customer_id", "account_id"],
        [
            "merchant_billing.id",
            "merchant_billing.customer_id",
            "merchant_billing.account_id",
        ],
        name="instruction_runs_billing_owner_fkey",
    ),
    ForeignKeyConstraint(
        ["instruction_id", "customer_id", "account_id", "billing_id"],
        [
            "standing_instructions.id",
            "standing_instructions.customer_id",
            "standing_instructions.from_account_id",
            "standing_instructions.billing_id",
        ],
        name="instruction_runs_instruction_billing_fkey",
    ),
    ForeignKeyConstraint(
        ["transfer_id", "account_id"],
        ["transfers.id", "transfers.debit_account_id"],
        name="instruction_runs_transfer_account_fkey",
    ),
    UniqueConstraint("instruction_id", "due_at", name="instruction_runs_instruction_due_key"),
    UniqueConstraint("billing_id", "due_at", name="instruction_runs_billing_due_key"),
)
Index("instruction_runs_customer_due", instruction_runs.c.customer_id, instruction_runs.c.due_at)
Index(
    "instruction_runs_transfer_unique",
    instruction_runs.c.transfer_id,
    unique=True,
    postgresql_where=instruction_runs.c.transfer_id.isnot(None),
)

EVENT_KIND_CHECK = (
    "kind IN ("
    "'FUND_CONFIRM','FUND_SETTLE','FUND_EXPIRY',"
    "'SCHEDULED_TRANSFER','MERCHANT_CHARGE','REMINDER','DEMO_EVENT'"
    ")"
)
EVENT_STATUS_CHECK = "status IN ('PENDING','RUNNING','DONE','RETRYING')"

event_jobs = Table(
    "event_jobs",
    metadata,
    idcol(primary_key=True),
    Column("kind", String, nullable=False),
    Column("rank", Integer, nullable=False),
    timestamp("due_at", nullable=False),
    Column("immutable_payload", JSONB, nullable=False),
    Column("payload_hash", String, nullable=False),
    Column("source_id", String(256), nullable=False),
    Column("period_key", String, nullable=False),
    Column("status", String, nullable=False),
    timestamp("lease_until_wall"),
    Column("attempt_count", Integer, nullable=False, server_default="0"),
    CheckConstraint(EVENT_KIND_CHECK, name="event_jobs_kind_check"),
    CheckConstraint(EVENT_STATUS_CHECK, name="event_jobs_status_check"),
    CheckConstraint("rank >= 0", name="event_jobs_rank_check"),
    CheckConstraint("attempt_count >= 0", name="event_jobs_attempt_count_check"),
    UniqueConstraint("kind", "source_id", "due_at", name="event_jobs_source_instant_key"),
    UniqueConstraint("source_id", "period_key", name="event_jobs_source_period_key"),
)
Index("event_jobs_payload_hash", event_jobs.c.payload_hash)
Index(
    "event_jobs_pending_due",
    event_jobs.c.status,
    event_jobs.c.due_at,
    event_jobs.c.rank,
    event_jobs.c.id,
    postgresql_where=(event_jobs.c.status == "PENDING"),
)

risk_assessments = Table(
    "risk_assessments",
    metadata,
    idcol(primary_key=True),
    Column(
        "customer_id",
        ForeignKey("customers.id", name="risk_assessments_customer_id_fkey"),
        nullable=False,
    ),
    Column("questionnaire_id", String, nullable=False),
    Column("answers", JSONB, nullable=False),
    Column("score", Integer, nullable=False),
    Column("level", String, nullable=False),
    timestamp("assessed_at", nullable=False),
    timestamp("valid_until", nullable=False),
    Column("plan_id", String(256), nullable=False),
    CheckConstraint("score BETWEEN 6 AND 24", name="risk_assessments_score_check"),
    CheckConstraint("level IN ('R1','R2','R3','R4','R5')", name="risk_assessments_level_check"),
    CheckConstraint("valid_until > assessed_at", name="risk_assessments_valid_until_check"),
    CheckConstraint("questionnaire_id <> ''", name="risk_assessments_questionnaire_id_check"),
    UniqueConstraint("plan_id", name="risk_assessments_plan_id_key"),
)
Index(
    "risk_assessments_customer_time",
    risk_assessments.c.customer_id,
    risk_assessments.c.assessed_at,
    risk_assessments.c.id,
)
Index(
    "risk_assessments_customer_valid",
    risk_assessments.c.customer_id,
    risk_assessments.c.valid_until,
    risk_assessments.c.id,
)

SPLIT_REQUESTS_SELF_CHECK = (
    "(include_self IS TRUE AND self_share_cents>=0) OR "
    "(include_self IS FALSE AND self_share_cents=0)"
)

split_requests = Table(
    "split_requests",
    metadata,
    idcol(primary_key=True),
    Column(
        "customer_id",
        ForeignKey("customers.id", name="split_requests_customer_id_fkey"),
        nullable=False,
    ),
    Column("account_id", String(256), nullable=False),
    Column("total_cents", BigInteger, nullable=False),
    Column("include_self", Boolean, nullable=False),
    Column("self_share_cents", BigInteger, nullable=False),
    timestamp("created_at", nullable=False),
    CheckConstraint(
        "total_cents>0 AND total_cents<=100000000",
        name="split_requests_total_cents_check",
    ),
    CheckConstraint("self_share_cents>=0", name="split_requests_self_share_cents_check"),
    CheckConstraint(SPLIT_REQUESTS_SELF_CHECK, name="split_requests_include_self_check"),
    ForeignKeyConstraint(
        ["account_id", "customer_id"],
        ["accounts.id", "accounts.customer_id"],
        name="split_requests_account_customer_fkey",
    ),
    UniqueConstraint(
        "id", "customer_id", "account_id", name="split_requests_id_customer_account_key"
    ),
)

SPLIT_ITEM_STATUS_CHECK = "status IN ('UNPAID','PAID')"
SPLIT_ITEM_PAID_CHECK = (
    "(status='PAID' AND paid_at IS NOT NULL) OR (status='UNPAID' AND paid_at IS NULL)"
)

split_items = Table(
    "split_items",
    metadata,
    idcol(primary_key=True),
    Column("request_id", String(256), nullable=False),
    Column(
        "customer_id",
        ForeignKey("customers.id", name="split_items_customer_id_fkey"),
        nullable=False,
    ),
    Column("account_id", String(256), nullable=False),
    Column("payee_id", String(256), nullable=False),
    Column("amount_cents", BigInteger, nullable=False),
    Column("status", String, nullable=False),
    Column("paid_at", DateTime(timezone=True)),
    Column("payment_event_id", String(256)),
    CheckConstraint("amount_cents>0", name="split_items_amount_cents_check"),
    CheckConstraint(SPLIT_ITEM_STATUS_CHECK, name="split_items_status_check"),
    CheckConstraint(SPLIT_ITEM_PAID_CHECK, name="split_items_paid_check"),
    ForeignKeyConstraint(
        ["request_id", "customer_id", "account_id"],
        [
            "split_requests.id",
            "split_requests.customer_id",
            "split_requests.account_id",
        ],
        name="split_items_request_owner_fkey",
    ),
    ForeignKeyConstraint(
        ["payee_id", "customer_id"],
        ["payees.id", "payees.customer_id"],
        name="split_items_payee_customer_fkey",
    ),
    UniqueConstraint("request_id", "payee_id", name="split_items_request_payee_key"),
)

PRODUCTS_TYPE_CHECK = "product_type IN ('MMF','BOND','MIXED','EQUITY')"

products = Table(
    "products",
    metadata,
    idcol(primary_key=True),
    Column("name", Text, nullable=False),
    Column("product_type", String, nullable=False),
    Column("risk_level", Integer, nullable=False),
    Column("risk_label", Text, nullable=False),
    Column("base_nav", Numeric(18, 4), nullable=False),
    Column("base_date", DateTime(timezone=True), nullable=False),
    Column("confirm_days", Integer, nullable=False),
    Column("settle_days", Integer, nullable=False),
    Column("min_amount_cents", BigInteger, nullable=False),
    Column("risk_disclosure", Text, nullable=False),
    Column("disclosure_version", String, nullable=False),
    CheckConstraint(PRODUCTS_TYPE_CHECK, name="products_product_type_check"),
    CheckConstraint("risk_level BETWEEN 1 AND 5", name="products_risk_level_check"),
    CheckConstraint("base_nav > 0", name="products_base_nav_check"),
    CheckConstraint("confirm_days >= 0", name="products_confirm_days_check"),
    CheckConstraint("settle_days >= 0", name="products_settle_days_check"),
    CheckConstraint("min_amount_cents > 0", name="products_min_amount_cents_check"),
)


FUND_ORDER_TYPE_CHECK = "type IN ('SUB','RED')"
FUND_ORDER_STATUS_CHECK = (
    "status IN ('PENDING_CONFIRM','PENDING_SETTLEMENT','SETTLED','FAILED','EXPIRED')"
)
EVENT_EXECUTION_STATUS_CHECK = (
    "status IN ('SUCCESS','FAILED_INSUFFICIENT','BLOCKED','SKIPPED_REAPPROVAL')"
)

holdings = Table(
    "holdings",
    metadata,
    Column("customer_id", ForeignKey("customers.id"), nullable=False),
    Column("account_id", String(256), nullable=False),
    Column(
        "product_id",
        ForeignKey("products.id", name="holdings_product_id_fkey"),
        nullable=False,
    ),
    Column("units", Numeric(18, 4), nullable=False),
    Column("reserved_units", Numeric(18, 4), nullable=False),
    Column("cost_cents", BigInteger, nullable=False),
    Column("reserved_cost_cents", BigInteger, nullable=False),
    CheckConstraint("units >= reserved_units AND reserved_units >= 0", name="holdings_units_check"),
    CheckConstraint(
        "cost_cents >= reserved_cost_cents AND reserved_cost_cents >= 0",
        name="holdings_cost_check",
    ),
    ForeignKeyConstraint(
        ["account_id", "customer_id"],
        ["accounts.id", "accounts.customer_id"],
        name="holdings_account_customer_fkey",
    ),
    UniqueConstraint("customer_id", "account_id", "product_id", name="holdings_owner_product_key"),
)

fund_orders = Table(
    "fund_orders",
    metadata,
    idcol(primary_key=True),
    Column("customer_id", ForeignKey("customers.id"), nullable=False),
    Column("account_id", String(256), nullable=False),
    Column(
        "product_id",
        ForeignKey("products.id", name="fund_orders_product_id_fkey"),
        nullable=False,
    ),
    Column("type", String, nullable=False),
    Column("status", String, nullable=False),
    Column("amount_cents", BigInteger, nullable=False),
    Column("units", Numeric(18, 4), nullable=False),
    Column("nav", Numeric(18, 4)),
    Column("reserved_cost_cents", BigInteger, nullable=False),
    Column("pending_transfer_id", String(256)),
    Column("plan_id", String(256), nullable=False),
    Column("step_id", String(256), nullable=False),
    Column("trade_date", DateTime(timezone=True), nullable=False),
    Column("confirm_at", DateTime(timezone=True)),
    Column("settle_at", DateTime(timezone=True)),
    Column("expires_at", DateTime(timezone=True)),
    Column("failure_code", String),
    CheckConstraint(FUND_ORDER_TYPE_CHECK, name="fund_orders_type_check"),
    CheckConstraint(FUND_ORDER_STATUS_CHECK, name="fund_orders_status_check"),
    CheckConstraint("amount_cents > 0", name="fund_orders_amount_cents_check"),
    CheckConstraint("units >= 0", name="fund_orders_units_check"),
    CheckConstraint("reserved_cost_cents >= 0", name="fund_orders_reserved_cost_cents_check"),
    ForeignKeyConstraint(
        ["account_id", "customer_id"],
        ["accounts.id", "accounts.customer_id"],
        name="fund_orders_account_customer_fkey",
    ),
)

event_executions = Table(
    "event_executions",
    metadata,
    Column(
        "event_id",
        ForeignKey("event_jobs.id", name="event_executions_event_id_fkey"),
        primary_key=True,
    ),
    Column("status", String, nullable=False),
    Column("authorization_evidence", JSONB, nullable=False),
    Column("result_json", JSONB, nullable=False),
    Column("committed_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(EVENT_EXECUTION_STATUS_CHECK, name="event_executions_status_check"),
)
