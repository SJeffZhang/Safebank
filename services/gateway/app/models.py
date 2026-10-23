"""Gateway schema (PRD §7.2). Migrations own DDL; runtime only DML.

All IDs are strings (ULID / prefixed IDs per PRD §9.1), so the gateway database
needs no sequences. Status columns carry CHECK constraints matching the value
sets enumerated in PRD §7.2; fields PRD does not enumerate (for example
``execution_jobs.status``) are left unconstrained and noted in the W0 progress
document.
"""

from __future__ import annotations

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
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()


def idcol(name: str = "id", **kw) -> Column:
    return Column(name, String(256), **kw)


def timestamp(name: str, **kw) -> Column:
    return Column(name, DateTime(timezone=True), **kw)


PLAN_STATUS = (
    "EVALUATED",
    "REPLAN",
    "DENIED",
    "PENDING_APPROVAL",
    "CONFIRMED",
    "EXECUTING",
    "UNKNOWN",
    "EXECUTED",
    "FAILED",
    "REJECTED",
    "EXPIRED",
    "SUPERSEDED",
)
APPROVAL_STATUS = (
    "PENDING",
    "CONFIRMED",
    "REJECTED",
    "EXPIRED",
    "SUPERSEDED",
    "USED",
    "LOCKED",
)


sessions = Table(
    "sessions",
    metadata,
    idcol(primary_key=True),
    Column("customer_id", String(256), nullable=False),
    timestamp("created_at_wall", nullable=False),
    timestamp("revoked_at_wall"),
)

utterances = Table(
    "utterances",
    metadata,
    idcol(primary_key=True),
    Column("sid", ForeignKey("sessions.id"), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("text", Text),
    Column("normalized_scope", JSONB, nullable=False),
    Column("echoes_untrusted", Boolean, nullable=False, server_default="false"),
    Column("source_id", String(256)),
    Column("parent_ids", JSONB, nullable=False, server_default="[]"),
    Column("status", String(32), nullable=False),
    timestamp("created_at_wall", nullable=False),
    timestamp("expires_at_wall"),
    Column("consumed_by", String(256)),
    Column("authorization_epoch", BigInteger, nullable=False),
    CheckConstraint("kind IN ('typed','hint','action','context')"),
    CheckConstraint("status IN ('ACTIVE','SUPERSEDED','CONSUMED','CANCELLED')"),
)

read_records = Table(
    "read_records",
    metadata,
    idcol(primary_key=True),
    Column("sid", ForeignKey("sessions.id"), nullable=False),
    Column("tool", String(64), nullable=False),
    Column("canonical_args", JSONB, nullable=False),
    Column("response", JSONB, nullable=False),
    Column("untrusted_paths", JSONB, nullable=False, server_default="[]"),
    Column("versions", JSONB, nullable=False, server_default="{}"),
    timestamp("observed_at", nullable=False),
    timestamp("created_at_wall", nullable=False),
    Column("raw_text", String(8192)),
)

pending_clarifications = Table(
    "pending_clarifications",
    metadata,
    idcol(primary_key=True),
    Column("sid", ForeignKey("sessions.id"), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("prompt_template_id", String(256)),
    Column("parent_utterance_ids", JSONB, nullable=False, server_default="[]"),
    Column("authorization_epoch", BigInteger, nullable=False),
    Column("required_fields", JSONB, nullable=False, server_default="[]"),
    Column("candidate_refs", JSONB, nullable=False, server_default="[]"),
    Column("partial_scope", JSONB, nullable=False, server_default="{}"),
    Column("status", String(32), nullable=False),
    timestamp("expires_at_wall"),
    CheckConstraint("kind IN ('SLOTS','FORM_REQUIRED')"),
    CheckConstraint("status IN ('OPEN','RESOLVED','CANCELLED')"),
)

form_actions = Table(
    "form_actions",
    metadata,
    idcol(primary_key=True),
    Column("sid", ForeignKey("sessions.id"), nullable=False),
    Column("scope_json", JSONB, nullable=False),
    Column("parent_plan_id", String(256)),
    Column("view_hash", String),
    timestamp("expires_at_wall"),
    Column("used_by", String(256)),
)

hints = Table(
    "hints",
    metadata,
    idcol(primary_key=True),
    Column("sid", ForeignKey("sessions.id"), nullable=False),
    Column("scope_json", JSONB, nullable=False),
    Column("parent_plan_id", String(256)),
    Column("view_hash", String),
    timestamp("expires_at_wall"),
    Column("used_by", String(256)),
)

plans = Table(
    "plans",
    metadata,
    idcol(primary_key=True),
    Column("customer_id", String(256), nullable=False),
    Column("sid", String(256), nullable=False),
    Column("origin", String(32), nullable=False),
    Column("request_id", String(256), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("plan_hash", String(64), nullable=False),
    Column("steps_hash", String(64), nullable=False),
    Column("resolved_steps", JSONB, nullable=False),
    Column("utterance_ids", JSONB, nullable=False, server_default="[]"),
    Column("scope_ids", JSONB, nullable=False, server_default="[]"),
    Column("status", String(32), nullable=False),
    timestamp("created_at_wall", nullable=False),
    CheckConstraint(
        "status IN ('" + "','".join(PLAN_STATUS) + "') AND origin IN ('agent','app_form')"
    ),
    UniqueConstraint("customer_id", "origin", "request_id"),
)

evaluations = Table(
    "evaluations",
    metadata,
    idcol(primary_key=True),
    Column("plan_id", ForeignKey("plans.id"), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("decision", JSONB, nullable=False),
    Column("risk", String(16), nullable=False),
    Column("findings", JSONB, nullable=False, server_default="[]"),
    Column("preflight", JSONB, nullable=False, server_default="{}"),
    Column("versions", JSONB, nullable=False, server_default="{}"),
    Column("policy_version", String(64), nullable=False),
    Column("view_hash", String(64)),
    timestamp("created_at_wall", nullable=False),
    CheckConstraint("risk IN ('GREEN','YELLOW','RED') AND revision >= 1"),
)

approvals = Table(
    "approvals",
    metadata,
    idcol(primary_key=True),
    Column("plan_id", ForeignKey("plans.id"), nullable=False),
    Column("evaluation_id", ForeignKey("evaluations.id"), nullable=False),
    Column("customer_id", String(256), nullable=False),
    Column("sid", String(256), nullable=False),
    Column("plan_hash", String(64), nullable=False),
    Column("view_hash", String(64), nullable=False),
    Column("binding_mac", String(128), nullable=False),
    timestamp("expires_at_wall", nullable=False),
    Column("status", String(32), nullable=False),
    Column("risk_ack", Boolean, nullable=False, server_default="false"),
    timestamp("confirmed_at_wall"),
    CheckConstraint("status IN ('" + "','".join(APPROVAL_STATUS) + "')"),
)

otp_challenges = Table(
    "otp_challenges",
    metadata,
    idcol("approval_id", primary_key=True),
    Column("code_digest", String(128), nullable=False),
    timestamp("expires_at_wall", nullable=False),
    Column("attempt_count", Integer, nullable=False, server_default="0"),
    Column("sent_count", Integer, nullable=False, server_default="0"),
    timestamp("locked_at_wall"),
    ForeignKeyConstraint(["approval_id"], ["approvals.id"]),
)

authorization_claims = Table(
    "authorization_claims",
    metadata,
    idcol("scope_id", primary_key=True),
    Column("plan_id", ForeignKey("plans.id"), nullable=False),
    Column("status", String(32), nullable=False),
    CheckConstraint("status IN ('CLAIMED','CONSUMED')"),
)

execution_jobs = Table(
    "execution_jobs",
    metadata,
    idcol("plan_id", primary_key=True),
    Column("status", String(32), nullable=False),
    Column("l3_attempt", Integer, nullable=False),
    Column("retry_count", Integer, nullable=False, server_default="0"),
    Column("last_error", JSONB),
    Column("result", JSONB),
    ForeignKeyConstraint(["plan_id"], ["plans.id"]),
)

scheduled_grants = Table(
    "scheduled_grants",
    metadata,
    idcol("instruction_id", primary_key=True),
    Column("customer_id", String(256), nullable=False),
    Column("source_plan_id", String(256), nullable=False),
    Column("scope_json", JSONB, nullable=False),
    Column("scope_hash", String(64), nullable=False),
    Column("approved_risk", String(16), nullable=False),
    Column("status", String(32), nullable=False),
    timestamp("created_at", nullable=False),
    timestamp("revoked_at"),
    CheckConstraint("approved_risk IN ('GREEN','YELLOW','RED') AND status IN ('ACTIVE','REVOKED')"),
)

event_decisions = Table(
    "event_decisions",
    metadata,
    idcol("event_id", primary_key=True),
    Column("status", String(32), nullable=False),
    Column("code", String(64)),
    timestamp("decided_at_wall", nullable=False),
    CheckConstraint("status IN ('SUCCESS','BLOCKED','FAILED_INSUFFICIENT','SKIPPED_REAPPROVAL')"),
)

workflow_events = Table(
    "workflow_events",
    metadata,
    idcol("event_id", primary_key=True),
    Column("customer_id", String(256), nullable=False),
    Column("plan_id", String(256)),
    Column("step_id", String(256)),
    Column("action_kind", String(64), nullable=False),
    Column("target_id", String(256)),
    Column("amount_cents", BigInteger, nullable=False),
    Column("status", String(32), nullable=False),
    timestamp("occurred_at", nullable=False),
)

cooldowns = Table(
    "cooldowns",
    metadata,
    Column("customer_id", String(256), primary_key=True),
    Column("payee_fingerprint", String(256), primary_key=True),
    timestamp("until_at", nullable=False),
    Column("reason", String(64), nullable=False),
)

notifications = Table(
    "notifications",
    metadata,
    idcol(primary_key=True),
    Column("customer_id", String(256), nullable=False),
    Column("event_id", String(256), nullable=False),
    Column("type", String(64), nullable=False),
    Column("payload", JSONB, nullable=False),
    timestamp("created_at", nullable=False),
    timestamp("read_at_wall"),
    UniqueConstraint("event_id", "type"),
)

audit_events = Table(
    "audit_events",
    metadata,
    idcol(primary_key=True),
    Column(
        "seq",
        BigInteger,
        nullable=False,
        server_default=text("nextval('audit_sequence')"),
    ),
    Column("customer_id", String(256), nullable=False),
    Column("trace_id", String(256)),
    Column("event_type", String(64), nullable=False),
    Column("payload_digest", String(64), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("prev_hash", String(64), nullable=False),
    Column("hash", String(64), nullable=False),
    timestamp("created_at_wall", nullable=False),
    UniqueConstraint("customer_id", "prev_hash"),
)

Index("audit_customer_seq", audit_events.c.customer_id, audit_events.c.seq)

Index(
    "one_open_clarification_per_session",
    pending_clarifications.c.sid,
    unique=True,
    postgresql_where=pending_clarifications.c.status == "OPEN",
)
Index(
    "one_pending_approval_per_session",
    approvals.c.sid,
    unique=True,
    postgresql_where=approvals.c.status == "PENDING",
)
Index(
    "one_usable_approval_per_plan",
    approvals.c.plan_id,
    unique=True,
    postgresql_where=approvals.c.status.in_(["PENDING", "CONFIRMED"]),
)
Index("evaluations_plan_time", evaluations.c.plan_id, evaluations.c.created_at_wall)
