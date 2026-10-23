"""Stage ⑤ consistent snapshot (PRD §7.2, §7.7.1): pin one L3 snapshot.

The snapshot is requested once through :meth:`EvalContext.bank_state` and
cached on the context (conductor decision #4). Stage ⑤ simply publishes that
same cached state as ``ctx.snapshot`` so ⑥⑦⑧ share one immutable picture with
the pinned ``versions`` / ``clock_version`` / ``sim_now``. It never re-fetches
L3 and never writes it.
"""

from .context import EvalContext, Stage


class SnapshotStage(Stage):
    name = "snapshot"

    async def run(self, ctx: EvalContext) -> None:
        # An empty infra-probe context has no plan/customer and no L3 client.
        if ctx.plan_draft is None and ctx.customer_id is None:
            return
        ctx.snapshot = await ctx.bank_state()


stage = SnapshotStage()
