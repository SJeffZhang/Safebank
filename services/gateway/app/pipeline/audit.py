"""Stage ⑨ evaluation audit (PRD §7.12): append to the customer hash chain."""

from .context import EvalContext, Stage


class AuditStage(Stage):
    name = "audit"
    always_run = True

    async def run(self, ctx: EvalContext) -> None:
        # W3 implements this stage (records the evaluation). SHADOW_B1 mode must
        # not write the database or the audit chain. The append primitive lives
        # in services.gateway.app.audit.chain.
        return None


stage = AuditStage()
