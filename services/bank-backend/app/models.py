"""PostgreSQL core schema (PRD §8.3). Migrations own DDL; runtime only DML."""

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
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
