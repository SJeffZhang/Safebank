"""Approval creation, HMAC binding and idempotent supersede (W4).

``create_for_evaluation`` is called by W3's G4 handler inside its own short
transaction after ``append_evaluation``: the caller owns commit/rollback and the
plan status transition; this library never commits, changes plan/job/claim
state, calls L3 or issues HTTP. The HMAC key and server-side profile are wired
explicitly via :func:`configure`; without them the library fails closed with a
503 (never a default/random key, never the service token, never the policy
version in place of the profile).
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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from .. import state
from .. import tables as t
from ..errors import GatewayError
from ..schemas.enums import ApprovalStatus, PlanStatus
from ..utterance.service import new_id
from .planview import content_hash

_APPROVAL_TTL = timedelta(minutes=5)
_VALID_PROFILES = frozenset({"B0", "B1", "B2", "B2_NO_PREFLIGHT", "B2_NO_WORKFLOW", "B2_NO_TAINT"})


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
            not isinstance(value, int) for value in self.versions.values()
        ):
            raise ValueError("binding versions must map IDs to integers")
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


async def _supersede_session(
    conn: AsyncConnection, *, sid: str, plan_id: str, except_evaluation_id: str
) -> None:
    """Supersede prior PENDING/CONFIRMED approvals of the same session.

    Only unexecuted approvals are superseded (PRD §7.10.2 / §7.10.5). The
    ``state.transition`` guard keeps the shared table rules authoritative and
    forbids touching USED/LOCKED rows or live execution state.
    """
    rows = (
        (
            await conn.execute(
                select(t.approvals.c.id, t.approvals.c.status)
                .where(
                    t.approvals.c.sid == sid,
                    t.approvals.c.status.in_(
                        [ApprovalStatus.PENDING.value, ApprovalStatus.CONFIRMED.value]
                    ),
                    t.approvals.c.evaluation_id != except_evaluation_id,
                )
                .with_for_update()
            )
        )
        .mappings()
        .all()
    )
    for row in rows:
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
    decision = evaluation["decision"]
    decision_value = decision.get("decision")
    if decision_value == "ALLOW":
        return None
    if decision_value != "REQUIRE_APPROVAL":
        raise ValueError("create_for_evaluation requires a REQUIRE_APPROVAL evaluation")

    # Ownership / identity consistency; a mismatched view or a view whose hash
    # no longer matches its content is a programming error, never a soft skip.
    if (
        view.plan_id != plan_id
        or view.evaluation_id != evaluation["id"]
        or view.session_id != sid
        or view.origin != plan["origin"]
        or view.risk.value != evaluation["risk"]
    ):
        raise ValueError("view does not match the plan/evaluation identity")
    if content_hash(view) != view.view_hash:
        raise ValueError("view_hash does not match the view content")

    now = cfg.wall_now()
    expires = _expiry(view, now)

    # Same evaluation never creates a second approval or extends its lifetime.
    existing = (
        (
            await conn.execute(
                select(t.approvals)
                .where(
                    t.approvals.c.evaluation_id == evaluation["id"],
                    t.approvals.c.plan_id == plan_id,
                )
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if existing is not None:
        return existing["id"]

    session = (
        (await conn.execute(select(t.sessions).where(t.sessions.c.id == sid).with_for_update()))
        .mappings()
        .one_or_none()
    )
    if session is None or session["customer_id"] != customer_id:
        raise GatewayError(Code.OWNERSHIP_MISMATCH, 404)
    if session["revoked_at_wall"] is not None:
        raise GatewayError(Code.AUTHORIZATION_CANCELLED, 409, "session has been revoked")

    # A live execution job means the plan is past the approval boundary; a new
    # approval must go through the ADR-008 barrier, not this path. Claims are
    # never released here.
    job_status = await conn.scalar(
        select(t.execution_jobs.c.status)
        .where(t.execution_jobs.c.plan_id == plan_id)
        .with_for_update()
    )
    if job_status is not None and job_status in {s.value for s in state.LIVE_JOB_STATUSES}:
        raise GatewayError(Code.STATE_CHANGED, 409, "a live execution job exists for this plan")

    await _supersede_session(conn, sid=sid, plan_id=plan_id, except_evaluation_id=evaluation["id"])

    approval_id = new_id("apv")
    binding = ApprovalBinding(
        customer_id=customer_id,
        sid=sid,
        plan_id=plan_id,
        plan_hash=plan["plan_hash"],
        evaluation_id=evaluation["id"],
        versions=evaluation["versions"],
        clock_version=evaluation["clock_version"],
        policy_version=evaluation["policy_version"],
        profile=cfg.profile,
        view_hash=view.view_hash,
        expires_at_wall=expires,
    )
    await conn.execute(
        t.approvals.insert().values(
            id=approval_id,
            plan_id=plan_id,
            evaluation_id=evaluation["id"],
            customer_id=customer_id,
            sid=sid,
            plan_hash=plan["plan_hash"],
            view_hash=view.view_hash,
            binding_mac=binding_mac(cfg.key, binding),
            expires_at_wall=expires,
            status=ApprovalStatus.PENDING.value,
            risk_ack=False,
        )
    )

    # Persist the final view_json with the real approval ID / status / expiry,
    # consistent with the approval row and the evaluation row (never the
    # placeholder ``apv_unassigned``).
    final_view = view.model_copy(
        update={
            "approval_id": approval_id,
            "status": PlanStatus.PENDING_APPROVAL,
            "expires_at_wall": expires.isoformat(),
        }
    )
    await conn.execute(
        t.evaluations.update()
        .where(
            t.evaluations.c.id == evaluation["id"],
            t.evaluations.c.plan_id == plan_id,
        )
        .values(view_hash=view.view_hash, view_json=final_view.model_dump(mode="json"))
    )
    return approval_id
