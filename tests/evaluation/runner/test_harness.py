"""E0b T2a: the evaluation harness wires the real gateway to a real bank backend.

Creates a session and registers one utterance through the real gateway, then
checks that an assistant JWT came back, that the utterance row actually landed
in the gateway database, and that the gateway's L3 client is the real one
(never ``FakeL3``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import text

from evaluation.runner.harness import eval_harness
from services.gateway.app.l3.client import L3Client
from services.gateway.app.l3.fake import FakeL3

FIXTURE = Path(__file__).parent / "fixtures" / "mini_seed.json"


def test_harness_session_and_utterance(gateway_db, obligations_db, monkeypatch):
    async def run():
        async with eval_harness(gateway_db, obligations_db, FIXTURE, monkeypatch) as h:
            assert isinstance(h.app.state.l3, L3Client)
            assert not isinstance(h.app.state.l3, FakeL3)
            sid = await h.new_session("cus_zs")
            utterance_id, assistant_jwt = await h.register_utterance(sid, "查询余额")
            assert assistant_jwt
            async with h.database.connect() as conn:
                count = await conn.scalar(
                    text("SELECT count(*) FROM utterances WHERE id = :id"), {"id": utterance_id}
                )
                assert count == 1

    asyncio.run(run())
