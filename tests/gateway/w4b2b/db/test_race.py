"""W4b2b fix round 5: race-condition test for session revocation with FOR UPDATE.

Calls the real ``_validate_confirmable`` directly (not through HTTP) with
raw DB connections and a minimal mock ``Request``.

- connA opens a transaction, calls ``_lock_approval`` then
  ``_validate_confirmable`` — this acquires ``SELECT … FOR UPDATE`` on the
  session row.
- connB (separate transaction) tries ``UPDATE sessions SET revoked_at_wall``
  with ``lock_timeout = 500ms`` → **must be blocked**.

Red-verification: temporarily remove ``.with_for_update()`` from the session
SELECT in ``_validate_confirmable`` (api/approvals.py line 139).  Without
the lock connB succeeds → the assertion fails.  Restore the lock → test
passes.  Temporary edit is NOT committed.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from sqlalchemy import text

from services.gateway.app import tables as t
from tests.gateway.w4b2b.db.conftest import (
    HMAC_KEY,
    WALL,
    _runtime_db,
    _seed_full,
    build_app,
)

# ── Minimal mock Request for _validate_confirmable ──────────────────────────


class _MockProfile:
    profile = "B2"


class _MockState:
    def __init__(self, key: bytes) -> None:
        self.approval_hmac_key = key
        self.settings = _MockProfile()


class _MockApp:
    def __init__(self, key: bytes) -> None:
        self.state = _MockState(key)


class _MockRequest:
    def __init__(self, key: bytes) -> None:
        self.app = _MockApp(key)


# ── Test ────────────────────────────────────────────────────────────────────


def test_for_update_blocks_concurrent_session_revoke(app_config):
    """Session FOR UPDATE in ``_validate_confirmable`` blocks concurrent revoke.

    Calls the **real** production functions ``_lock_approval`` and
    ``_validate_confirmable`` (imported from ``api/approvals.py``) on a raw
    DB connection so the test exercises the actual FOR UPDATE that protects
    G6/G7.  No monkey-patching, no HTTP — just two independent PostgreSQL
    connections inside the same asyncio loop.
    """

    import services.gateway.app.api.approvals as mod

    KEY = HMAC_KEY  # 32-byte test key (matches conftest default)
    VIEW_HASH = "b" * 64  # default used by _seed_full
    NOW = WALL + timedelta(minutes=1)  # not expired (expiry = WALL + 5 min)

    async def run():
        app = await build_app(app_config)
        db = _runtime_db(app)

        # ── Seed the approval chain ──────────────────────────────────
        async with db.begin() as conn:
            await _seed_full(
                conn,
                approval_id="apv_race",
                sid="ses_race",
                plan_id="pln_race",
                evaluation_id="evl_race",
                hmac_key=KEY,
            )

        # ── Mock request (provides key + profile to _validate_confirmable) ─
        mock_req = _MockRequest(KEY)

        # ── connA: lock approval + validate → holds FOR UPDATE on session ─
        async with db.connect() as conn_a:
            async with conn_a.begin():
                approval = await mod._lock_approval(conn_a, "apv_race", "cus_zs")

                # This acquires FOR UPDATE on session, plan, evaluation rows.
                plan, evaluation, binding = await mod._validate_confirmable(
                    conn_a,
                    approval,
                    view_hash=VIEW_HASH,
                    now=NOW,
                    request=mock_req,
                )

                # ── connB: try to revoke the session with lock_timeout ─
                blocked = True

                async def try_revoke():
                    nonlocal blocked
                    try:
                        async with db.begin() as conn_b:
                            await conn_b.execute(text("SET LOCAL lock_timeout = '500ms'"))
                            await conn_b.execute(
                                t.sessions.update()
                                .where(t.sessions.c.id == "ses_race")
                                .values(revoked_at_wall=WALL)
                            )
                        # If we get here, connB was NOT blocked → race window.
                        blocked = False
                    except Exception:
                        blocked = True

                task = asyncio.create_task(try_revoke())
                await asyncio.sleep(0.8)  # wait longer than lock_timeout
                await task

                assert blocked, (
                    "FOR UPDATE must block concurrent session revocation; "
                    "connB committed revocation while connA held FOR UPDATE"
                )

                # connA rolls back — neither confirm nor revoke persisted.
                await conn_a.rollback()

        # ── After rollback: connB retry should succeed (lock released) ─
        async with db.begin() as conn:
            await conn.execute(
                t.sessions.update()
                .where(t.sessions.c.id == "ses_race")
                .values(revoked_at_wall=WALL)
            )

        await db.dispose()

    asyncio.run(run())
