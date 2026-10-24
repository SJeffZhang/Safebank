"""Approval creation, HMAC binding and idempotent supersede (W4).

``create_for_evaluation`` is called by W3's G4 handler inside its own short
transaction after ``append_evaluation``: the caller owns commit/rollback and the
plan status transition; this library never commits, changes plan/job/claim
state, calls L3 or issues HTTP. The HMAC key and server-side profile are wired
explicitly via :func:`configure`; without them the library fails closed with a
503 (never a default/random key, never the service token, never the policy
version in place of the profile).

All authoritative facts are re-read and locked from the database (plan,
evaluation, session, live job) so a self-consistent caller mapping cannot
insert an approval whose evaluation belongs to another plan or whose view
diverges from the persisted decision.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from safebank_bankcore.ids import canonical
from safebank_contracts.codes import Code
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from .. import state
from .. import tables as t
from ..errors import GatewayError
from ..schemas.enums import ApprovalStatus, PlanStatus
from ..utterance.service import new_id
from .planview import content_hash

_APPROVAL_TTL = timedelta(minutes=5)
_VALID_PROFILES = frozenset({"B0", "B1", "B2", "B2_NO_PREFLIGHT", "B2_NO_WORKFLOW", "B2_NO_TAINT"})

# Plan states in which a fresh approval may still be created (§7.10.5: the
# first evaluation is EVALUATED; a re-evaluation may revisit a CONFIRMED plan).
_ELIGIBLE_PLAN_STATUSES = frozenset(
    {PlanStatus.EVALUATED.value, PlanStatus.PENDING_APPROVAL.value, PlanStatus.CONFIRMED.value}
)

_EXISTING_CONFLICT: dict[str, Code] = {
    ApprovalStatus.SUPERSEDED.value: Code.APPROVAL_SUPERSEDED,
    ApprovalStatus.USED.value: Code.APPROVAL_USED,
    ApprovalStatus.EXPIRED.value: Code.APPROVAL_EXPIRED,
    ApprovalStatus.REJECTED.value: Code.APPROVAL_REJECTED,
    ApprovalStatus.LOCKED.value: Code.OTP_LOCKED,
}


@dataclass(frozen=True)
class _ApprovalConfig:
    key: bytes
    profile: str
    wall_now: Callable[[], datetime]


_config: _ApprovalConfig | None = None


def configure(approval_hmac_key: bytes, *, profile: str, wall_now: Callable[[], datetime]) -> None:
    """Wire the explicit approval HMAC key, server profile and wall clock.

    Raises ``ValueError`` for a short key, an invalid profile or a wall clock
    that is not callable / does not return a timezone-aware datetime. The key is
    copied so later mutation of the caller's bytes cannot drift the stored MACs.
    """
    if not isinstance(approval_hmac_key, bytes) or len(approval_hmac_key) < 32:
        raise ValueError("approval HMAC key must be at least 32 bytes")
    if profile not in _VALID_PROFILES:
        raise ValueError(f"invalid gateway profile {profile!r}")
    if not callable(wall_now):
        raise ValueError("wall_now must be callable")
    now = wall_now()
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("wall_now must return a timezone-aware datetime")
    global _config
    _config = _ApprovalConfig(bytes(approval_hmac_key), profile, wall_now)


def _require_config() -> _ApprovalConfig:
    if _config is None:
        raise GatewayError(Code.DEPENDENCY_UNAVAILABLE, 503, "approval HMAC is not configured")
    return _config


@dataclass(frozen=True)
class ApprovalBinding:
    """The exact field set bound by ``binding_mac`` (PRD §7.10.1)."""

    customer_id: str
    sid: str
    plan_id: str
    plan_hash: str
    evaluation_id: str
    versions: Mapping[str, int]
    clock_version: int
    policy_version: str
    profile: str
    view_hash: str
    expires_at_wall: datetime

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value
            for value in (
                self.customer_id,
                self.sid,
                self.plan_id,
                self.plan_hash,
                self.evaluation_id,
                self.policy_version,
                self.profile,
                self.view_hash,
            )
        ):
            raise ValueError("binding string fields must be nonempty")
        if type(self.clock_version) is not int or self.clock_version < 0:
            raise ValueError("binding clock_version must be a nonnegative integer")
        if not isinstance(self.versions, Mapping) or any(
            not isinstance(key, str) or not key or type(value) is not int or value < 0
            for key, value in self.versions.items()
        ):
            raise ValueError("binding versions must map nonempty IDs to nonnegative integers")
        if (
            not isinstance(self.expires_at_wall, datetime)
            or self.expires_at_wall.tzinfo is None
            or self.expires_at_wall.utcoffset() is None
        ):
            raise ValueError("binding expiry must be timezone-aware")


def _binding_payload(binding: ApprovalBinding) -> dict[str, Any]:
    return {
        "customer_id": binding.customer_id,
        "sid": binding.sid,
        "plan_id": binding.plan_id,
        "plan_hash": binding.plan_hash,
        "evaluation_id": binding.evaluation_id,
        "versions": dict(sorted(binding.versions.items())),
        "clock_version": binding.clock_version,
        "policy_version": binding.policy_version,
        "profile": binding.profile,
        "view_hash": binding.view_hash,
        # Normalize to UTC so a TIMESTAMPTZ round-trip in a different session
        # offset yields the identical bytes (§9.1 时间 RFC3339 带偏移).
        "expires_at_wall": binding.expires_at_wall.astimezone(UTC).isoformat(),
    }


def binding_mac(key: bytes, binding: ApprovalBinding) -> str:
    return hmac.new(
        key, canonical(_binding_payload(binding)).encode("utf-8"), hashlib.sha256
    ).hexdigest()


def verify_binding(key: bytes, binding: ApprovalBinding, mac: str) -> bool:
    """Recompute the MAC and compare constant-time; never trust the passed MAC."""
    return hmac.compare_digest(binding_mac(key, binding), mac)


def _expiry(view: Any, now: datetime) -> datetime:
    expires = datetime.fromisoformat(view.expires_at_wall)
    if expires.tzinfo is None or expires.utcoffset() is None:
        raise ValueError("view expiry must be timezone-aware")
    if not now < expires <= now + _APPROVAL_TTL:
        raise ValueError("view expiry exceeds the 5-minute approval window")
    return expires


def _wall(cfg: _ApprovalConfig) -> datetime:
    now = cfg.wall_now()
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("wall clock must return a timezone-aware datetime")
    return now


def _existing_approval_id(
    existing: Mapping[str, Any],
    *,
    cfg: _ApprovalConfig,
    db_plan: Mapping[str, Any],
    db_eval: Mapping[str, Any],
    view_hash: str,
    now: datetime,
) -> str:
    status = existing["status"]
    if status not in (ApprovalStatus.PENDING.value, ApprovalStatus.CONFIRMED.value):
        raise GatewayError(
            _EXISTING_CONFLICT.get(status, Code.STATE_CHANGED),
            409,
            f"existing approval is {status}",
        )
    if existing["expires_at_wall"] <= now:
        raise GatewayError(Code.APPROVAL_EXPIRED, 409, "existing approval has expired")
    if (
        existing["plan_id"] != db_plan["id"]
        or existing["customer_id"] != db_plan["customer_id"]
        or existing["sid"] != db_plan["sid"]
        or existing["plan_hash"] != db_plan["plan_hash"]
        or existing["evaluation_id"] != db_eval["id"]
        or existing["view_hash"] != view_hash
    ):
        raise GatewayError(
            Code.APPROVAL_BINDING_MISMATCH, 409, "existing approval binding fields mismatch"
        )
    binding = ApprovalBinding(
        customer_id=db_plan["customer_id"],
        sid=db_plan["sid"],
        plan_id=db_plan["id"],
        plan_hash=db_plan["plan_hash"],
        evaluation_id=db_eval["id"],
        versions=db_eval["versions"],
        clock_version=db_eval["clock_version"],
        policy_version=db_eval["policy_version"],
        profile=cfg.profile,
        view_hash=existing["view_hash"],
        expires_at_wall=existing["expires_at_wall"],
    )
    if not verify_binding(cfg.key, binding, existing["binding_mac"]):
        raise GatewayError(
            Code.APPROVAL_BINDING_MISMATCH, 409, "existing approval MAC does not verify"
        )
    return existing["id"]


async def _has_claimed(conn: AsyncConnection, plan_id: str) -> bool:
    """True when the plan still holds an unconsumed CLAIMED authorization claim.

    A CLAIMED claim is persistent proof that execution already consumed the
    scope; normal approval creation must not re-approve or supersede it. Only
    CLAIMED blocks — CONSUMED (already archived) does not.
    """
    claimed = await conn.scalar(
        select(t.authorization_claims.c.scope_id)
        .where(
            t.authorization_claims.c.plan_id == plan_id,
            t.authorization_claims.c.status == "CLAIMED",
        )
        .limit(1)
        .with_for_update()
    )
    return claimed is not None


async def _supersede_session(conn: AsyncConnection, *, sid: str, except_evaluation_id: str) -> None:
    """Supersede prior PENDING/CONFIRMED approvals of the same session.

    A candidate whose plan has a live execution job (or the claims it still
    holds) must not be superseded: that would strand a confirmed approval behind
    an ADR-008 barrier, so the whole creation is refused with 409 and nothing is
    changed (PRD §7.10 / ADR-008). Jobs are locked before approvals to keep the
    same ``job -> approval`` order the execution path uses.
    """
    candidates = (
        (
            await conn.execute(
                select(t.approvals.c.id, t.approvals.c.plan_id, t.approvals.c.status)
                .where(
                    t.approvals.c.sid == sid,
                    t.approvals.c.status.in_(
                        [ApprovalStatus.PENDING.value, ApprovalStatus.CONFIRMED.value]
                    ),
                    t.approvals.c.evaluation_id != except_evaluation_id,
                )
                .order_by(t.approvals.c.plan_id, t.approvals.c.id)
            )
        )
        .mappings()
        .all()
    )
    for row in candidates:
        job_status = await conn.scalar(
            select(t.execution_jobs.c.status)
            .where(t.execution_jobs.c.plan_id == row["plan_id"])
            .with_for_update()
        )
        if job_status is not None and job_status in {s.value for s in state.LIVE_JOB_STATUSES}:
            raise GatewayError(
                Code.STATE_CHANGED,
                409,
                "another plan in this session has a live execution job",
            )
        # A persistent CLAIMED claim (even without a job, e.g. legacy/malformed
        # state) is proof execution consumed the scope: never supersede it.
        if await _has_claimed(conn, row["plan_id"]):
            raise GatewayError(
                Code.STATE_CHANGED,
                409,
                "another plan in this session has unconsumed authorization claims",
            )
    for row in candidates:
        await state.transition(
            conn,
            t.approvals,
            id_=row["id"],
            from_statuses=[row["status"]],
            to=ApprovalStatus.SUPERSEDED.value,
        )


async def create_for_evaluation(
    conn: AsyncConnection, *, plan: Any, evaluation: Any, view: Any
) -> str | None:
    """Create a PENDING approval for a REQUIRE_APPROVAL evaluation.

    ``plan`` / ``evaluation`` are mapping rows from the ``plans`` /
    ``evaluations`` tables; ``view`` is the PlanView returned by
    ``planview.build_view``. Returns the new ``approval_id`` (never None for a
    REQUIRE_APPROVAL call) and persists the final ``view_json`` (real approval
    ID / status / expiry) on the evaluation row. ALLOW returns None and creates
    nothing; DENY/REPLAN are caller errors. The caller owns commit/rollback.
    """
    cfg = _require_config()
    plan_id = plan["id"]
    customer_id = plan["customer_id"]
    sid = plan["sid"]
    evaluation_id = evaluation["id"]
    decision = evaluation["decision"]
    decision_value = decision.get("decision")
    if decision_value == "ALLOW":
        return None
    if decision_value != "REQUIRE_APPROVAL":
        raise ValueError("create_for_evaluation requires a REQUIRE_APPROVAL evaluation")

    # View identity and content: a mismatched view or a view whose hash no
    # longer matches its content is a programming error, never a soft skip.
    if (
        view.plan_id != plan_id
        or view.evaluation_id != evaluation_id
        or view.session_id != sid
        or view.origin != plan["origin"]
    ):
        raise ValueError("view does not match the plan/evaluation identity")
    if content_hash(view) != view.view_hash:
        raise ValueError("view_hash does not match the view content")

    now = _wall(cfg)
    expires = _expiry(view, now)

    # Authoritative locked rows. Session is locked first so the order matches
    # W1 (session -> plan -> job -> approval); locking plan first would form an
    # ABBA with a W1 cancel_session that already holds the session row lock.
    session = (
        (await conn.execute(select(t.sessions).where(t.sessions.c.id == sid).with_for_update()))
        .mappings()
        .one_or_none()
    )
    if session is None or session["customer_id"] != customer_id:
        raise GatewayError(Code.OWNERSHIP_MISMATCH, 404)
    if session["revoked_at_wall"] is not None:
        raise GatewayError(Code.AUTHORIZATION_CANCELLED, 409, "session has been revoked")

    db_plan = (
        (await conn.execute(select(t.plans).where(t.plans.c.id == plan_id).with_for_update()))
        .mappings()
        .one_or_none()
    )
    if (
        db_plan is None
        or db_plan["customer_id"] != customer_id
        or db_plan["sid"] != sid
        or db_plan["origin"] != plan["origin"]
        or db_plan["plan_hash"] != plan["plan_hash"]
    ):
        raise GatewayError(Code.OWNERSHIP_MISMATCH, 404)
    if db_plan["status"] not in _ELIGIBLE_PLAN_STATUSES:
        raise GatewayError(Code.STATE_CHANGED, 409, "plan is not approval-eligible")

    db_eval = (
        (
            await conn.execute(
                select(t.evaluations).where(t.evaluations.c.id == evaluation_id).with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if db_eval is None or db_eval["plan_id"] != plan_id:
        raise ValueError("evaluation does not belong to the plan")
    if db_eval["risk"] != view.risk.value:
        raise ValueError("evaluation risk does not match the view")
    if db_eval["decision"].get("decision") != "REQUIRE_APPROVAL":
        raise ValueError("evaluation decision is not REQUIRE_APPROVAL")
    if db_eval["findings"] != list(view.findings):
        raise ValueError("evaluation findings do not match the view")
    if db_eval["preflight"] != view.preflight.model_dump(mode="json"):
        raise ValueError("evaluation preflight does not match the view")
    latest_revision = await conn.scalar(
        select(func.max(t.evaluations.c.revision)).where(t.evaluations.c.plan_id == plan_id)
    )
    if db_eval["revision"] != latest_revision:
        raise GatewayError(Code.STATE_CHANGED, 409, "evaluation is not the latest revision")

    # A live execution job means the plan is past the approval boundary; a new
    # approval must go through the ADR-008 barrier, not this path.
    job_status = await conn.scalar(
        select(t.execution_jobs.c.status)
        .where(t.execution_jobs.c.plan_id == plan_id)
        .with_for_update()
    )
    if job_status is not None and job_status in {s.value for s in state.LIVE_JOB_STATUSES}:
        raise GatewayError(Code.STATE_CHANGED, 409, "a live execution job exists for this plan")
    # An unconsumed CLAIMED claim on this plan is proof the scope was already
    # consumed; normal creation (and idempotent return) must not proceed.
    if await _has_claimed(conn, plan_id):
        raise GatewayError(
            Code.STATE_CHANGED, 409, "unconsumed authorization claims exist for this plan"
        )

    # Idempotency: re-check only after the serializing session/plan locks so two
    # concurrent creators of the same evaluation return the same ID (P1-2).
    existing = (
        (
            await conn.execute(
                select(t.approvals)
                .where(
                    t.approvals.c.evaluation_id == evaluation_id,
                    t.approvals.c.plan_id == plan_id,
                )
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if existing is not None:
        return _existing_approval_id(
            existing,
            cfg=cfg,
            db_plan=db_plan,
            db_eval=db_eval,
            view_hash=view.view_hash,
            now=now,
        )

    await _supersede_session(conn, sid=sid, except_evaluation_id=evaluation_id)

    approval_id = new_id("apv")
    binding = ApprovalBinding(
        customer_id=customer_id,
        sid=sid,
        plan_id=plan_id,
        plan_hash=db_plan["plan_hash"],
        evaluation_id=evaluation_id,
        versions=db_eval["versions"],
        clock_version=db_eval["clock_version"],
        policy_version=db_eval["policy_version"],
        profile=cfg.profile,
        view_hash=view.view_hash,
        expires_at_wall=expires,
    )
    await conn.execute(
        t.approvals.insert().values(
            id=approval_id,
            plan_id=plan_id,
            evaluation_id=evaluation_id,
            customer_id=customer_id,
            sid=sid,
            plan_hash=db_plan["plan_hash"],
            view_hash=view.view_hash,
            binding_mac=binding_mac(cfg.key, binding),
            expires_at_wall=expires,
            status=ApprovalStatus.PENDING.value,
            risk_ack=False,
        )
    )

    # Persist the final view_json with the real approval ID / status / expiry,
    # consistent with the approval row and the evaluation row (never the
    # placeholder ``apv_unassigned``). A 0-row update is a real error: the
    # caller's transaction rolls back, so no half-written approval survives.
    final_view = view.model_copy(
        update={
            "approval_id": approval_id,
            "status": PlanStatus.PENDING_APPROVAL,
            "expires_at_wall": expires.isoformat(),
        }
    )
    updated = await conn.execute(
        t.evaluations.update()
        .where(
            t.evaluations.c.id == evaluation_id,
            t.evaluations.c.plan_id == plan_id,
        )
        .values(view_hash=view.view_hash, view_json=final_view.model_dump(mode="json"))
    )
    if updated.rowcount != 1:
        raise ValueError("evaluation view_json update affected no row")
    return approval_id
