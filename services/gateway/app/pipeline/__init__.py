"""Evaluation pipeline orchestration (PRD §7.2).

The nine stages run in order. A hard DENY finding skips the snapshot-dependent
stages (⑤ snapshot, ⑥ preflight, ⑦ workflow) but decision (⑧) and audit (⑨)
always run so a DENY still yields a complete ``Decision``. Contract/HTTP-level
failures raise :class:`GatewayError` (they are E4xx, not findings). Profile
ablation (PRD §10.1) disables stages via :meth:`Stage.enabled`.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..errors import GatewayError
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
from .context import EvalContext, EvalMode, Stage, StageStatus

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

SKIP_AFTER_DENY = frozenset({"snapshot", "preflight", "workflow"})


async def evaluate(
    ctx: EvalContext, *, stages: Sequence[Stage] | None = None, mode: EvalMode | None = None
) -> EvalContext:
    if mode is not None:
        ctx.mode = mode
    for stage in stages if stages is not None else STAGES:
        if not stage.enabled(ctx.profile):
            ctx.stage_status[stage.name] = StageStatus.SKIPPED
            continue
        if ctx.short_circuit and stage.name in SKIP_AFTER_DENY and not stage.always_run:
            ctx.stage_status[stage.name] = StageStatus.SKIPPED
            continue
        ctx.stage_status[stage.name] = StageStatus.PENDING
        try:
            await stage.run(ctx)
        except GatewayError:
            ctx.stage_status[stage.name] = StageStatus.ERROR
            raise
        ctx.stage_status[stage.name] = StageStatus.PASSED
        if ctx.has_hard_deny():
            ctx.short_circuit = True
    return ctx
