"""Stage ⑨ evaluation audit (PRD §7.12): append to the customer hash chain."""

from .context import EvalContext, Stage


class AuditStage(Stage):
    name = "audit"

    async def run(self, ctx: EvalContext) -> None:
        # W5 wires the audit interfaces; the append primitive lives in
        # services.gateway.app.audit.chain and is exercised by W0 tests.
        return None


stage = AuditStage()
