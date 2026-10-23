"""Stage ⑤ snapshot pins the single cached L3 state without re-fetching."""

import asyncio

from safebank_contracts.state import BankState

from services.gateway.app.l3.fake import FakeL3
from services.gateway.app.pipeline.entity import EntityStage
from services.gateway.app.pipeline.snapshot import SnapshotStage
from tests.gateway.w3a.conftest import build_ctx, transfer, w3_state


def test_snapshot_reuses_cached_state_and_fetches_once():
    fake = FakeL3()
    fake.set_snapshot(w3_state())
    ctx = build_ctx([transfer("pye_b", 100000)])
    ctx._lazy_state = None
    ctx.l3 = None  # entity will use the fake transport below

    async def run():
        from services.gateway.app.l3.client import L3Client

        ctx.l3 = L3Client("http://fake", "gateway-token", transport=fake.transport())
        await EntityStage().run(ctx)
        await SnapshotStage().run(ctx)

    asyncio.run(run())
    snapshots = [c for c in fake.calls if c["op"] == "snapshot"]
    assert len(snapshots) == 1
    assert isinstance(ctx.snapshot, BankState)
    assert ctx.snapshot.versions == {"cus_a": 0, "cus_b": 0}
    assert ctx.snapshot.clock_version == 1
