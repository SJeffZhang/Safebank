"""Isolated temporary PostgreSQL 16 for obligations-data tests (make test-obligations).

Spawns a uniquely named, randomly ported container (no fixed subnet) so workers
can run concurrently, provisions the bank roles and database, runs the bank
migration twice on an empty database, and destroys the container on exit.
Skipped unless ``OBLIGATIONS_DB_TESTS=1``.
"""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
IMAGE = (
    "postgres:16.10-alpine@sha256:029660641a0cfc575b14f336ba448fb8a75fd595d42e1fa316b9fb4378742297"
)


@dataclass
class ObligationsDb:
    name: str
    admin_url: str
    runtime_url: str
    ro_url: str


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs)


def _provision() -> ObligationsDb:
    name = f"sbg-obligations-db-{os.getpid()}-{secrets.token_hex(4)}"
    port = _free_port()
    admin_pw = secrets.token_hex(16)
    app_pw = secrets.token_hex(16)
    ro_pw = secrets.token_hex(16)
    _run(
        [
            "docker",
            "run",
            "--rm",
            "-d",
            "--name",
            name,
            "--tmpfs",
            "/var/lib/postgresql/data:rw,noexec,nosuid,nodev,mode=0700",
            "-e",
            "PGDATA=/var/lib/postgresql/data",
            "-e",
            f"POSTGRES_PASSWORD={admin_pw}",
            "-p",
            f"127.0.0.1:{port}:5432",
            IMAGE,
        ]
    )
    try:
        sql = (
            "DO $$\n"
            "BEGIN\n"
            "  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'bank_app') THEN\n"
            f"    CREATE ROLE bank_app LOGIN PASSWORD '{app_pw}';\n"
            "  END IF;\n"
            "  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'bank_ro') THEN\n"
            f"    CREATE ROLE bank_ro LOGIN PASSWORD '{ro_pw}';\n"
            "  END IF;\n"
            "END\n"
            "$$;\n"
            "SELECT 'CREATE DATABASE bank_obligations_test OWNER postgres' WHERE NOT EXISTS\n"
            "  (SELECT FROM pg_database WHERE datname='bank_obligations_test')\\gexec\n"
            "REVOKE ALL ON DATABASE bank_obligations_test FROM PUBLIC;\n"
            "GRANT CONNECT ON DATABASE bank_obligations_test TO bank_app, bank_ro;\n"
        )
        for _ in range(60):
            result = subprocess.run(
                ["docker", "exec", "-i", name, "psql", "-U", "postgres", "-v", "ON_ERROR_STOP=1"],
                input=sql,
                text=True,
                capture_output=True,
            )
            if result.returncode == 0:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError(f"temporary postgres provisioning failed: {result.stderr}")

        admin_url = f"postgresql://postgres:{admin_pw}@127.0.0.1:{port}/bank_obligations_test"
        runtime_url = f"postgresql://bank_app:{app_pw}@127.0.0.1:{port}/bank_obligations_test"
        ro_url = f"postgresql://bank_ro:{ro_pw}@127.0.0.1:{port}/bank_obligations_test"
        return ObligationsDb(name=name, admin_url=admin_url, runtime_url=runtime_url, ro_url=ro_url)
    except BaseException:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        raise


def _migrate(db: ObligationsDb, revision: str) -> None:
    pythonpath = os.pathsep.join(
        str(ROOT / p)
        for p in ("packages/contracts/src", "packages/bankcore/src", "services/bank-backend")
    )
    env = {
        **os.environ,
        "BANK_MIGRATION_DATABASE_URL": db.admin_url,
        "PYTHONPATH": pythonpath + os.pathsep + str(ROOT),
    }
    cmd = [
        sys.executable,
        "-m",
        "alembic",
        "-c",
        "services/bank-backend/alembic.ini",
        "upgrade",
        revision,
    ]
    _run(cmd, cwd=ROOT, env=env)


@pytest.fixture(scope="session")
def obligations_db():
    if os.getenv("OBLIGATIONS_DB_TESTS") != "1":
        pytest.skip("requires isolated PostgreSQL; run make test-obligations")
    db = _provision()
    try:
        # Empty database, upgrade head twice must both succeed.
        _migrate(db, "head")
        _migrate(db, "head")
        yield db
    finally:
        subprocess.run(["docker", "rm", "-f", db.name], capture_output=True)


@pytest.fixture(scope="session")
def obligations_0002_db():
    if os.getenv("OBLIGATIONS_DB_TESTS") != "1":
        pytest.skip("requires isolated PostgreSQL; run make test-obligations")
    db = _provision()
    try:
        _migrate(db, "0002_identity")
        yield db
    finally:
        subprocess.run(["docker", "rm", "-f", db.name], capture_output=True)


@pytest.fixture(scope="session")
def obligations_0003_db():
    if os.getenv("OBLIGATIONS_DB_TESTS") != "1":
        pytest.skip("requires isolated PostgreSQL; run make test-obligations")
    db = _provision()
    try:
        _migrate(db, "0003_obligations")
        yield db
    finally:
        subprocess.run(["docker", "rm", "-f", db.name], capture_output=True)
