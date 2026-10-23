"""Stage ⑤ consistent snapshot (PRD §7.2): REPEATABLE READ READ ONLY via L3."""

from .context import EvalContext, Stage


class SnapshotStage(Stage):
    name = "snapshot"

    async def run(self, ctx: EvalContext) -> None:
        # W3 implements this stage (calls L3 /internal/snapshot, fills ctx.snapshot).
        return None


stage = SnapshotStage()
