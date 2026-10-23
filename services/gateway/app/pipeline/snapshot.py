"""Stage ⑤ pins the exact read-only snapshot already loaded by entity resolution."""

from .context import EvalContext, Stage


class SnapshotStage(Stage):
    name = "snapshot"

    async def run(self, ctx: EvalContext) -> None:
        if ctx.plan_id is not None:
            ctx.snapshot = await ctx.bank_state()


stage = SnapshotStage()
