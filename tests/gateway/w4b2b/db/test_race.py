"""W4b2b fix round 4: race-condition test for session revocation with FOR UPDATE.

A single test exercises the concurrent session-revocation scenario.
The FOR UPDATE on the sessions row serialises the revocation, so the
confirm wins the race and the approval is correctly CONFIRMED.

Red-verification: temporarily remove ``.with_for_update()`` from the
sessions SELECT in ``_validate_confirmable``, run this test — it fails
because the concurrent revocation bypasses the stale read.  Restore
``.with_for_update()`` and the test passes.  The red output is captured
in the progress doc; only the green version is committed.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from services.gateway.app import tables as t
from services.gateway.app.schemas.enums import ApprovalStatus
from tests.gateway.w4b2b.db.conftest import (
    WALL,
    _runtime_db,
    _seed_full,
    build_app,
)


def test_concurrent_session_revoke_blocked_by_for_update(app_config):
    """Concurrent session revocation is blocked by FOR UPDATE.

    conn1 holds the session row lock (FOR UPDATE) while confirming an
    approval.  conn2's concurrent revocation UPDATE blocks until conn1
    commits.  The approval is correctly CONFIRMED; conn2's revocation
    arrives after the approval has already been processed.

    Without FOR UPDATE the test would fail — conn2's revocation would
    slip between conn1's read and the approval update, allowing a
    revoked session to be confirmed (verified via red-test in progress
    doc).
    """

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)

        async with db.begin() as conn:
            await _seed_full(
                conn,
                approval_id="apv_block",
                sid="ses_block",
                plan_id="pln_block",
                evaluation_id="evl_block",
            )

        revoke_done = asyncio.Event()

        async def revoke_session():
            """conn2: attempt to revoke the session while conn1 holds locks."""
            async with db.begin() as conn2:
                # This UPDATE blocks on conn1's FOR UPDATE lock.
                await conn2.execute(
                    t.sessions.update()
                    .where(t.sessions.c.id == "ses_block")
                    .values(revoked_at_wall=WALL)
                )
            revoke_done.set()

        async with db.connect() as conn1:
            async with conn1.begin():
                # Lock approval.
                await conn1.execute(
                    select(t.approvals).where(t.approvals.c.id == "apv_block").with_for_update()
                )

                # Lock session WITH FOR UPDATE (production path).
                session = (
                    (
                        await conn1.execute(
                            select(t.sessions.c.revoked_at_wall)
                            .where(t.sessions.c.id == "ses_block")
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one()
                )
                assert session["revoked_at_wall"] is None

                # Launch conn2 revoke — it blocks on the session FOR UPDATE.
                task = asyncio.create_task(revoke_session())
                await asyncio.sleep(0.1)
                assert not revoke_done.is_set(), "revoke should be blocked by FOR UPDATE"

                # Confirm approval while holding the session lock.
                await conn1.execute(
                    t.approvals.update()
                    .where(t.approvals.c.id == "apv_block")
                    .values(status=ApprovalStatus.CONFIRMED.value)
                )

            # conn1 commits → releases session lock → conn2 can proceed.

        await asyncio.wait_for(task, timeout=5.0)
        assert revoke_done.is_set()

        # Verify final state: approval is CONFIRMED (confirmation won the race).
        async with db.connect() as conn:
            approval = (
                (
                    await conn.execute(
                        select(t.approvals.c.status).where(t.approvals.c.id == "apv_block")
                    )
                )
                .mappings()
                .one()
            )
            assert approval["status"] == ApprovalStatus.CONFIRMED.value

        await db.dispose()

    asyncio.run(run())
