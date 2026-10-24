"""C1d agent PostgreSQL fixtures.

Spawns an isolated temporary PostgreSQL container (tmpfs, no persistent volume)
and provides an ``agent_pg`` fixture with database URLs and a pre-setup
AsyncPostgresSaver.

Skipped unless ``AGENT_DB_TESTS=1``.
"""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
IMAGE = (
    "postgres:16.10-alpine@sha256:029660641a0cfc575b14f336ba448fb8a75fd595d42e1fa316b9fb4378742297"
)


@dataclass
class AgentPg:
    name: str
    admin_url: str
    runtime_url: str  # postgresql://agent_app:pw@host:port/agent


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs)


def _provision() -> AgentPg:
    name = f"sbg-agent-db-{os.getpid()}-{secrets.token_hex(4)}"
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
            "  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'agent_app') THEN\n"
            f"    CREATE ROLE agent_app LOGIN PASSWORD '{app_pw}';\n"
            "  END IF;\n"
            "END\n"
            "$$;\n"
            "SELECT 'CREATE DATABASE agent' WHERE NOT EXISTS\n"
            "  (SELECT FROM pg_database WHERE datname='agent')\\gexec\n"
        )
        db_sql = (
            "REVOKE ALL ON DATABASE agent FROM PUBLIC;\n"
            "GRANT CONNECT ON DATABASE agent TO agent_app;\n"
            "GRANT CREATE ON SCHEMA public TO agent_app;\n"
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

        # Run schema grants against the agent database
        for _ in range(10):
            result = subprocess.run(
                ["docker", "exec", "-i", name, "psql", "-U", "postgres", "-d", "agent", "-v", "ON_ERROR_STOP=1"],
                input=db_sql,
                text=True,
                capture_output=True,
            )
            if result.returncode == 0:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError(f"agent db schema grants failed: {result.stderr}")

        admin_url = f"postgresql://postgres:{admin_pw}@127.0.0.1:{port}/agent"
        runtime_url = f"postgresql://agent_app:{app_pw}@127.0.0.1:{port}/agent"
        return AgentPg(name=name, admin_url=admin_url, runtime_url=runtime_url)
    except BaseException:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        raise


@pytest.fixture(scope="session")
def agent_pg():
    if os.getenv("AGENT_DB_TESTS") != "1":
        pytest.skip("requires isolated PostgreSQL; run make test-agent-db")
    db = _provision()
    try:
        yield db
    finally:
        subprocess.run(["docker", "rm", "-f", db.name], capture_output=True)