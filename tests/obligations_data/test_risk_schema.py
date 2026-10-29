"""Real PostgreSQL schema acceptance for risk_assessments (C5 fact table)."""

import asyncio
from datetime import datetime, timezone

import pytest
from app import models as m
from app.db import engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.pool import NullPool

from tests.obligations_data.conftest import _migrate
from tests.obligations_data.fixtures import install_core, install_obligations

TZ = timezone.fromutc


def at(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _valid(**updates):
    values = dict(
        id="ra_zs_v1",
        customer_id="cus_zs",
        questionnaire_id="rq_v1",
        answers={"q1": "a", "q2": "b", "q3": "c", "q4": "d", "q5": "e", "q6": "f"},
        score=12,
        level="R3",
        assessed_at=at("2026-10-15T09:00:00+08:00"),
        valid_until=at("2027-10-15T09:00:00+08:00"),
        plan_id="pln_risk_test",
    )
    values.update(updates)
    return values


async def _insert(admin_url, **values):
    admin = engine(admin_url, poolclass=NullPool)
    async with admin.begin() as conn:
        await conn.execute(m.risk_assessments.insert().values(**_valid(**values)))
    await admin.dispose()


async def _insert_raw(admin_url, **values):
    admin = engine(admin_url, poolclass=NullPool)
    async with admin.begin() as conn:
        await conn.execute(m.risk_assessments.insert().values(**values))
    await admin.dispose()


# ── upgrade head twice ──────────────────────────────────────────────


def test_alembic_version_is_head(obligations_db):
    async def check():
        db = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with db.connect() as c:
            assert (
                await c.scalar(text("SELECT version_num FROM alembic_version")) == "0008_funds"
            )
            owner = await c.scalar(
                text("SELECT tableowner FROM pg_tables WHERE tablename='risk_assessments'")
            )
            assert owner == "postgres"
        await db.dispose()

    asyncio.run(check())


# ── CHECK constraints ───────────────────────────────────────────────


def test_score_below_6_rejected(obligations_db):
    async def check():
        with pytest.raises(DBAPIError) as exc:
            await _insert(
                obligations_db.admin_url,
                id="ra_score_lo",
                score=5,
                plan_id="pln_score_lo",
            )
        assert "risk_assessments_score_check" in str(exc.value.orig)

    asyncio.run(check())


def test_score_above_24_rejected(obligations_db):
    async def check():
        with pytest.raises(DBAPIError) as exc:
            await _insert(
                obligations_db.admin_url,
                id="ra_score_hi",
                score=25,
                plan_id="pln_score_hi",
            )
        assert "risk_assessments_score_check" in str(exc.value.orig)

    asyncio.run(check())


def test_invalid_level_rejected(obligations_db):
    async def check():
        with pytest.raises(DBAPIError) as exc:
            await _insert(
                obligations_db.admin_url,
                id="ra_level_bad",
                level="R6",
                plan_id="pln_level_bad",
            )
        assert "risk_assessments_level_check" in str(exc.value.orig)

    asyncio.run(check())


def test_valid_until_not_after_assessed_at_rejected(obligations_db):
    async def check():
        with pytest.raises(DBAPIError) as exc:
            await _insert(
                obligations_db.admin_url,
                id="ra_valid_equal",
                valid_until=at("2026-10-15T09:00:00+08:00"),
                plan_id="pln_valid_equal",
            )
        assert "risk_assessments_valid_until_check" in str(exc.value.orig)

    asyncio.run(check())


def test_empty_questionnaire_id_rejected(obligations_db):
    async def check():
        with pytest.raises(DBAPIError) as exc:
            await _insert(
                obligations_db.admin_url,
                id="ra_qid_empty",
                questionnaire_id="",
                plan_id="pln_qid_empty",
            )
        assert "risk_assessments_questionnaire_id_check" in str(exc.value.orig)

    asyncio.run(check())


# ── UNIQUE constraint ───────────────────────────────────────────────


def test_duplicate_plan_id_rejected(obligations_db):
    async def check():
        await _insert(obligations_db.admin_url, plan_id="pln_dup")
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        with pytest.raises(DBAPIError) as exc:
            await _insert(obligations_db.admin_url, plan_id="pln_dup", id="ra_zs_v2")
        assert "risk_assessments_plan_id_key" in str(exc.value.orig)
        await admin.dispose()

    asyncio.run(check())


# ── FK constraint ───────────────────────────────────────────────────


def test_nonexistent_customer_rejected(obligations_db):
    async def check():
        with pytest.raises(DBAPIError) as exc:
            await _insert(
                obligations_db.admin_url,
                id="ra_fk_bad",
                customer_id="cus_nonexistent",
                plan_id="pln_fk_bad",
            )
        assert "risk_assessments_customer_id_fkey" in str(exc.value.orig)

    asyncio.run(check())


# ── Append-only trigger ─────────────────────────────────────────────


def test_update_rejected(obligations_db):
    async def check():
        await _insert(obligations_db.admin_url, id="ra_immutable", plan_id="pln_immutable")
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        async with admin.begin() as conn:
            with pytest.raises(DBAPIError) as exc:
                async with conn.begin_nested():
                    await conn.execute(
                        text("UPDATE risk_assessments SET score=24 WHERE plan_id='pln_immutable'")
                    )
            assert getattr(exc.value.orig, "sqlstate", None) == "23514"
        await admin.dispose()

    asyncio.run(check())


def test_delete_rejected(obligations_db):
    async def check():
        await _insert(obligations_db.admin_url, id="ra_no_delete", plan_id="pln_no_delete")
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        async with admin.begin() as conn:
            with pytest.raises(DBAPIError) as exc:
                async with conn.begin_nested():
                    await conn.execute(
                        text("DELETE FROM risk_assessments WHERE plan_id='pln_no_delete'")
                    )
            assert getattr(exc.value.orig, "sqlstate", None) == "23514"
        await admin.dispose()

    asyncio.run(check())


# ── Runtime role cannot DDL / TRUNCATE ──────────────────────────────


def test_runtime_cannot_ddl_or_truncate(obligations_db):
    async def check():
        db = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with db.connect() as c:
            for statement in (
                "CREATE TABLE illegal_risk(id integer)",
                "TRUNCATE risk_assessments",
            ):
                with pytest.raises(DBAPIError):
                    async with c.begin_nested():
                        await c.execute(text(statement))
        await db.dispose()

    asyncio.run(check())


# ── Upgrade from 0003 preserves existing obligation data ────────────


def test_0003_data_survives_0006_upgrade(obligations_0003_db):
    db = obligations_0003_db

    async def seed_0003():
        admin = engine(db.admin_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            await install_obligations(conn)
        await admin.dispose()

    def capture_0003_tables():
        async def _capture():
            admin = engine(db.admin_url, poolclass=NullPool)
            async with admin.connect() as conn:
                rows = (
                    (await conn.execute(text("SELECT count(*) FROM merchants"))).scalar(),
                    (await conn.execute(text("SELECT count(*) FROM merchant_billing"))).scalar(),
                    (
                        await conn.execute(text("SELECT count(*) FROM standing_instructions"))
                    ).scalar(),
                    (await conn.execute(text("SELECT count(*) FROM instruction_runs"))).scalar(),
                    (await conn.scalar(text("SELECT version_num FROM alembic_version"))),
                )
            await admin.dispose()
            return rows

        return asyncio.run(_capture())

    # 1. Migrate to 0003, install data.
    _migrate(db, "0003_obligations")
    asyncio.run(seed_0003())

    # 2. Capture counts at 0003.
    m_count, b_count, s_count, r_count, ver_before = capture_0003_tables()
    assert ver_before == "0003_obligations"
    assert m_count == 4
    assert b_count == 6
    assert s_count == 7
    assert r_count == 0  # no runs installed by fixture

    # 3. Upgrade to 0006 twice.
    _migrate(db, "head")
    _migrate(db, "head")

    # 4. Verify existing obligation data unchanged.
    m2, b2, s2, r2, ver_after = capture_0003_tables()
    assert ver_after == "0008_funds"
    assert m2 == m_count
    assert b2 == b_count
    assert s2 == s_count
    assert r2 == r_count

    # 5. Verify risk_assessments table exists and is writable.
    async def exercise_0006():
        admin = engine(db.admin_url, poolclass=NullPool)
        runtime = engine(db.runtime_url, poolclass=NullPool)
        await _insert_raw(
            db.admin_url,
            id="ra_upgrade_test",
            customer_id="cus_zs",
            questionnaire_id="rq_v1",
            answers={"q1": "a"},
            score=12,
            level="R3",
            assessed_at=at("2026-10-15T09:00:00+08:00"),
            valid_until=at("2027-10-15T09:00:00+08:00"),
            plan_id="pln_upgrade_test",
        )
        async with runtime.connect() as conn:
            count = await conn.scalar(text("SELECT count(*) FROM risk_assessments"))
            assert count == 1
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(exercise_0006())
