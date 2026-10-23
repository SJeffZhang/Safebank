"""Stage ③ provenance and taint (PRD §7.2, §7.5): arg_sources, derived templates."""

from .context import EvalContext, Stage


class ProvenanceStage(Stage):
    name = "provenance"

    async def run(self, ctx: EvalContext) -> None:
        # W2 implements this stage; W0 provides the no-op slot.
        return None


stage = ProvenanceStage()
