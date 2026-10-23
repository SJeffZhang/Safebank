"""Stage ② entity and ownership (PRD §7.2): resolve references, verify ownership."""

from .context import EvalContext, Stage


class EntityStage(Stage):
    name = "entity"

    async def run(self, ctx: EvalContext) -> None:
        # W2 implements this stage; W0 provides the no-op slot.
        return None


stage = EntityStage()
