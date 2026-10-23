"""Isolated temporary PostgreSQL 16 for gateway DB tests (make test-gateway-db).

Spawns a uniquely named, randomly ported container (no fixed subnet) so
multiple workers can run concurrently, runs the gateway migration twice on an
empty database, and destroys the container on exit. Skipped unless
``GATEWAY_DB_TESTS=1``.
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

ROOT = Path(__file__).resolve().parents[3]
IMAGE = (
    "postgres:16.10-alpine@sha256:029660641a0cfc575b14f336ba448fb8a75fd595d42e1fa316b9fb4378742297"
)


@dataclass
class GatewayDb:
    name: str
    admin_url: str
    runtime_url: str


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs)


def _provision(revision: str = "head") -> GatewayDb:
    name = f"sbg-gateway-db-{os.getpid()}-{secrets.token_hex(4)}"
    port = _free_port()
    admin_pw = secrets.token_hex(16)
    app_pw = secrets.token_hex(16)
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
            "  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'gateway_app') THEN\n"
            f"    CREATE ROLE gateway_app LOGIN PASSWORD '{app_pw}';\n"
            "  END IF;\n"
            "  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'gateway_admin') THEN\n"
            "    CREATE ROLE gateway_admin;\n"
            "  END IF;\n"
            "END\n"
            "$$;\n"
            "SELECT 'CREATE DATABASE gateway_test' WHERE NOT EXISTS\n"
            "  (SELECT FROM pg_database WHERE datname='gateway_test')\\gexec\n"
            "REVOKE ALL ON DATABASE gateway_test FROM PUBLIC;\n"
            "GRANT CONNECT ON DATABASE gateway_test TO gateway_app, gateway_admin;\n"
        )
        # The official entrypoint runs a temporary server during first init and
        # briefly restarts it afterwards; retry the idempotent provisioning SQL
        # until the real server is listening.
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

        admin_url = f"postgresql://postgres:{admin_pw}@127.0.0.1:{port}/gateway_test"
        runtime_url = f"postgresql://gateway_app:{app_pw}@127.0.0.1:{port}/gateway_test"
        pythonpath = os.pathsep.join(
            str(ROOT / p)
            for p in ("packages/contracts/src", "packages/bankcore/src", "services/gateway")
        )
        env = {
            **os.environ,
            "GATEWAY_MIGRATION_DATABASE_URL": admin_url,
            "PYTHONPATH": pythonpath + os.pathsep + str(ROOT),
        }
        cmd = [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "services/gateway/alembic.ini",
            "upgrade",
            revision,
        ]
        # Empty database, upgrade head twice must both succeed.
        _run(cmd, cwd=ROOT, env=env)
        _run(cmd, cwd=ROOT, env=env)
        return GatewayDb(name=name, admin_url=admin_url, runtime_url=runtime_url)
    except BaseException:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        raise


@pytest.fixture(scope="session")
def gateway_db():
    if os.getenv("GATEWAY_DB_TESTS") != "1":
        pytest.skip("requires isolated PostgreSQL; run make test-gateway-db")
    db = _provision()
    try:
        yield db
    finally:
        subprocess.run(["docker", "rm", "-f", db.name], capture_output=True)
