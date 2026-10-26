"""E0b T1: the evaluation seed loader writes into the real bank backend.

The loader must load the seed into a real tmpfs PostgreSQL (never FakeL3),
recompute ``target_fingerprint`` with the real fingerprint algorithm, expose
every seeded customer / account / payee / card / transaction through the real
L3 snapshot and read interfaces, refuse a second load on a non-empty database,
and fail closed before writing anything when an entity reference is missing.
"""

from __future__ import annotations

import asyncio
import copy
import socket
import threading
from pathlib import Path

import httpx
import pytest
import uvicorn
from app import models as m
from app.db import engine as bank_engine
from app.main import create_app
from app.state import fingerprint
from safebank_contracts.plan import SnapshotRequest
from sqlalchemy import text
from sqlalchemy.pool import NullPool

from evaluation.runner.seed import SeedError, load_seed, read_seed
from services.gateway.app.l3.client import L3Client
from tests.obligations_data.fixtures import KEY

SERVICE_TOKEN = "e0b-t1-test-token"
FIXTURE = Path(__file__).parent / "fixtures" / "mini_seed.json"

# A transfer to the internal payee brings cus_ww into the snapshot scope, so the
# redaction path (other customers' contact details are never leaked) is exercised.
_TRANSFER_TO_WW = {
    "step_id": "s1",
    "tool": "transfer",
    "args": {
        "from_account_id": "acc_zs_chk",
        "payee_id": "pye_zs_ww",
        "to_account_id": None,
        "amount_cents": 80000,
        "memo": None,
    },
    "depends_on": (),
    "arg_sources": (),
}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _serve(app, port):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()


async def _truncate(admin):
    names = ",".join(m.metadata.tables)
    async with admin.begin() as conn:
        await conn.execute(text(f"TRUNCATE {names} CASCADE"))


async def _serve_l3(obligations_db, seed):
    admin = bank_engine(obligations_db.admin_url, poolclass=NullPool)
    runtime = bank_engine(obligations_db.runtime_url, poolclass=NullPool)
    await _truncate(admin)
    async with admin.connect() as conn:
        await load_seed(conn, seed, fingerprint_key=KEY)
    app = create_app(db=runtime, service_token=SERVICE_TOKEN, fingerprint_key=KEY)
    port = free_port()
    _serve(app, port)
    base = f"http://127.0.0.1:{port}"
    l3 = L3Client(base, SERVICE_TOKEN)
    async with httpx.AsyncClient(base_url=base) as client:
        for _ in range(40):
            try:
                probe = await client.get(
                    "/readyz", headers={"X-Service-Token": SERVICE_TOKEN}, timeout=2.0
                )
                break
            except httpx.TransportError:
                await asyncio.sleep(0.2)
        else:
            raise RuntimeError("uvicorn listener never became ready")
        assert probe.status_code == 200
    return admin, runtime, l3


async def _read(l3, tool, customer_id, args):
    return await l3.read(tool, {"customer_id": customer_id, "args": args})


def test_load_seed_then_real_snapshot_and_reads(obligations_db):
    seed = read_seed(FIXTURE)

    async def run():
        admin, runtime, l3 = await _serve_l3(obligations_db, seed)
        try:
            snapshot = await l3.snapshot(
                SnapshotRequest(
                    customer_id="cus_zs", steps=(_TRANSFER_TO_WW,), event_id=None
                ).model_dump(mode="json")
            )
            assert snapshot["dataset_revision"] == "mini_seed_v1.0"
            assert snapshot["customers"]["cus_zs"]["display_name"] == "张三"
            assert snapshot["customers"]["cus_zs"]["phone"] == "13800000001"
            # Other customers' contact details are redacted, never leaked.
            assert snapshot["customers"]["cus_ww"]["phone"] == ""
            account = snapshot["accounts"]["acc_zs_chk"]
            assert account["credits_posted_cents"] == 780000
            assert account["debits_posted_cents"] == 180000
            assert account["current_card_id"] == "crd_zs_1"
            assert snapshot["cards"]["crd_zs_1"]["single_limit_cents"] == 500000
            assert snapshot["payees"]["pye_zs_ww"]["target_fingerprint"] == fingerprint(
                "6222020000000201", KEY
            )
            txns = {t["id"]: t for t in snapshot["transactions"]}
            assert txns["txn_zs_003"]["amount_cents"] == 180000
            assert "6222020000009999" in txns["txn_zs_003"]["memo"]

            accounts = await _read(l3, "get_accounts", "cus_zs", {"history_days": 0})
            by_id = {a["id"]: a for a in accounts["accounts"]}
            assert by_id["acc_zs_chk"]["balance_cents"] == 600000
            assert isinstance(by_id["acc_zs_chk"]["balance_cents"], int)
            assert by_id["acc_zs_chk"]["account_no_masked"] == "****0101"

            payees = await _read(l3, "list_payees", "cus_zs", {"query": None})
            payee = {p["id"]: p for p in payees["items"]}["pye_zs_ww"]
            assert payee["target_fingerprint"] == fingerprint("6222020000000201", KEY)
            assert payee["is_internal"] is True

            cards = await _read(l3, "get_cards", "cus_zs", {"account_id": None})
            card = {c["id"]: c for c in cards["items"]}["crd_zs_1"]
            assert card["single_limit_cents"] == 500000
            assert card["status"] == "ACTIVE"

            history = await _read(
                l3,
                "get_transactions",
                "cus_zs",
                {
                    "from_at": "2026-08-01T00:00:00+08:00",
                    "to_at": "2026-11-01T00:00:00+08:00",
                    "account_id": None,
                    "category": None,
                    "merchant_id": None,
                    "kind": None,
                },
            )
            txn = {t["id"]: t for t in history["items"]}["txn_zs_003"]
            assert txn["amount_cents"] == 180000
            assert "6222020000009999" in txn["memo"]
        finally:
            await admin.dispose()
            await runtime.dispose()
            await l3.close()

    asyncio.run(run())


def test_reload_refuses_non_empty_database(obligations_db):
    seed = read_seed(FIXTURE)

    async def run():
        admin = bank_engine(obligations_db.admin_url, poolclass=NullPool)
        await _truncate(admin)
        async with admin.connect() as conn:
            await load_seed(conn, seed, fingerprint_key=KEY)
        with pytest.raises(SeedError):
            async with admin.connect() as conn:
                await load_seed(conn, seed, fingerprint_key=KEY)
        await admin.dispose()

    asyncio.run(run())


def test_missing_reference_fails_closed_leaves_db_empty(obligations_db):
    seed = copy.deepcopy(read_seed(FIXTURE))
    seed["transactions"][0]["payee_id"] = "pye_nonexistent"

    async def run():
        admin = bank_engine(obligations_db.admin_url, poolclass=NullPool)
        await _truncate(admin)
        with pytest.raises(SeedError):
            async with admin.connect() as conn:
                await load_seed(conn, seed, fingerprint_key=KEY)
        async with admin.connect() as conn:
            count = await conn.scalar(text("SELECT count(*) FROM customers"))
            assert count == 0
        await admin.dispose()

    asyncio.run(run())
