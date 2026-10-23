"""Stage ⑧ decision aggregation (PRD §7.9): DENY > REPLAN > APPROVAL > ALLOW."""

from .context import EvalContext, Stage


class DecisionStage(Stage):
    name = "decision"
    always_run = True

    async def run(self, ctx: EvalContext) -> None:
        # W3 implements this stage; DENY still reaches here to assemble a Decision.
        return None


stage = DecisionStage()
