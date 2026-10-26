"""E0b T7b: CLI entry point for L1 stability evaluation.

Usage:
    make eval-l1 LIMIT=20
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

# Ensure shared packages are importable when run outside pytest.
_project_root = Path(__file__).resolve().parent.parent.parent
for _p in [
    str(_project_root),
    str(_project_root / "packages" / "contracts" / "src"),
    str(_project_root / "packages" / "bankcore" / "src"),
    str(_project_root / "services" / "bank-backend"),
]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from evaluation.metrics.compute import compute_metrics, write_outputs  # noqa: E402
from evaluation.runner.harness import eval_harness  # noqa: E402
from evaluation.runner.loop import StopRun, run_corpus  # noqa: E402
from services.agent.app.graph import Understanding  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CORPUS_DIR = PROJECT_ROOT / "evaluation" / "corpus" / "l1_stability"
SEED_PATH = PROJECT_ROOT / "evaluation" / "fixtures" / "l1_seed_v1.json"

IMAGE = (
    "postgres:16.10-alpine@sha256:029660641a0cfc575b14f336ba448fb8a75fd595d42e1fa316b9fb4378742297"
)


# ── tmpfs PostgreSQL provisioning (adapted from tests/*/conftest.py) ────────────


@dataclass
class _GatewayDb:
    name: str
    admin_url: str
    runtime_url: str


@dataclass
class _ObligationsDb:
    name: str
    admin_url: str
    runtime_url: str
    ro_url: str


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs)


def _provision_gateway_db() -> _GatewayDb:
    """Provision a tmpfs PostgreSQL for gateway tests.

    Adapted from tests/gateway/db/conftest.py:_provision.
    """
    name = f"sbg-eval-gw-{os.getpid()}-{secrets.token_hex(4)}"
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
        for _ in range(60):
            result = subprocess.run(
                [
                    "docker",
                    "exec",
                    "-i",
                    name,
                    "psql",
                    "-U",
                    "postgres",
                    "-v",
                    "ON_ERROR_STOP=1",
                ],
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
            str(PROJECT_ROOT / p)
            for p in ("packages/contracts/src", "packages/bankcore/src", "services/gateway")
        )
        env = {
            **os.environ,
            "GATEWAY_MIGRATION_DATABASE_URL": admin_url,
            "PYTHONPATH": pythonpath + os.pathsep + str(PROJECT_ROOT),
        }
        cmd = [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "services/gateway/alembic.ini",
            "upgrade",
            "head",
        ]
        _run(cmd, cwd=PROJECT_ROOT, env=env)
        _run(cmd, cwd=PROJECT_ROOT, env=env)
        return _GatewayDb(name=name, admin_url=admin_url, runtime_url=runtime_url)
    except BaseException:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        raise


def _provision_obligations_db() -> _ObligationsDb:
    """Provision a tmpfs PostgreSQL for bank-backend tests.

    Adapted from tests/obligations_data/conftest.py:_provision.
    """
    name = f"sbg-eval-obl-{os.getpid()}-{secrets.token_hex(4)}"
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
                [
                    "docker",
                    "exec",
                    "-i",
                    name,
                    "psql",
                    "-U",
                    "postgres",
                    "-v",
                    "ON_ERROR_STOP=1",
                ],
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
        return _ObligationsDb(
            name=name, admin_url=admin_url, runtime_url=runtime_url, ro_url=ro_url
        )
    except BaseException:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        raise


def _migrate_obligations(db: _ObligationsDb, revision: str = "head") -> None:
    """Migrate bank-backend database.

    Adapted from tests/obligations_data/conftest.py:_migrate.
    """
    pythonpath = os.pathsep.join(
        str(PROJECT_ROOT / p)
        for p in ("packages/contracts/src", "packages/bankcore/src", "services/bank-backend")
    )
    env = {
        **os.environ,
        "BANK_MIGRATION_DATABASE_URL": db.admin_url,
        "PYTHONPATH": pythonpath + os.pathsep + str(PROJECT_ROOT),
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
    _run(cmd, cwd=PROJECT_ROOT, env=env)


# ── helpers ────────────────────────────────────────────────────────────────────


def _load_cases(
    limit: int | None,
    sample_ids: list[str] | None,
    filter_pat: str | None,
) -> list[dict[str, Any]]:
    """Load corpus cases from JSONL files, sorted by case_id."""
    cases: list[dict[str, Any]] = []
    jsonl_files = sorted(CORPUS_DIR.glob("*.jsonl"))
    for fp in jsonl_files:
        for line in fp.read_text(encoding="utf-8").strip().splitlines():
            if not line:
                continue
            case = json.loads(line)
            cases.append(case)

    if sample_ids:
        id_set = set(sample_ids)
        cases = [c for c in cases if c.get("case_id", "") in id_set]

    if filter_pat:
        pattern = filter_pat.lower()
        cases = [
            c
            for c in cases
            if pattern in c.get("case_id", "").lower() or pattern in c.get("category", "").lower()
        ]

    if limit is not None and limit > 0:
        cases = cases[:limit]

    return cases


def _fake_ports_factory(case: dict[str, Any]) -> dict[str, Any]:
    """Fake LLM ports: evaluate-only graph stops at real G4, no live model."""
    return dict(
        understand=lambda s, c: Understanding(intents=[], needs_write=False),
        gather=lambda s, c: [],
        plan=lambda s, c: None,  # never called when needs_write=False
        respond=lambda s, c: "ok",
        verify_output=lambda s, c: "ok",
    )


def _read_seed_version() -> str:
    """Read the dataset_revision from the seed file."""
    try:
        raw = json.loads(SEED_PATH.read_text(encoding="utf-8"))
        return str(raw.get("dataset_revision", "unknown"))
    except Exception:
        return "unknown"


def _fmt_ts() -> str:
    """UTC timestamp for default output directory."""
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _build_meta(llm_mode: str, case_count: int, runs: list[Any]) -> dict[str, str]:
    """Build meta dict for report output."""
    return {
        "llm_mode": llm_mode,
        "model": "fake" if llm_mode == "fake" else llm_mode,
        "l3": "real-tmpfs",
        "seed_version": _read_seed_version(),
        "corpus_version": "v1",
        "sample_count": str(case_count),
    }


# ── async main ────────────────────────────────────────────────────────────────


async def _run_eval(args: argparse.Namespace) -> int:
    """Provision real tmpfs PG, run evaluation, clean up."""
    cases = _load_cases(
        limit=args.limit,
        sample_ids=args.sample_ids,
        filter_pat=args.filter_pat,
    )
    if not cases:
        print("没有匹配的语料用例", file=sys.stderr)
        return 1

    gw_db = _provision_gateway_db()
    try:
        obl_db = _provision_obligations_db()
        _migrate_obligations(obl_db, "head")
        _migrate_obligations(obl_db, "head")
    except BaseException:
        subprocess.run(["docker", "rm", "-f", gw_db.name], capture_output=True)
        raise

    try:
        mp = pytest.MonkeyPatch()
        try:
            async with eval_harness(gw_db, obl_db, str(SEED_PATH), mp) as h:
                try:
                    runs = await run_corpus(
                        h,
                        cases,
                        ports_factory=_fake_ports_factory,
                        repeat=args.repeat,
                        customer_id="cus_zs",
                    )
                except StopRun as exc:
                    print(f"评测中断: {exc}", file=sys.stderr)
                    runs = exc.runs
                    if runs:
                        print(
                            f"已保留 {len(runs)} 条已完成结果",
                            file=sys.stderr,
                        )
                    metrics = compute_metrics(runs)
                    out_dir = (
                        Path(args.out_dir)
                        if args.out_dir
                        else PROJECT_ROOT / "evaluation" / "reports" / _fmt_ts()
                    )
                    meta = _build_meta(args.llm_mode, len(cases), runs)
                    write_outputs(out_dir, runs, metrics, meta)
                    _print_summary(out_dir, len(cases), args.repeat, runs)
                    return 3
        finally:
            mp.undo()
    finally:
        subprocess.run(["docker", "rm", "-f", gw_db.name], capture_output=True)
        subprocess.run(["docker", "rm", "-f", obl_db.name], capture_output=True)

    metrics = compute_metrics(runs)
    out_dir = (
        Path(args.out_dir) if args.out_dir else PROJECT_ROOT / "evaluation" / "reports" / _fmt_ts()
    )
    meta = _build_meta(args.llm_mode, len(cases), runs)
    write_outputs(out_dir, runs, metrics, meta)
    _print_summary(out_dir, len(cases), args.repeat, runs)
    return 0


def _print_summary(out_dir: Path, case_count: int, repeat: int, runs: list[Any]) -> None:
    print(f"已写入 {out_dir}")
    print(f"  用例: {case_count} 条 x repeat={repeat} -> {len(runs)} 条 CaseRun")
    print("  metrics.json | report.md | cases.jsonl")


# ── CLI entry point ───────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="E0b L1 Stability Evaluation Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Modes:
  fake    — fake LLM ports, real gateway + L3 + tmpfs PostgreSQL.
  replay  — replay from recorded LLM responses (not yet connected).
  live    — call DeepSeek V4 Pro for each case (requires DEEPSEEK_API_KEY).
""",
    )
    parser.add_argument(
        "--llm-mode",
        choices=("fake", "replay", "live"),
        default="fake",
        help="LLM mode: fake | replay | live (default: fake)",
    )
    parser.add_argument("--limit", type=int, default=None, help="Max cases to run (default: all)")
    parser.add_argument(
        "--repeat",
        type=int,
        default=3,
        help="Repeat each case N times (default: 3)",
    )
    parser.add_argument("--sample-ids", nargs="*", default=None, help="Run only these case IDs")
    parser.add_argument(
        "--filter",
        dest="filter_pat",
        default=None,
        help="Filter cases by substring in case_id or category",
    )
    parser.add_argument(
        "--out",
        dest="out_dir",
        default=None,
        help="Output directory (default: evaluation/reports/<UTC timestamp>)",
    )

    args = parser.parse_args(argv)

    # ── live guard ──────────────────────────────────────────────────────────
    if args.llm_mode == "live":
        if not os.environ.get("DEEPSEEK_API_KEY"):
            print("live 需要设置 DEEPSEEK_API_KEY 环境变量", file=sys.stderr)
            return 2
        print("live 由协调者在后续任务接入", file=sys.stderr)
        return 2

    if args.llm_mode == "replay":
        print("replay 模式尚未接入，后续任务实现", file=sys.stderr)
        return 2

    # ── fake mode (real gateway + L3 + tmpfs PG) ──────────────────────────
    return asyncio.run(_run_eval(args))


if __name__ == "__main__":
    sys.exit(main())
