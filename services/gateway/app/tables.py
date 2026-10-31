"""Gateway schema (PRD §7.2). Migrations own DDL; runtime only DML.

All IDs are strings (ULID / prefixed IDs per PRD §9.1). Status CHECK constraints
are generated from ``app.schemas.enums`` so the wire values and the database
cannot drift. ``schema_v1.json`` remains the frozen historical 0001 rendering;
additive revisions upgrade it to this metadata. Live DB drift tests compare the
complete migrated schema with this model.
"""

from __future__ import annotations

from enum import StrEnum

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

from .schemas.enums import (
    ApprovalStatus,
    ClarificationKind,
    ClarificationStatus,
    ExecutionJobStatus,
    ParseStatus,
    PlanStatus,
    UtteranceStatus,
    WorkflowEventStatus,
)

metadata = MetaData()


def idcol(name: str = "id", **kw) -> Column:
    return Column(name, String(256), **kw)


def timestamp(name: str, **kw) -> Column:
    return Column(name, DateTime(timezone=True), **kw)


def _in(enum: type[StrEnum]) -> str:
    return "','".join(e.value for e in enum)


sessions = Table(
    "sessions",
    metadata,
    idcol(primary_key=True),
    Column("customer_id", String(256), nullable=False),
    Column("authorization_epoch", BigInteger, nullable=False, server_default="0"),
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
    Column("parse_status", String(32), nullable=False, server_default="SCOPED"),
    Column("clarification_id", String(256)),
    timestamp("created_at_wall", nullable=False),
    timestamp("expires_at_wall"),
    Column("consumed_by", String(256)),
    Column("authorization_epoch", BigInteger, nullable=False),
    CheckConstraint("kind IN ('typed','hint','action','context')"),
    CheckConstraint(f"status IN ('{_in(UtteranceStatus)}')"),
    CheckConstraint(f"parse_status IN ('{_in(ParseStatus)}')"),
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
    CheckConstraint(f"kind IN ('{_in(ClarificationKind)}')"),
    CheckConstraint(f"status IN ('{_in(ClarificationStatus)}')"),
)

form_actions = Table(
    "form_actions",
    metadata,
    idcol(primary_key=True),
    Column("sid", ForeignKey("sessions.id"), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("action_kind", String(64)),
    Column("normalized_fields", JSONB, nullable=False),
    Column("parent_utterance_id", String(256)),
    Column("parent_plan_id", String(256)),
    Column("view_hash", String),
    timestamp("created_at_wall", nullable=False),
    timestamp("expires_at_wall"),
    Column("used_by", String(256)),
    CheckConstraint("kind IN ('FORM','CLARIFICATION')"),
)

hints = Table(
    "hints",
    metadata,
    idcol(primary_key=True),
    Column("sid", ForeignKey("sessions.id"), nullable=False),
    Column("scope_json", JSONB, nullable=False),
    Column("label", String(256), nullable=False),
    Column("parent_plan_id", String(256)),
    Column("view_hash", String),
    timestamp("created_at_wall", nullable=False),
    timestamp("expires_at_wall"),
    Column("used_by", String(256)),
)

plans = Table(
    "plans",
    metadata,
    idcol(primary_key=True),
    Column("customer_id", String(256), nullable=False),
    Column("sid", ForeignKey("sessions.id"), nullable=False),
    Column("origin", String(32), nullable=False),
    Column("request_id", String(256), nullable=False),
    Column("request_hash", String(64), nullable=False),
    Column("plan_hash", String(64), nullable=False),
    Column("steps_hash", String(64), nullable=False),
    Column("resolved_steps", JSONB, nullable=False),
    Column("plan_draft", JSONB, nullable=True),
    Column("authorization_epoch", BigInteger, nullable=True),
    Column("utterance_ids", JSONB, nullable=False, server_default="[]"),
    Column("scope_ids", JSONB, nullable=False, server_default="[]"),
    Column("status", String(32), nullable=False),
    timestamp("created_at_wall", nullable=False),
    CheckConstraint(f"status IN ('{_in(PlanStatus)}') AND origin IN ('agent','app_form')"),
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
    Column("equivalent_to", String(256)),
    Column("view_json", JSONB),
    Column("clock_version", BigInteger, nullable=False, server_default="0"),
    timestamp("created_at_wall", nullable=False),
    CheckConstraint("risk IN ('GREEN','YELLOW','RED') AND revision >= 1"),
    UniqueConstraint("plan_id", "revision"),
)

approvals = Table(
    "approvals",
    metadata,
    idcol(primary_key=True),
    Column("plan_id", ForeignKey("plans.id"), nullable=False),
    Column("evaluation_id", ForeignKey("evaluations.id"), nullable=False),
    Column("customer_id", String(256), nullable=False),
    Column("sid", ForeignKey("sessions.id"), nullable=False),
    Column("plan_hash", String(64), nullable=False),
    Column("view_hash", String(64), nullable=False),
    Column("binding_mac", String(128), nullable=False),
    timestamp("expires_at_wall", nullable=False),
    Column("status", String(32), nullable=False),
    Column("risk_ack", Boolean, nullable=False, server_default="false"),
    timestamp("confirmed_at_wall"),
    CheckConstraint(f"status IN ('{_in(ApprovalStatus)}')"),
)

otp_challenges = Table(
    "otp_challenges",
    metadata,
    idcol("approval_id", primary_key=True),
    Column("code_digest", String(128), nullable=False),
    Column("code_ciphertext", Text),
    Column("view_hash", String(64), nullable=False),
    timestamp("expires_at_wall", nullable=False),
    Column("attempt_count", Integer, nullable=False, server_default="0"),
    Column("sent_count", Integer, nullable=False, server_default="0"),
    timestamp("last_sent_at_wall"),
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
    timestamp("created_at_wall", nullable=False),
    timestamp("updated_at_wall", nullable=False),
    timestamp("deadline_wall"),
    Column("authorization_evidence", JSONB),
    timestamp("deadline_cap_wall"),
    ForeignKeyConstraint(["plan_id"], ["plans.id"]),
    CheckConstraint(f"status IN ('{_in(ExecutionJobStatus)}')"),
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
    CheckConstraint(
        "status IN ('IN_PROGRESS','SUCCESS','BLOCKED','FAILED_INSUFFICIENT','SKIPPED_REAPPROVAL')"
    ),
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
    CheckConstraint(f"status IN ('{_in(WorkflowEventStatus)}')"),
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
    Column("plan_id", String(256)),
    timestamp("created_at_wall", nullable=False),
    UniqueConstraint("customer_id", "prev_hash"),
)

# Persistent route-scoped request idempotency (PRD §9.1), used by every
# request_id-bearing gateway endpoint (G3, G6, G7, G8, G10, G13, G14, G15, ...).
request_dedup = Table(
    "request_dedup",
    metadata,
    Column("customer_id", String(256), primary_key=True),
    Column("route", String(256), primary_key=True),
    Column("request_id", String(256), primary_key=True),
    Column("request_hash", String(64), nullable=False),
    Column("status_code", Integer, nullable=False),
    Column("response", JSONB, nullable=False),
    timestamp("created_at_wall", nullable=False),
)

pin_failures = Table(
    "pin_failures",
    metadata,
    Column("customer_id", String(256), primary_key=True),
    Column("failed_count", Integer, nullable=False, server_default="0"),
    timestamp("locked_until_wall"),
    timestamp("last_failed_at_wall"),
)

ip_ratelimit = Table(
    "ip_ratelimit",
    metadata,
    Column("ip", String(64), primary_key=True),
    timestamp("window_started_at_wall", nullable=False),
    Column("count", Integer, nullable=False, server_default="0"),
)

auth_sessions = Table(
    "auth_sessions",
    metadata,
    Column("jti", String(256), primary_key=True),
    Column("customer_id", String(256), nullable=False),
    timestamp("created_at_wall", nullable=False),
    timestamp("expires_at_wall", nullable=False),
    timestamp("revoked_at_wall"),
)


def _index(name: str, table: Table, *cols: Column, **kw) -> Index:
    idx = Index(name, *cols, **kw)
    idx.table = table
    return idx


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
Index("audit_customer_seq", audit_events.c.customer_id, audit_events.c.seq)
Index("audit_plan", audit_events.c.plan_id)
Index("utterances_sid_status", utterances.c.sid, utterances.c.status)
Index("plans_sid", plans.c.sid)
Index("approvals_customer_status", approvals.c.customer_id, approvals.c.status)
Index("read_records_sid", read_records.c.sid)
Index("workflow_customer_time", workflow_events.c.customer_id, workflow_events.c.occurred_at)
Index("notifications_customer_read", notifications.c.customer_id, notifications.c.read_at_wall)
Index("auth_sessions_customer", auth_sessions.c.customer_id, auth_sessions.c.revoked_at_wall)
