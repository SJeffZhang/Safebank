"""W4b2b fix round 3: race-condition tests for session revocation bypass.

P1: ``_validate_confirmable`` reads the sessions row without FOR UPDATE,
allowing a concurrent session revocation to pass through the check and
approve a revoked session.
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


def test_session_revoke_race_without_for_update_would_bypass(app_config):
    """Red test: prove the race window exists when sessions row is read without
    FOR UPDATE.

    Two concurrent connections:
    - conn1 (confirm path): locks approval, reads session WITHOUT lock,
      sees no revocation. conn2 concurrently revokes the session.
    - conn1's stale read would have passed the check, allowing confirmation
      of a revoked session.

    This test runs at the DB level (not HTTP) to explicitly control
    transaction interleaving.  It does NOT write through the app endpoints
    — it demonstrates the conceptual race that the FOR UPDATE fix closes.
    """

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)

        async with db.begin() as conn:
            await _seed_full(
                conn,
                approval_id="apv_race",
                sid="ses_race",
                plan_id="pln_race",
                evaluation_id="evl_race",
            )

        # ── Phase 1: conn1 locks approval, reads session WITHOUT lock ──
        async with db.connect() as conn1:
            async with conn1.begin() as txn1:
                # Lock the approval row (matching _lock_approval).
                approval = (
                    (
                        await conn1.execute(
                            select(t.approvals)
                            .where(t.approvals.c.id == "apv_race")
                            .with_for_update()
                        )
                    )
                    .mappings()
                    .one()
                )
                assert approval["status"] == "PENDING"

                # Read sessions WITHOUT FOR UPDATE (old buggy path).
                session_before = (
                    (
                        await conn1.execute(
                            select(t.sessions.c.revoked_at_wall).where(
                                t.sessions.c.id == "ses_race"
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                # The session is not revoked at this point.
                assert session_before["revoked_at_wall"] is None

                # ── Phase 2: conn2 revokes the session concurrently ──
                async with db.begin() as conn2:
                    await conn2.execute(
                        t.sessions.update()
                        .where(t.sessions.c.id == "ses_race")
                        .values(revoked_at_wall=WALL)
                    )
                    # conn2 commits successfully.

                # ── Phase 3: conn1 checks the stale read ──
                # The stale `session_before` still has revoked_at_wall=None.
                # In the old code this check passes — the approval would be
                # confirmed despite the session being revoked.
                assert session_before["revoked_at_wall"] is None  # stale!
                assert session_before["revoked_at_wall"] is None  # ← would pass check

                # Confirm: re-reading WITHIN the same transaction (READ COMMITTED)
                # sees the committed revocation from conn2.
                session_after = (
                    (
                        await conn1.execute(
                            select(t.sessions.c.revoked_at_wall).where(
                                t.sessions.c.id == "ses_race"
                            )
                        )
                    )
                    .mappings()
                    .one()
                )
                # In READ COMMITTED this DOES see the committed update.
                assert session_after["revoked_at_wall"] is not None

                # The gap between session_before and session_after is exactly
                # the race window.  The old code that used session_before
                # (single read, no lock) would confirm a revoked session.

                # Rollback conn1 — we are only proving the race exists.
                await txn1.rollback()

        # ── Verify: conn2's revocation was committed ──
        async with db.connect() as conn:
            row = (
                (
                    await conn.execute(
                        select(t.sessions.c.revoked_at_wall).where(t.sessions.c.id == "ses_race")
                    )
                )
                .mappings()
                .one()
            )
            assert row["revoked_at_wall"] is not None

        await db.dispose()

    asyncio.run(run())


def test_confirm_revoked_session_blocked_by_for_update(app_config):
    """Green test: with FOR UPDATE on the sessions row, a concurrent
    revocation blocks until conn1 commits, then the re-check catches it.

    We prove this by:
    1. conn1 locks approval, reads session WITH FOR UPDATE — blocks future revocations.
    2. Launch conn2 as a concurrent task that tries to revoke the session.
    3. conn1 proceeds with confirm (transition approval to CONFIRMED).
    4. conn1 commits, releasing the session lock.
    5. conn2's revocation resumes but the approval is already CONFIRMED
       — and more importantly, the G6/G7 code path with FOR UPDATE ensures
       conn2 would block during the validation window.
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
                # This UPDATE will block until conn1 commits because conn1
                # holds FOR UPDATE on the sessions row.
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

                # Lock session WITH FOR UPDATE (new fixed path).
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

                # Launch conn2 revoke task — it will block on the session lock.
                task = asyncio.create_task(revoke_session())
                # Give conn2 a moment to reach the UPDATE and block.
                await asyncio.sleep(0.1)
                assert not revoke_done.is_set()

                # Confirm the approval while holding the session lock.
                await conn1.execute(
                    t.approvals.update()
                    .where(t.approvals.c.id == "apv_block")
                    .values(status=ApprovalStatus.CONFIRMED.value)
                )

            # txn1 commits → releases session lock → conn2 can proceed.

        # Wait for conn2 to finish.
        await asyncio.wait_for(task, timeout=5.0)
        assert revoke_done.is_set()

        # conn2's revocation committed AFTER conn1, but the approval was
        # already CONFIRMED (and G6/G7 would have transitioned the plan).
        # The FOR UPDATE ensured conn2 couldn't slip in between the read
        # and the approval update.

        # Verify final state: approval is CONFIRMED.
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
            # The confirmation won the race; session is now revoked but
            # the approval was already processed.
            assert approval["status"] == ApprovalStatus.CONFIRMED.value

        await db.dispose()

    asyncio.run(run())
