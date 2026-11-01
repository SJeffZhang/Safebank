"""Deny-only grant revocation from a caller-verified L3 committed cancellation.

Missing lazy grants get an explicit REVOKED tombstone, never invented creation
consent. Original grants retain their source scope, risk and first revocation
time. All writes share the execution archive transaction.
"""

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError
from safebank_bankcore.ids import digest
from safebank_contracts.codes import Code
from safebank_contracts.plan import ExecutionResult
from safebank_contracts.views import WRITE_RESULTS
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from . import tables as t
from .errors import GatewayError

_CANCEL = frozenset({"cancel_scheduled", "cancel_subscription"})


async def revoke_committed_grants(
    conn: AsyncConnection, plan: Mapping[str, Any], result: ExecutionResult
) -> None:
    """Only call after terminal identity/result verification, inside its tx.

    An orphan may lack its original draft, but a verified L3 cancellation still
    proves a protective revocation. Available resolved steps must agree exactly.
    Tombstone RED is a fail-closed storage sentinel, not an approval; its marked
    scope can never serve as an ACTIVE grant or reconstruct creation consent.
    """
    if not conn.in_transaction() or result.plan_id != plan["id"]:
        raise GatewayError(Code.STATE_CHANGED, 409)
    resolved = plan.get("resolved_steps") or []
    by_step = {row.get("step_id"): row for row in resolved if isinstance(row, dict)}
    cancelled = {}
    for step in result.steps:
        if step.tool not in _CANCEL:
            continue
        try:
            outcome = WRITE_RESULTS[step.tool].model_validate(step.result)
        except ValidationError as exc:
            raise GatewayError(Code.STATE_CHANGED, 409) from exc
        if step.status != "EXECUTED" or outcome.status != "DELETED":
            raise GatewayError(Code.STATE_CHANGED, 409)
        if resolved:
            original = by_step.get(step.step_id) or {}
            if (
                original.get("tool") != step.tool
                or (original.get("args") or {}).get("instruction_id") != outcome.instruction_id
            ):
                raise GatewayError(Code.STATE_CHANGED, 409)
        cancelled[outcome.instruction_id] = step
    # Stable ordering avoids opposite lock orders for multi-cancel plans.
    for instruction_id, step in sorted(cancelled.items()):
        tombstone = {
            "revocation": {
                "plan_id": plan["id"],
                "step_id": step.step_id,
                "tool": step.tool,
            }
        }
        row = await conn.execute(
            insert(t.scheduled_grants)
            .values(
                instruction_id=instruction_id,
                customer_id=plan["customer_id"],
                source_plan_id=plan["id"],
                scope_json=tombstone,
                scope_hash=digest(["revoked-grant", plan["customer_id"], instruction_id]),
                approved_risk="RED",
                status="REVOKED",
                created_at=func.now(),
                revoked_at=func.now(),
            )
            .on_conflict_do_update(
                index_elements=[t.scheduled_grants.c.instruction_id],
                set_={
                    "status": "REVOKED",
                    "revoked_at": func.coalesce(t.scheduled_grants.c.revoked_at, func.now()),
                },
                where=t.scheduled_grants.c.customer_id == plan["customer_id"],
            )
            .returning(t.scheduled_grants.c.instruction_id)
        )
        if row.scalar_one_or_none() is None:
            raise GatewayError(Code.STATE_CHANGED, 409)
