"""Evaluation pipeline orchestration (PRD §7.2).

The nine stages run in order. A hard DENY finding short-circuits the remaining
stages except audit, which always records the reason. ``evaluate`` accepts an
explicit ``stages`` sequence so tests can inject a failing stage without
monkeypatching the registry.
"""

from __future__ import annotations

from collections.abc import Sequence

from . import (
    audit,
    contract,
    decision,
    entity,
    intent,
    preflight,
    provenance,
    snapshot,
    workflow,
)
from .context import Effect, EvalContext, Stage

STAGES: tuple[Stage, ...] = (
    contract.stage,
    entity.stage,
    provenance.stage,
    intent.stage,
    snapshot.stage,
    preflight.stage,
    workflow.stage,
    decision.stage,
    audit.stage,
)

AUDIT_STAGE_NAME = "audit"


async def evaluate(ctx: EvalContext, *, stages: Sequence[Stage] | None = None) -> EvalContext:
    for stage in stages if stages is not None else STAGES:
        if ctx.short_circuit and stage.name != AUDIT_STAGE_NAME:
            continue
        await stage.run(ctx)
        if ctx.has_hard_deny():
            ctx.short_circuit = True
    return ctx


def hard_deny(ctx: EvalContext) -> bool:
    return any(f.effect is Effect.DENY for f in ctx.findings)
