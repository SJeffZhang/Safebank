"""Stage ④ intent scope (PRD §7.2, §7.6): multiset (target, amount) comparison."""

from .context import EvalContext, Stage


class IntentStage(Stage):
    name = "intent"

    async def run(self, ctx: EvalContext) -> None:
        # W2 implements this stage; W0 provides the no-op slot.
        return None


stage = IntentStage()
