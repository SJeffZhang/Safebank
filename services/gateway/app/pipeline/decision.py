"""Stage ⑧ decision aggregation (PRD §7.9): DENY > REPLAN > APPROVAL > ALLOW."""

from .context import EvalContext, Stage


class DecisionStage(Stage):
    name = "decision"

    async def run(self, ctx: EvalContext) -> None:
        # W3 implements this stage; W0 provides the no-op slot.
        return None


stage = DecisionStage()
