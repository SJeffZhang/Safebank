"""Stage ⑥ cumulative preflight (PRD §7.7): bankcore apply_* over the snapshot."""

from .context import EvalContext, Stage


class PreflightStage(Stage):
    name = "preflight"

    async def run(self, ctx: EvalContext) -> None:
        # W3 implements this stage (shared bankcore rules, never writes L3).
        return None


stage = PreflightStage()
