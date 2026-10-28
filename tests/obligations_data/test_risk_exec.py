"""C5 risk assessment execution tests.

submit_risk_assessment (W12) + get_risk_questionnaire (R12) + get_risk_profile (R13)
against a real tmpfs Postgres backend served via HTTP.

Uses the obligations fixtures (install_core + install_obligations) as the seed baseline.
"""

import asyncio
import socket
import threading
from datetime import datetime

import httpx
import pytest
import uvicorn
from app import models as m
from app.db import engine
from app.main import create_app
from app.state import load_bank_state, resolve_scope
from safebank_bankcore.banking import apply_plan
from safebank_contracts.enums import Risk
from safebank_contracts.plan import Execute, InteractiveEvidence, PlanBody, Prepare
from safebank_contracts.state import InsertAssessment
from sqlalchemy import func, select as sa_select, text
from sqlalchemy.pool import NullPool

from tests.contracts.samples import step
from tests.obligations_data.fixtures import KEY, NOW, install_core, install_obligations

SERVICE_TOKEN = "obligations-test-token"
DEADLINE = "2099-01-01T00:00:00Z"
PLAN_HASH = "hash_risk_001"


@pytest.fixture(autouse=True)
def clean_database(obligations_db):
    """Truncate every application table before each test."""

    async def _truncate():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        async with admin.begin() as conn:
            names = ",".join(m.metadata.tables)
            await conn.execute(text(f"TRUNCATE {names} CASCADE"))
        await admin.dispose()

    asyncio.run(_truncate())
    yield


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _serve(app, port):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server


def _read(client, port, tool, args, *, customer_id="cus_zs", auth=SERVICE_TOKEN, timeout=10.0):
    return client.post(
        f"http://127.0.0.1:{port}/internal/read/{tool}",
        json={"args": args, "customer_id": customer_id},
        headers={"X-Service-Token": auth},
        timeout=timeout,
    )


async def _prepare_app(obligations_db):
    """Install core + obligations and return (app, port, runtime_engine)."""
    admin = engine(obligations_db.admin_url, poolclass=NullPool)
    runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
    async with admin.begin() as conn:
        await install_core(conn)
        await install_obligations(conn)
    await admin.dispose()
    app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
    port = free_port()
    return app, port, runtime


@pytest.fixture
def app_port(obligations_db):
    app, port, runtime = asyncio.run(_prepare_app(obligations_db))
    server = _serve(app, port)
    yield port
    server.should_exit = True
    asyncio.run(runtime.dispose())


# ── W12 submit_risk_assessment ──

_ANSWERS = [
    {"question_id": "q01", "option_id": "c"},
    {"question_id": "q02", "option_id": "d"},
    {"question_id": "q03", "option_id": "c"},
    {"question_id": "q04", "option_id": "b"},
    {"question_id": "q05", "option_id": "a"},
    {"question_id": "q06", "option_id": "c"},
]
# scores: 3+4+3+2+1+3 = 16 → R3 (14-17)


def _evidence(plan_hash: str) -> InteractiveEvidence:
    return InteractiveEvidence(
        type="INTERACTIVE",
        origin="app_form",
        scope_ids=("utt_1",),
        utterance_ids=("utt_1",),
        evaluation_id="evl_1",
        approved_evaluation_id=None,
        plan_hash=plan_hash,
        approval_id=None,
        confirmed_view_hash=None,
        final_view_hash=plan_hash,
        risk=Risk.GREEN,
        confirmed_at_wall=None,
        authorization_epoch=0,
    )


def _steps(answers=_ANSWERS):
    return PlanBody(
        intents=(),
        steps=[
            step(
                tool="submit_risk_assessment",
                step_id="s1",
                questionnaire_id="rq_v1",
                answers=[
                    {"question_id": a["question_id"], "option_id": a["option_id"]}
                    for a in answers
                ],
            )
        ],
        rationale="test",
    ).steps


def _bodies(plan_id, answers=_ANSWERS, plan_hash=PLAN_HASH, customer_id="cus_zs"):
    steps = _steps(answers)
    evidence = _evidence(plan_hash)
    prepare = Prepare(
        customer_id=customer_id,
        plan_id=plan_id,
        attempt=1,
        plan_hash=plan_hash,
        steps=steps,
        deadline_wall=DEADLINE,
        deadline_cap_wall=DEADLINE,
        authorization_evidence=evidence,
    )
    execute = Execute(
        customer_id=customer_id,
        plan_id=plan_id,
        attempt=1,
        plan_hash=plan_hash,
        steps=steps,
        expected_versions={customer_id: 0},
        clock_version=1,
        authorization_evidence=evidence,
    )
    return prepare, execute


def _submit(client, port, plan_id, answers=_ANSWERS, customer_id="cus_zs"):
    prepare, execute = _bodies(plan_id, answers, customer_id=customer_id)
    headers = {"X-Service-Token": SERVICE_TOKEN}
    r = client.post(
        f"http://127.0.0.1:{port}/internal/plans/prepare",
        json=prepare.model_dump(mode="json"),
        headers=headers,
        timeout=10.0,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "PREPARED"
    e = client.post(
        f"http://127.0.0.1:{port}/internal/plans/execute",
        json=execute.model_dump(mode="json"),
        headers=headers,
        timeout=10.0,
    )
    assert e.status_code == 200, e.text
    return e.json()


def _seed_risk(
    obligations_db,
    customer_id,
    *,
    level,
    assessed_at,
    valid_until,
    assessment_id,
    plan_id,
):
    """Directly install a risk assessment + customer summary (admin role)."""

    async def _run():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await conn.execute(
                m.risk_assessments.insert().values(
                    id=assessment_id,
                    customer_id=customer_id,
                    questionnaire_id="rq_v1",
                    answers=[
                        ["q01", "a"],
                        ["q02", "b"],
                        ["q03", "c"],
                        ["q04", "d"],
                        ["q05", "a"],
                        ["q06", "b"],
                    ],
                    score=12,
                    level=f"R{level}",
                    assessed_at=datetime.fromisoformat(assessed_at),
                    valid_until=datetime.fromisoformat(valid_until),
                    plan_id=plan_id,
                )
            )
            await conn.execute(
                m.customers.update()
                .where(m.customers.c.id == customer_id)
                .values(
                    risk_level=level,
                    risk_assessed_at=datetime.fromisoformat(assessed_at),
                    risk_valid_until=valid_until.split("T")[0],
                )
            )
        await admin.dispose()

    asyncio.run(_run())


class TestSubmitRiskAssessment:
    def test_fact_row_and_summary_match(self, app_port, obligations_db):
        """Real PG: submit_risk_assessment creates one fact row, updates customer summary."""
        client = httpx.Client()
        data = _submit(client, app_port, "plan_risk_001")
        assert data["status"] == "EXECUTED"

        async def _verify():
            admin = engine(obligations_db.admin_url, poolclass=NullPool)
            async with admin.begin() as conn:
                r = (
                    await conn.execute(
                        sa_select(m.risk_assessments).where(
                            m.risk_assessments.c.customer_id == "cus_zs"
                        )
                    )
                ).mappings().all()
                assert len(r) == 1
                row = r[0]
                # Score 3+4+3+2+1+3 = 16 → R3
                assert row["score"] == 16
                assert row["level"] == "R3"
                assert row["customer_id"] == "cus_zs"
                assert row["questionnaire_id"] == "rq_v1"
                assert row["plan_id"] == "plan_risk_001"
                assert row["answers"] is not None

                c = (
                    await conn.execute(
                        sa_select(m.customers).where(m.customers.c.id == "cus_zs")
                    )
                ).mappings().one()
                assert c["risk_level"] == 3
                assert c["risk_assessed_at"] is not None
                assert c["risk_valid_until"] == "2027-10-15"

                ops = (
                    await conn.execute(
                        sa_select(m.operation_events).where(
                            m.operation_events.c.plan_id == "plan_risk_001"
                        )
                    )
                ).mappings().all()
                assert len(ops) == 1
            await admin.dispose()

        asyncio.run(_verify())

    def test_twenty_retries_single_fact_row(self, app_port, obligations_db):
        """Same plan re-executed 20 times persists exactly one fact row."""
        client = httpx.Client()
        prepare, execute = _bodies("plan_risk_retry")
        headers = {"X-Service-Token": SERVICE_TOKEN}
        r = client.post(
            f"http://127.0.0.1:{app_port}/internal/plans/prepare",
            json=prepare.model_dump(mode="json"),
            headers=headers,
            timeout=10.0,
        )
        assert r.status_code == 200, r.text
        for _ in range(20):
            e = client.post(
                f"http://127.0.0.1:{app_port}/internal/plans/execute",
                json=execute.model_dump(mode="json"),
                headers=headers,
                timeout=10.0,
            )
            assert e.status_code == 200, e.text
            assert e.json()["status"] == "EXECUTED"

        async def _count():
            admin = engine(obligations_db.admin_url, poolclass=NullPool)
            async with admin.begin() as conn:
                assessments = await conn.scalar(
                    sa_select(func.count()).select_from(m.risk_assessments)
                )
                executions = await conn.scalar(
                    sa_select(func.count()).select_from(m.plan_executions)
                )
            await admin.dispose()
            return assessments, executions

        assessments, executions = asyncio.run(_count())
        assert assessments == 1
        assert executions == 1

    def test_illegal_answers_leave_no_data(self, app_port, obligations_db):
        """Unknown question_id passes the wire contract but fails L3 scoring, leaving no data."""
        bad = [
            {"question_id": "q01", "option_id": "c"},
            {"question_id": "q02", "option_id": "d"},
            {"question_id": "q03", "option_id": "c"},
            {"question_id": "q04", "option_id": "b"},
            {"question_id": "q05", "option_id": "a"},
            {"question_id": "q99", "option_id": "c"},
        ]
        client = httpx.Client()
        data = _submit(client, app_port, "plan_risk_bad", bad)
        assert data["status"] == "FAILED"
        assert data["error_code"] == "CONTRACT_INVALID"

        async def _count():
            admin = engine(obligations_db.admin_url, poolclass=NullPool)
            async with admin.begin() as conn:
                assessments = await conn.scalar(
                    sa_select(func.count()).select_from(m.risk_assessments)
                )
                c = (
                    await conn.execute(
                        sa_select(m.customers).where(m.customers.c.id == "cus_zs")
                    )
                ).mappings().one()
            await admin.dispose()
            return assessments, c["risk_level"]

        assessments, risk_level = asyncio.run(_count())
        assert assessments == 0
        assert risk_level is None

    def test_risk_profile_four_validity_states(self, app_port, obligations_db):
        """get_risk_profile reports is_valid/valid_until across four distinct states."""
        client = httpx.Client()

        # 1. never assessed
        data = _read(client, app_port, "get_risk_profile", {}).json()
        assert data["level"] is None
        assert data["assessment_id"] is None
        assert data["valid_until"] is None
        assert data["is_valid"] is False

        # 2. valid (fresh assessment submitted via HTTP)
        _submit(client, app_port, "plan_risk_valid")
        data = _read(client, app_port, "get_risk_profile", {}).json()
        assert data["level"] == 3
        assert data["is_valid"] is True
        assert data["valid_until"] == "2027-10-15"

        # 3. expired (valid_until before sim_now date)
        _seed_risk(
            obligations_db,
            "cus_sb",
            level=3,
            assessed_at="2025-10-14T09:00:00+08:00",
            valid_until="2026-10-14T09:00:00+08:00",
            assessment_id="ra_expired",
            plan_id="pln_expired",
        )
        data = _read(client, app_port, "get_risk_profile", {}, customer_id="cus_sb").json()
        assert data["level"] == 3
        assert data["is_valid"] is False
        assert data["valid_until"] == "2026-10-14"

        # 4. boundary: valid_until == sim_now date → still valid
        _seed_risk(
            obligations_db,
            "cus_ww",
            level=3,
            assessed_at="2025-10-15T09:00:00+08:00",
            valid_until="2026-10-15T09:00:00+08:00",
            assessment_id="ra_boundary",
            plan_id="pln_boundary",
        )
        data = _read(client, app_port, "get_risk_profile", {}, customer_id="cus_ww").json()
        assert data["level"] == 3
        assert data["is_valid"] is True
        assert data["valid_until"] == "2026-10-15"

    def test_questionnaire_content(self, app_port):
        """get_risk_questionnaire returns the six fixed rq_v1 questions with a/b/c/d options."""
        client = httpx.Client()
        data = _read(client, app_port, "get_risk_questionnaire", {}).json()
        assert data["questionnaire_id"] == "rq_v1"
        assert data["validity_months"] == 12
        questions = data["questions"]
        assert [q["id"] for q in questions] == ["q01", "q02", "q03", "q04", "q05", "q06"]
        for q in questions:
            assert q["prompt"]
            assert [o["id"] for o in q["options"]] == ["a", "b", "c", "d"]

    def test_preflight_and_reload_agree(self, app_port, obligations_db):
        """Pure-function preflight and the persisted+reloaded state agree."""
        steps = _steps()

        async def preflight():
            runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
            async with runtime.connect() as conn:
                scope = await resolve_scope(conn, "cus_zs", steps, KEY)
                state = await load_bank_state(conn, scope, NOW, customer_id="cus_zs")
                sim = apply_plan(state, steps, "cus_zs", "pln_preflight")
                assert sim.error is None
                record = next(e.record for e in sim.effects if isinstance(e, InsertAssessment))
                return record
            await runtime.dispose()

        record = asyncio.run(preflight())
        assert record.score == 16
        assert record.level == "R3"
        assert str(record.valid_until) == "2027-10-15"

        client = httpx.Client()
        data = _submit(client, app_port, "pln_preflight")
        assert data["status"] == "EXECUTED"

        async def reload():
            runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
            async with runtime.connect() as conn:
                scope = await resolve_scope(conn, "cus_zs", steps, KEY)
                return await load_bank_state(conn, scope, NOW, customer_id="cus_zs")
            await runtime.dispose()

        state = asyncio.run(reload())
        assert state.customers["cus_zs"].risk_level == 3
        assert state.assessments[0].id == record.id
        assert state.assessments[0].score == record.score
        assert state.assessments[0].level == record.level
        assert state.assessments[0].valid_until == record.valid_until
