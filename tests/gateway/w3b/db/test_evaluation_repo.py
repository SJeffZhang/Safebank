"""W3b Task 1.5: evaluation/repo.py real-PG tests.

Covers: latest_revision, append_evaluation (normal and revision conflict),
update_view_json.  Each test uses a unique plan_id because the tmpfs
PostgreSQL is shared across tests.
"""

from __future__ import annotations

import asyncio

import pytest
from safebank_contracts.codes import Code
from sqlalchemy import select
from sqlalchemy.pool import NullPool

from services.gateway.app import tables as t
from services.gateway.app.db import engine
from services.gateway.app.errors import GatewayError
from services.gateway.app.evaluation.repo import (
    append_evaluation,
    latest_revision,
    update_view_json,
)
from tests.gateway.w3b.db.conftest import WALL, _seed_session


async def _seed_plan(conn, *, plan_id, sid="ses_g4test", customer="cus_a", request_id=None):
    """Seed a minimal plan row (FK for evaluations)."""
    rid = request_id or f"req_{plan_id}"
    await conn.execute(
        t.plans.insert().values(
            id=plan_id,
            customer_id=customer,
            sid=sid,
            origin="agent",
            request_id=rid,
            request_hash="0" * 64,
            plan_hash="0" * 64,
            steps_hash="0" * 64,
            resolved_steps=[],
            utterance_ids=[],
            scope_ids=[],
            status="EVALUATED",
            created_at_wall=WALL,
        )
    )


def test_latest_revision_returns_zero_when_no_rows(gateway_db):
    async def run():
        eng = engine(gateway_db.admin_url, poolclass=NullPool)
        try:
            async with eng.connect() as conn:
                rev = await latest_revision(conn, "pln_nonexistent")
            assert rev == 0
        finally:
            await eng.dispose()

    asyncio.run(run())


def test_latest_revision_returns_max(gateway_db):
    async def run():
        eng = engine(gateway_db.admin_url, poolclass=NullPool)
        try:
            async with eng.begin() as conn:
                await _seed_session(conn)
                pid = "pln_revmax"
                await _seed_plan(conn, plan_id=pid, request_id="req_revmax")
                await conn.execute(
                    t.evaluations.insert().values(
                        id="evl_rev_1",
                        plan_id=pid,
                        revision=1,
                        decision={},
                        risk="GREEN",
                        findings=[],
                        preflight={},
                        versions={},
                        policy_version="sha256:a",
                        clock_version=1,
                        created_at_wall=WALL,
                    )
                )
                await conn.execute(
                    t.evaluations.insert().values(
                        id="evl_rev_2",
                        plan_id=pid,
                        revision=3,
                        decision={},
                        risk="YELLOW",
                        findings=[],
                        preflight={},
                        versions={},
                        policy_version="sha256:b",
                        clock_version=1,
                        created_at_wall=WALL,
                    )
                )

            async with eng.connect() as conn:
                rev = await latest_revision(conn, pid)
            assert rev == 3
        finally:
            await eng.dispose()

    asyncio.run(run())


def test_append_evaluation_normal(gateway_db):
    async def run():
        eng = engine(gateway_db.admin_url, poolclass=NullPool)
        try:
            pid = "pln_evl_normal"
            async with eng.begin() as conn:
                await _seed_session(conn)
                await _seed_plan(conn, plan_id=pid, request_id="req_evl_1")

            async with eng.begin() as conn2:
                await append_evaluation(
                    conn2,
                    plan_id=pid,
                    evaluation_id="evl_normal",
                    revision=1,
                    decision={"decision": "ALLOW"},
                    risk="GREEN",
                    findings=[],
                    preflight={"executable": True, "steps": [], "balances": []},
                    versions={"acc_a": 1},
                    policy_version="sha256:test",
                    clock_version=1,
                    created_at_wall=WALL,
                )

            async with eng.connect() as conn3:
                row = (
                    (
                        await conn3.execute(
                            select(t.evaluations).where(t.evaluations.c.id == "evl_normal")
                        )
                    )
                    .mappings()
                    .one()
                )
            assert row["plan_id"] == pid
            assert row["revision"] == 1
            assert row["decision"] == {"decision": "ALLOW"}
            assert row["risk"] == "GREEN"
        finally:
            await eng.dispose()

    asyncio.run(run())


def test_append_evaluation_revision_conflict_raises_409(gateway_db):
    async def run():
        eng = engine(gateway_db.admin_url, poolclass=NullPool)
        try:
            pid = "pln_evl_conflict"
            async with eng.begin() as conn:
                await _seed_session(conn)
                await _seed_plan(conn, plan_id=pid, request_id="req_evl_conflict")

            async with eng.begin() as conn2:
                await append_evaluation(
                    conn2,
                    plan_id=pid,
                    evaluation_id="evl_conflict_a",
                    revision=1,
                    decision={"decision": "ALLOW"},
                    risk="GREEN",
                    findings=[],
                    preflight={},
                    versions={},
                    policy_version="sha256:test",
                    clock_version=1,
                    created_at_wall=WALL,
                )

                with pytest.raises(GatewayError) as exc:
                    await append_evaluation(
                        conn2,
                        plan_id=pid,
                        evaluation_id="evl_conflict_b",
                        revision=1,
                        decision={"decision": "DENY"},
                        risk="RED",
                        findings=[],
                        preflight={},
                        versions={},
                        policy_version="sha256:test",
                        clock_version=1,
                        created_at_wall=WALL,
                    )
            assert exc.value.code is Code.STATE_CHANGED
        finally:
            await eng.dispose()

    asyncio.run(run())


def test_update_view_json_normal(gateway_db):
    async def run():
        eng = engine(gateway_db.admin_url, poolclass=NullPool)
        try:
            pid = "pln_vj_normal"
            async with eng.begin() as conn:
                await _seed_session(conn)
                await _seed_plan(conn, plan_id=pid, request_id="req_vj_1")
                await conn.execute(
                    t.evaluations.insert().values(
                        id="evl_vj",
                        plan_id=pid,
                        revision=1,
                        decision={},
                        risk="GREEN",
                        findings=[],
                        preflight={},
                        versions={},
                        policy_version="sha256:test",
                        clock_version=1,
                        created_at_wall=WALL,
                    )
                )

            async with eng.begin() as conn2:
                await update_view_json(
                    conn2,
                    evaluation_id="evl_vj",
                    plan_id=pid,
                    view_hash="a" * 64,
                    view_json={"key": "value"},
                )

            async with eng.connect() as conn3:
                row = (
                    (
                        await conn3.execute(
                            select(t.evaluations).where(t.evaluations.c.id == "evl_vj")
                        )
                    )
                    .mappings()
                    .one()
                )
            assert row["view_hash"] == "a" * 64
            assert row["view_json"] == {"key": "value"}
        finally:
            await eng.dispose()

    asyncio.run(run())


def test_update_view_json_not_found_raises_404(gateway_db):
    async def run():
        eng = engine(gateway_db.admin_url, poolclass=NullPool)
        try:
            async with eng.begin() as conn:
                with pytest.raises(GatewayError) as exc:
                    await update_view_json(
                        conn,
                        evaluation_id="evl_nonexistent",
                        plan_id="pln_nonexistent",
                        view_hash="a" * 64,
                        view_json={},
                    )
            assert exc.value.code is Code.NOT_FOUND
        finally:
            await eng.dispose()

    asyncio.run(run())
