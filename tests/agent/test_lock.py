"""C1d thread lock tests — pg_try_advisory_lock concurrency guards.

Uses a temporary PostgreSQL container (tmpfs, destroyed on exit).  Skipped unless
``AGENT_DB_TESTS=1``.
"""

from __future__ import annotations

import asyncio

import psycopg
import pytest

pytestmark = pytest.mark.db


def test_concurrent_same_thread_rejected(agent_pg):
    """Two concurrent connections: same thread_id, second pg_try_advisory_lock returns False."""
    async def _run():
        conn_a = await psycopg.AsyncConnection.connect(agent_pg.runtime_url)
        try:
            thread_id = "ses_lock_test"
            cur = await conn_a.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (thread_id,))
            row = await cur.fetchone()
            assert row[0] is True

            conn_b = await psycopg.AsyncConnection.connect(agent_pg.runtime_url)
            try:
                cur2 = await conn_b.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (thread_id,))
                row2 = await cur2.fetchone()
                assert row2[0] is False
            finally:
                await conn_b.close()
        finally:
            await conn_a.close()

    asyncio.run(_run())


def test_different_threads_ok(agent_pg):
    """Two different thread_ids can both acquire locks."""
    async def _run():
        conn_a = await psycopg.AsyncConnection.connect(agent_pg.runtime_url)
        try:
            cur = await conn_a.execute("SELECT pg_try_advisory_lock(hashtext(%s))", ("thread_a",))
            row = await cur.fetchone()
            assert row[0] is True

            conn_b = await psycopg.AsyncConnection.connect(agent_pg.runtime_url)
            try:
                cur2 = await conn_b.execute("SELECT pg_try_advisory_lock(hashtext(%s))", ("thread_b",))
                row2 = await cur2.fetchone()
                assert row2[0] is True
            finally:
                await conn_b.close()
        finally:
            await conn_a.close()

    asyncio.run(_run())


def test_lock_released_after_completion(agent_pg):
    """After connection closes, lock is released for same thread_id."""
    async def _run():
        thread_id = "ses_lock_release"
        conn_a = await psycopg.AsyncConnection.connect(agent_pg.runtime_url)
        cur = await conn_a.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (thread_id,))
        row = await cur.fetchone()
        assert row[0] is True
        await conn_a.close()

        conn_b = await psycopg.AsyncConnection.connect(agent_pg.runtime_url)
        try:
            cur2 = await conn_b.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (thread_id,))
            row2 = await cur2.fetchone()
            assert row2[0] is True
        finally:
            await conn_b.close()

    asyncio.run(_run())


def test_lock_released_on_connection_close(agent_pg):
    """Session-level lock is automatically released when connection closes."""
    async def _run():
        thread_id = "ses_lock_close"
        conn = await psycopg.AsyncConnection.connect(agent_pg.runtime_url)
        cur = await conn.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (thread_id,))
        row = await cur.fetchone()
        assert row[0] is True
        await conn.close()

        conn2 = await psycopg.AsyncConnection.connect(agent_pg.runtime_url)
        try:
            cur2 = await conn2.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (thread_id,))
            row2 = await cur2.fetchone()
            assert row2[0] is True
        finally:
            await conn2.close()

    asyncio.run(_run())