"""Stage ① contract (PRD §7.2): step count, DAG, field caps, request idempotency."""

from .context import EvalContext, Stage


class ContractStage(Stage):
    name = "contract"

    async def run(self, ctx: EvalContext) -> None:
        # W3 (G4 assembly) implements this stage; W0 provides the no-op slot.
        return None


stage = ContractStage()
