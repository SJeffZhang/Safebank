"""C4 split write (create_split_request) + read (get_split_status) real PG/HTTP tests.

Uses the real obligations fixtures (install_core) against a tmpfs Postgres
database served via HTTP: the read pattern from tests/obligations_data/test_reads.py
and the prepare/execute pattern from tests/obligations_data/test_obligations_data.py.
Run with make test-obligations (OBLIGATIONS_DB_TESTS=1).
"""

import asyncio
import socket
import threading

import httpx
import pytest
import uvicorn
from app.db import engine
from app.main import create_app
from safebank_contracts.enums import Risk
from safebank_contracts.plan import Execute, InteractiveEvidence, Prepare, SnapshotRequest
from safebank_contracts.state import BankState
from sqlalchemy import text as sa_text
from sqlalchemy.pool import NullPool

from tests.contracts.samples import step
from tests.obligations_data.fixtures import KEY, NOW, install_core

SERVICE_TOKEN = "obligations-test-token"
DEADLINE = "2099-01-01T00:00:00Z"

HEADERS = {"X-Service-Token": SERVICE_TOKEN}


@pytest.fixture(autouse=True)
def clean_database(obligations_db):
    """Truncate every application table before each test for an isolated dataset."""

    async def _truncate():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        async with admin.begin() as conn:
            from app import models as m

            names = ",".join(m.metadata.tables)
            await conn.execute(sa_text(f"TRUNCATE {names} CASCADE"))
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


def _green_evidence(plan_hash: str) -> InteractiveEvidence:
    return InteractiveEvidence(
        type="INTERACTIVE",
        origin="agent",
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


def _split_steps(
    total_cents=10000,
    participant_payee_ids=("pye_zs_mom", "pye_zs_wwu", "pye_zs_zl"),
    include_self=False,
    account_id="acc_zs_chk",
):
    return [
        step(
            tool="create_split_request",
            account_id=account_id,
            total_cents=total_cents,
            participant_payee_ids=list(participant_payee_ids),
            include_self=include_self,
            memo=None,
        )
    ]


async def _prepare_execute(
    client, plan_id, plan_hash, steps, *, expected_versions, clock_version=1
):
    """Prepare then execute a plan; return the execute JSON body."""
    evidence = _green_evidence(plan_hash)
    prepare_body = Prepare(
        customer_id="cus_zs",
        plan_id=plan_id,
        attempt=1,
        plan_hash=plan_hash,
        steps=steps,
        deadline_wall=DEADLINE,
        deadline_cap_wall=DEADLINE,
        authorization_evidence=evidence,
    )
    execute_body = Execute(
        customer_id="cus_zs",
        plan_id=plan_id,
        attempt=1,
        plan_hash=plan_hash,
        steps=steps,
        expected_versions=expected_versions,
        clock_version=clock_version,
        authorization_evidence=evidence,
    )
    for _ in range(40):
        try:
            prepared = await client.post(
                "/internal/plans/prepare",
                json=prepare_body.model_dump(mode="json"),
                headers=HEADERS,
                timeout=2.0,
            )
            break
        except httpx.TransportError:
            await asyncio.sleep(0.2)
    else:
        raise RuntimeError("uvicorn listener never became ready")
    assert prepared.status_code == 200, prepared.text
    executed = await client.post(
        "/internal/plans/execute",
        json=execute_body.model_dump(mode="json"),
        headers=HEADERS,
        timeout=10.0,
    )
    assert executed.status_code == 200, executed.text
    return executed.json()


async def _read(client, args, customer_id="cus_zs"):
    return await client.post(
        "/internal/read/get_split_status",
        json={"args": args, "customer_id": customer_id},
        headers=HEADERS,
        timeout=10.0,
    )


async def _snapshot(client, steps, customer_id="cus_zs"):
    snapshot = SnapshotRequest(customer_id=customer_id, steps=steps, event_id=None)
    return await client.post(
        "/internal/snapshot", json=snapshot.model_dump(mode="json"), headers=HEADERS, timeout=10.0
    )


def test_split_execute_rows_and_zero_ledger_change(obligations_db):
    """Execute 10000/3 over 3 payees → items 3334+3333+3333; balances and ledger untouched."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        steps = _split_steps()
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            body = await _prepare_execute(
                client, "pln_split_1", "hash_split_rows", steps, expected_versions={"cus_zs": 0}
            )
            assert body["status"] == "EXECUTED", body
            assert body["replayed"] is False
            snapshot = await _snapshot(client, steps)
        assert snapshot.status_code == 200, snapshot.text
        state = BankState.model_validate_json(snapshot.text)
        acc = state.accounts["acc_zs_chk"]
        # No funds move: balance and posted ledger positions are unchanged.
        assert acc.balance_cents == 600_000
        assert acc.available_cents == 600_000
        assert acc.debits_posted_cents == 0
        assert acc.credits_posted_cents == 600_000

        async with admin.connect() as conn:
            rows = (
                await conn.execute(
                    sa_text(
                        "SELECT total_cents, self_share_cents, include_self, account_id "
                        "FROM split_requests"
                    )
                )
            ).fetchall()
            assert len(rows) == 1
            total, self_share, include_self, account_id = rows[0]
            assert total == 10000
            assert self_share == 0
            assert include_self is False
            assert account_id == "acc_zs_chk"
            items = (
                await conn.execute(
                    sa_text(
                        "SELECT payee_id, amount_cents, status "
                        "FROM split_items ORDER BY amount_cents"
                    )
                )
            ).fetchall()
            assert [i[1] for i in items] == [3333, 3333, 3334]
            assert {i[0] for i in items} == {"pye_zs_mom", "pye_zs_wwu", "pye_zs_zl"}
            assert all(i[2] == "UNPAID" for i in items)
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_split_retry_20_times_single_request(obligations_db):
    """Re-executing the same committed plan 20 times must not duplicate the request/items."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        steps = _split_steps()
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            for _ in range(20):
                body = await _prepare_execute(
                    client,
                    "pln_split_1",
                    "hash_split_retry",
                    steps,
                    expected_versions={"cus_zs": 0},
                )
                assert body["status"] == "EXECUTED", body

        async with admin.connect() as conn:
            assert (await conn.scalar(sa_text("SELECT count(*) FROM split_requests"))) == 1
            assert (await conn.scalar(sa_text("SELECT count(*) FROM split_items"))) == 3
            assert (
                await conn.scalar(
                    sa_text(
                        "SELECT count(*) FROM plan_executions "
                        "WHERE customer_id='cus_zs' AND plan_id='pln_split_1'"
                    )
                )
                == 1
            )
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_split_cross_customer_rejected_without_rows(obligations_db):
    """Another customer's account or payee is rejected and leaves no split rows behind."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
            from app import models as m

            # A payee genuinely owned by cus_ls, invisible to cus_zs's loaded state.
            await conn.execute(
                m.payees.insert().values(
                    id="pye_ls_other",
                    customer_id="cus_ls",
                    name="李四他行收款人",
                    alias=None,
                    phone=None,
                    bank_name="他行",
                    account_no="6222020000000055",
                    target_account_id=None,
                    target_fingerprint="fp_ls_other",
                    created_at=NOW,
                )
            )
        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            # 1) Another customer's account → NOT_FOUND (account not in cus_zs scope).
            steps = _split_steps(account_id="acc_ls_chk")
            body = await _prepare_execute(
                client,
                "pln_split_x_acct",
                "hash_split_xacct",
                steps,
                expected_versions={"cus_zs": 0},
            )
            assert body["status"] == "FAILED", body
            assert body["error_code"] == "NOT_FOUND", body

            # 2) Another customer's payee → PAYEE_NOT_FOUND (not in cus_zs payees).
            steps = _split_steps(participant_payee_ids=("pye_zs_mom", "pye_ls_other"))
            body = await _prepare_execute(
                client,
                "pln_split_x_payee",
                "hash_split_xpayee",
                steps,
                expected_versions={"cus_zs": 0},
            )
            assert body["status"] == "FAILED", body
            assert body["error_code"] == "PAYEE_NOT_FOUND", body

        async with admin.connect() as conn:
            assert (await conn.scalar(sa_text("SELECT count(*) FROM split_requests"))) == 0
            assert (await conn.scalar(sa_text("SELECT count(*) FROM split_items"))) == 0
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_get_split_status_by_id_pagination_and_404(obligations_db):
    """By-id returns own request with correct outstanding; no-id paginates stably; foreign 404."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        steps_a = _split_steps()
        steps_b = _split_steps(total_cents=500, participant_payee_ids=("pye_zs_mom", "pye_zs_wwu"))
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            body_a = await _prepare_execute(
                client, "pln_split_a", "hash_split_a", steps_a, expected_versions={"cus_zs": 0}
            )
            body_b = await _prepare_execute(
                client, "pln_split_b", "hash_split_b", steps_b, expected_versions={"cus_zs": 1}
            )
            req_a = body_a["steps"][0]["created_ids"]["split_request_id"]
            req_b = body_b["steps"][0]["created_ids"]["split_request_id"]
            assert req_a != req_b

            # By id: own request with outstanding = total - self_share - received.
            r = await _read(client, {"request_id": req_a, "limit": 50})
            assert r.status_code == 200, r.text
            items = r.json()["items"]
            assert len(items) == 1
            view = items[0]
            assert view["id"] == req_a
            assert view["total_cents"] == 10000
            assert view["self_share_cents"] == 0
            assert view["received_cents"] == 0
            assert view["outstanding_cents"] == 10000
            assert sorted(i["amount_cents"] for i in view["items"]) == [3333, 3333, 3334]

            # No id: stable pagination over both requests.
            r1 = await _read(client, {"request_id": None, "limit": 1})
            assert r1.status_code == 200, r1.text
            b1 = r1.json()
            assert len(b1["items"]) == 1
            assert b1["next_cursor"] is not None
            r2 = await _read(client, {"request_id": None, "limit": 50, "cursor": b1["next_cursor"]})
            assert r2.status_code == 200, r2.text
            b2 = r2.json()
            assert len(b2["items"]) == 1
            assert b2["next_cursor"] is None
            assert {b1["items"][0]["id"], b2["items"][0]["id"]} == {req_a, req_b}

            # Another customer asking for cus_zs's request → 404.
            r = await _read(client, {"request_id": req_a, "limit": 50}, customer_id="cus_ls")
            assert r.status_code == 404, r.text
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())


def test_split_preflight_and_post_execution_consistent(obligations_db):
    """Snapshot before execution shows no splits; after execution the reload matches the read."""

    async def check():
        admin = engine(obligations_db.admin_url, poolclass=NullPool)
        runtime = engine(obligations_db.runtime_url, poolclass=NullPool)
        async with admin.begin() as conn:
            await install_core(conn)
        app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
        port = free_port()
        _serve(app, port)
        steps = _split_steps()
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            pre = await _snapshot(client, steps)
            assert pre.status_code == 200, pre.text
            assert BankState.model_validate_json(pre.text).splits == ()

            body = await _prepare_execute(
                client, "pln_split_1", "hash_split_consist", steps, expected_versions={"cus_zs": 0}
            )
            req = body["steps"][0]["created_ids"]["split_request_id"]

            r = await _read(client, {"request_id": req, "limit": 50})
            assert r.status_code == 200, r.text
            view = r.json()["items"][0]

            post = await _snapshot(client, steps)
        assert post.status_code == 200, post.text
        state = BankState.model_validate_json(post.text)
        assert len(state.splits) == 1
        loaded = state.splits[0]
        assert loaded.id == req
        assert loaded.total_cents == view["total_cents"] == 10000
        assert loaded.self_share_cents == view["self_share_cents"] == 0
        assert loaded.received_cents == view["received_cents"] == 0
        assert loaded.outstanding_cents == view["outstanding_cents"] == 10000
        assert [i.amount_cents for i in loaded.items] == [i["amount_cents"] for i in view["items"]]
        await admin.dispose()
        await runtime.dispose()

    asyncio.run(check())
