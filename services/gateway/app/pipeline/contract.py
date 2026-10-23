"""Stage ① contract (PRD §7.2): step count, DAG, field caps, request idempotency."""

from .context import EvalContext, Stage


class ContractStage(Stage):
    name = "contract"

    async def run(self, ctx: EvalContext) -> None:
        # W2 implements this stage; contract failures raise GatewayError (E422).
        return None


stage = ContractStage()
