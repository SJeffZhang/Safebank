"""Stage ⑦ cross-session workflow (PRD §7.8): sequence matcher and cooldowns."""

from .context import EvalContext, Stage


class WorkflowStage(Stage):
    name = "workflow"

    async def run(self, ctx: EvalContext) -> None:
        # W3 implements this stage; W0 provides the no-op slot.
        return None


stage = WorkflowStage()
