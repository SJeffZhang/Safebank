"""Stage ⑨ delivers an audit event to the orchestrator's append_tx callback.

G4 queues this event until its HTTP-free persistence transaction commits, then
calls audit.append_tx. Replays are audited by the execution orchestrator after
append_evaluation assigns the revision. SHADOW_B1 never invokes a callback.
"""

from .context import EvalContext, EvalMode, Stage


class AuditStage(Stage):
    name = "audit"
    always_run = True

    async def run(self, ctx: EvalContext) -> None:
        if ctx.mode == EvalMode.SHADOW_B1 or ctx.audit is None or ctx.decision is None:
            return
        await ctx.audit("EVALUATION", ctx.decision.model_dump(mode="json", exclude={"shadow_b1"}))


stage = AuditStage()
