import asyncio
from uuid import uuid4

import pytest
from safebank_contracts.codes import Code
from sqlalchemy import select
from sqlalchemy.pool import NullPool

from services.gateway.app import tables as t
from services.gateway.app.db import engine
from services.gateway.app.errors import GatewayError
from services.gateway.app.state import legal, transition


@pytest.mark.parametrize("target", ["RESOLVED", "CANCELLED"])
def test_open_clarification_can_finish_but_terminal_cannot_reopen(target):
    assert legal("pending_clarifications", "OPEN", target)
    assert not legal("pending_clarifications", target, "OPEN")


def test_clarification_competing_updates_have_one_winner(gateway_db):
    async def run():
        db = engine(gateway_db.runtime_url, poolclass=NullPool)
        sid, cid = f"ses_{uuid4().hex}", f"clr_{uuid4().hex}"
        try:
            async with db.begin() as conn:
                from datetime import UTC, datetime

                await conn.execute(
                    t.sessions.insert().values(
                        id=sid, customer_id="cus_clr", created_at_wall=datetime.now(UTC)
                    )
                )
                await conn.execute(
                    t.pending_clarifications.insert().values(
                        id=cid, sid=sid, kind="FORM_REQUIRED", authorization_epoch=0, status="OPEN"
                    )
                )

            async def finish(target):
                try:
                    async with db.begin() as conn:
                        await transition(
                            conn,
                            t.pending_clarifications,
                            id_=cid,
                            from_statuses=["OPEN"],
                            to=target,
                        )
                    return target
                except GatewayError as exc:
                    assert exc.status == 409
                    assert exc.code is Code.STATE_CHANGED
                    return "conflict"

            results = await asyncio.gather(finish("RESOLVED"), finish("CANCELLED"))
            assert results.count("conflict") == 1
            async with db.connect() as conn:
                status = await conn.scalar(
                    select(t.pending_clarifications.c.status).where(
                        t.pending_clarifications.c.id == cid
                    )
                )
                assert status in {"RESOLVED", "CANCELLED"}
                with pytest.raises(ValueError):
                    await transition(
                        conn, t.pending_clarifications, id_=cid, from_statuses=[status], to="OPEN"
                    )
        finally:
            await db.dispose()

    asyncio.run(run())
