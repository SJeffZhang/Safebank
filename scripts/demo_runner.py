"""Portable, fail-closed demo observations and coordinator SQL-receipt verification.

This runner never resets databases. Replay additionally requires Docker-internal
agent networks and actual blocked external TCP probes, with L2/PG reachability.
Driver exit labels are observations; only matching coordinator SQL receipts can
mark a run business-verified. See docs/acceptance/demo-runner.md.
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import os
import re
import secrets
import signal
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from scripts.demo_environment import (
    DemoEnvironment,
    default_deploy,
    generation_names,
    private_write,
)

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "docs/handoffs/2026-10-30-tools"
DEFAULT_CASES = ("01", "16", "27", "34", "36", "23")
CASE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
OBSERVED_EXITS = frozenset({0, 1, 2, 3, 4, 8, 9, 10, 11, 13})
HEALTH_PROBE = """
import json,socket,urllib.request
with urllib.request.urlopen('http://gateway:8200/health',timeout=3) as r:
    assert r.status==200
with socket.create_connection(('postgres-agent',5432),timeout=3):
    pass
print(json.dumps({'gateway_reachable':True,'postgres_agent_reachable':True}))
"""
OFFLINE_PROBE = """
import json,socket
blocked=[]
for host in ('1.1.1.1','api.deepseek.com'):
    try:
        c=socket.create_connection((host,443),timeout=2)
    except OSError:
        blocked.append(host)
    else:
        c.close()
        raise SystemExit(9)
print(json.dumps({'external_tcp_blocked':blocked}))
"""


class DemoFailure(RuntimeError):
    def __init__(self, message: str, exit_code: int = 1):
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class Case:
    id: str
    customer_id: str
    text: str


def load_cases(path: Path, selected: list[str] | None) -> tuple[Case, ...]:
    cases: dict[str, Case] = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 3:
            raise DemoFailure("cases require exactly id, customer and typed text columns")
        id_, customer, text = parts
        if not CASE_ID.fullmatch(id_) or id_ in cases or not customer or not text:
            raise DemoFailure("invalid or duplicate case")
        cases[id_] = Case(id_, customer, text)
    ids = list(cases) if selected is None else selected
    if not ids or len(set(ids)) != len(ids) or any(x not in cases for x in ids):
        raise DemoFailure("selected case IDs must be nonempty, unique and present in cases file")
    return tuple(cases[x] for x in ids)


def require_internal_networks(networks: list[dict[str, Any]]) -> list[str]:
    if not networks or any(n.get("Internal") is not True or not n.get("Name") for n in networks):
        raise DemoFailure(
            "replay requires every agent network to be Docker-internal; "
            "coordinate a serial network switch retaining databases and volumes"
        )
    return [n["Name"] for n in networks]


def write_runtime_overlay(directory: Path, recordings: Path, mode: str, case_id: str) -> Path:
    if mode not in {"live", "record", "replay"} or not CASE_ID.fullmatch(case_id):
        raise DemoFailure("invalid runtime mode or case ID")
    environment = {
        "LLM_MODE": mode,
        "LLM_PROFILE": "B2",
        "LLM_CASE_ID": case_id,
        "LLM_REPLAY_ROOT": "/run/safebank-replay",
    }
    if mode == "replay":
        environment["DEEPSEEK_API_KEY"] = ""
    agent = {"environment": environment}
    if mode != "live":
        agent["volumes"] = [
            {
                "type": "bind",
                "source": str(recordings.resolve()),
                "target": "/run/safebank-replay",
                "read_only": mode != "record",
            }
        ]
    overlay = {"services": {"agent": agent}}
    path = directory / f"runtime-{case_id}.yml"
    private_write(path, yaml.safe_dump(overlay))
    return path


_SECRET_FIELD = re.compile(
    r"(?:authorization|cookie|password|secret|token|api.?key|^pin$|^otp$)", re.I
)
_SECRET_TEXT = re.compile(
    r"Bearer\s+[^\s\"']+|sk-[A-Za-z0-9_-]{16,}|"
    r"(?:pin|otp|password|api[_-]?key|token|secret)\s*[=:]\s*[^\s,;\"']+",
    re.I,
)


def sanitize_trace(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(k): "[REDACTED]" if _SECRET_FIELD.search(str(k)) else sanitize_trace(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [sanitize_trace(v) for v in value]
    if isinstance(value, str):
        return _SECRET_TEXT.sub("[REDACTED]", value)
    return value


def verify_receipts(manifest: dict, proof_directory: Path) -> dict:
    verified = copy.deepcopy(manifest)
    cases = verified.get("cases")
    selected = verified.get("selected_case_ids")
    if (
        verified.get("status") != "OBSERVED"
        or not isinstance(cases, list)
        or not cases
        or not isinstance(selected, list)
        or any(not isinstance(case, dict) for case in cases)
    ):
        raise DemoFailure("only a complete OBSERVED run can be verified")
    actual = [case.get("case_id") for case in cases]
    if (
        actual != selected
        or any(not isinstance(id_, str) or not CASE_ID.fullmatch(id_) for id_ in actual)
        or len(set(actual)) != len(actual)
    ):
        raise DemoFailure("observations do not contain every selected case exactly once in order")
    for case in verified["cases"]:
        try:
            receipt = json.loads((proof_directory / f"{case['case_id']}.json").read_text())
        except (OSError, ValueError) as exc:
            raise DemoFailure("missing or invalid SQL receipt") from exc
        expected = {
            **{k: verified[k] for k in ("run_id", "checkout_sha", "generation")},
            "case_id": case["case_id"],
            "trace_sha256": case["trace_sha256"],
        }
        if (
            not isinstance(receipt, dict)
            or any(receipt.get(k) != v for k, v in expected.items())
            or receipt.get("verified") is not True
            or receipt.get("verifier") != "SQL"
            or not isinstance(receipt.get("checks"), dict)
            or not receipt["checks"]
            or any(v is not True for v in receipt["checks"].values())
        ):
            raise DemoFailure(
                "SQL receipt does not match this exact run, generation, SHA and trace"
            )
        case["business_verified"] = True
        case["sql_checks"] = list(receipt["checks"])
        case["proof_sha256"] = hashlib.sha256(
            (proof_directory / f"{case['case_id']}.json").read_bytes()
        ).hexdigest()
    verified["business_verified"] = True
    return verified


def run_command(
    argv: list[str], *, cwd: Path, env: dict[str, str], timeout: float = 60
) -> subprocess.CompletedProcess:
    """Keep stderr private and stop the whole driver group on a timeout."""
    with subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate(timeout=2)
            raise DemoFailure(
                "demo subprocess timed out; inspect current plan before resuming", 124
            ) from None
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


class DemoRunner:
    def __init__(self, demo: DemoEnvironment, command=run_command):
        self.demo = demo
        self.command = command
        self.env = {**os.environ, "COMPOSE_PROJECT_NAME": "safebank-e2e-1029"}
        self.files = list(demo.base)

    def checked(self, argv: list[str], *, timeout: float = 60, cwd: Path | None = None) -> str:
        result = self.command(argv, cwd=cwd or self.demo.deploy, env=self.env, timeout=timeout)
        if result.returncode:
            # Never echo raw output: Compose errors can include passwords/URLs.
            raise DemoFailure(f"demo operation failed (exit {result.returncode}); details withheld")
        return result.stdout

    def compose(self, *args: str, timeout: float = 60) -> str:
        return self.checked(["docker", "compose", *args], timeout=timeout)

    def metadata(self) -> dict:
        current = self.demo.current()
        if current is None:
            raise DemoFailure("no initialized generation; run make demo-reset separately")
        generation = current["generation"]
        try:
            generation_names(generation)
        except (TypeError, ValueError) as exc:
            raise DemoFailure("invalid current generation") from exc
        overlay = self.demo.state / generation / "compose.yml"
        if not overlay.is_file():
            raise DemoFailure("current generation overlay is missing")
        sha = self.checked(["git", "rev-parse", "HEAD"]).strip()
        source_sha = self.checked(["git", "rev-parse", "HEAD"], cwd=ROOT).strip()
        if not SHA.fullmatch(sha) or sha != source_sha:
            raise DemoFailure("runner and deployment must use the same exact full commit")
        if self.checked(["git", "status", "--porcelain", "--untracked-files=no"]).strip():
            raise DemoFailure("deployment tracked files contain uncommitted changes")
        self.files.append(str(overlay))
        self.env["COMPOSE_FILE"] = os.pathsep.join(self.files)
        # Recheck the active generation against runtime env, not only a state file.
        raw = self.compose(
            "exec",
            "-T",
            "bank-backend",
            "python",
            "-c",
            "import json,os;from urllib.parse import urlsplit;"
            "print(json.dumps({'database':urlsplit(os.environ['BANK_DATABASE_URL']).path[1:]}))",
        )
        if json.loads(raw).get("database") != f"bank_{generation}":
            raise DemoFailure("running bank database differs from selected generation")
        seed = self.demo.sql(
            "postgres-bank",
            f"bank_{generation}",
            "SELECT dataset_revision FROM clock WHERE id=1;",
        )
        return {"generation": generation, "checkout_sha": sha, "seed": seed}

    def offline_networks(self) -> list[str]:
        container_id = self.compose("ps", "-q", "agent").strip()
        if not container_id or "\n" in container_id:
            raise DemoFailure("exactly one running agent is required")
        inspected = json.loads(self.checked(["docker", "inspect", container_id]))
        if len(inspected) != 1 or inspected[0].get("HostConfig", {}).get("NetworkMode") in {
            "host",
            "none",
        }:
            raise DemoFailure("replay requires inspectable Docker-internal agent networks")
        attached = inspected[0].get("NetworkSettings", {}).get("Networks", {})
        if not attached:
            raise DemoFailure("replay requires inspectable Docker-internal agent networks")
        networks = json.loads(self.checked(["docker", "network", "inspect", *attached]))
        if {n.get("Name") for n in networks} != set(attached):
            raise DemoFailure("agent network inventory changed during inspection")
        return require_internal_networks(networks)

    def run(
        self,
        *,
        cases: tuple[Case, ...],
        mode: str,
        recordings: Path,
        run_directory: Path,
        observe_only: bool,
        proofs: Path | None = None,
    ) -> dict:
        if not observe_only and proofs is None:
            raise DemoFailure(
                "provide --proofs for verified mode or --observe-only for observations"
            )
        if mode not in {"live", "record", "replay"}:
            raise DemoFailure("invalid LLM mode")
        if mode == "record":
            recordings.mkdir(mode=0o700, parents=True, exist_ok=True)
        if mode != "live" and not recordings.is_dir():
            raise DemoFailure("recording directory is missing; replay never falls back live")
        if mode == "replay" and any(
            not (recordings / "B2" / case.id / "replay.jsonl").is_file() for case in cases
        ):
            raise DemoFailure("recording missing for selected case; no live fallback")
        metadata = self.metadata()
        network_names = self.offline_networks() if mode == "replay" else []
        run_directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        manifest = {
            **metadata,
            "run_id": run_directory.name,
            "mode": mode,
            "profile": "B2",
            "created_at": datetime.now(UTC).isoformat(),
            "business_verified": False,
            "status": "RUNNING",
            "selected_case_ids": [case.id for case in cases],
            "cases": [],
            "internal_networks": network_names,
        }
        private_write(run_directory / "manifest.json", json.dumps(manifest, indent=2) + "\n")

        def checkpoint() -> None:
            temporary = run_directory / "manifest.next.json"
            private_write(temporary, json.dumps(manifest, indent=2) + "\n")
            temporary.replace(run_directory / "manifest.json")

        try:
            for case in cases:
                if (
                    mode == "replay"
                    and not (recordings / "B2" / case.id / "replay.jsonl").is_file()
                ):
                    raise DemoFailure("recording missing for selected case; no live fallback")
                overlay = write_runtime_overlay(run_directory, recordings, mode, case.id)
                self.env["COMPOSE_FILE"] = os.pathsep.join([*self.files, str(overlay)])
                self.compose(
                    "up", "-d", "--no-deps", "--force-recreate", "--wait", "agent", timeout=120
                )
                if mode == "replay":
                    current_networks = self.offline_networks()
                    if current_networks != network_names:
                        raise DemoFailure("agent networks changed after runtime selection")
                health = json.loads(
                    self.compose("exec", "-T", "agent", "python", "-c", HEALTH_PROBE)
                )
                if health != {"gateway_reachable": True, "postgres_agent_reachable": True}:
                    raise DemoFailure("L2 or checkpoint service is unreachable")
                offline = None
                if mode == "replay":
                    offline = json.loads(
                        self.compose("exec", "-T", "agent", "python", "-c", OFFLINE_PROBE)
                    )
                    if offline != {"external_tcp_blocked": ["1.1.1.1", "api.deepseek.com"]}:
                        raise DemoFailure("external network blocking could not be verified")
                trace_path = f"/tmp/demo-{run_directory.name}-{case.id}.json"
                driver_env = {
                    **self.env,
                    "TRY_AGENT_OUT": trace_path,
                    "GW_PUBLIC": self.env.get("GW_PUBLIC", "172.30.10.12"),
                }
                result = self.command(
                    [
                        sys.executable,
                        str(TOOLS / "drive_try_agent.py"),
                        str(self.demo.deploy),
                        str(self.demo.deploy / ".secrets/demo_pins"),
                        case.customer_id,
                        case.text,
                        "ok",
                    ],
                    cwd=self.demo.deploy,
                    env=driver_env,
                    timeout=330,
                )
                if result.returncode in {75, 124}:
                    raise DemoFailure(
                        "provider quota/access error or timeout; batch stopped", result.returncode
                    )
                if result.returncode not in OBSERVED_EXITS:
                    raise DemoFailure(
                        "driver did not reach a recognized observation; batch stopped"
                    )
                raw_trace = self.compose("exec", "-T", "operator", "cat", trace_path)
                trace = json.loads(raw_trace)
                if (
                    not isinstance(trace, dict)
                    or not isinstance(trace.get("config"), dict)
                    or trace["config"].get("customer_id") != case.customer_id
                    or trace.get("config", {}).get("text") != case.text
                    or type(trace.get("exit_code")) is not int
                    or trace["exit_code"] != result.returncode
                    or not isinstance(trace.get("events"), list)
                    or not trace["events"]
                ):
                    raise DemoFailure("missing, stale or invalid current trace; batch stopped")
                trace_bytes = (
                    json.dumps(sanitize_trace(trace), ensure_ascii=False, indent=2) + "\n"
                ).encode()
                private_write(run_directory / f"{case.id}.trace.json", trace_bytes)
                manifest["cases"].append(
                    {
                        "case_id": case.id,
                        "customer_id": case.customer_id,
                        "typed_text": case.text,
                        "driver_exit": result.returncode,
                        "trace_sha256": hashlib.sha256(trace_bytes).hexdigest(),
                        "business_verified": False,
                        "health": health,
                        "offline_probe": offline,
                    }
                )
                checkpoint()
            manifest["status"] = "OBSERVED"
            checkpoint()
            if not observe_only:
                manifest = verify_receipts(manifest, proofs)
                manifest["status"] = "VERIFIED"
                checkpoint()
            return manifest
        except (DemoFailure, OSError, ValueError, KeyError, subprocess.SubprocessError):
            manifest["status"] = "FAILED"
            checkpoint()
            raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deploy", type=Path)
    parser.add_argument(
        "--mode", choices=("live", "record", "replay"), default=os.environ.get("LLM_MODE", "replay")
    )
    parser.add_argument("--cases", type=Path, help="explicit TSV: run all its rows in file order")
    parser.add_argument("--case-ids", help="comma-separated IDs, in execution order")
    parser.add_argument("--recordings", type=Path, default=ROOT / "evaluation/replay")
    parser.add_argument("--run-dir", type=Path)
    parser.add_argument("--observe-only", action="store_true")
    parser.add_argument("--proofs", type=Path)
    parser.add_argument(
        "--verify-run",
        type=Path,
        help="attach SQL receipts to a completed observation; no banking calls",
    )
    args = parser.parse_args()
    try:
        if args.verify_run:
            if args.proofs is None:
                raise DemoFailure("--verify-run requires --proofs")
            manifest = json.loads((args.verify_run / "manifest.json").read_text())
            for case in manifest.get("cases", []):
                trace = args.verify_run / f"{case['case_id']}.trace.json"
                if hashlib.sha256(trace.read_bytes()).hexdigest() != case["trace_sha256"]:
                    raise DemoFailure("observation trace changed since collection")
            verified = verify_receipts(manifest, args.proofs)
            verified["status"] = "VERIFIED"
            private_write(
                args.verify_run / "verification.json", json.dumps(verified, indent=2) + "\n"
            )
            print(
                json.dumps(
                    {"status": "VERIFIED", "business_verified": True, "run_id": verified["run_id"]}
                )
            )
            return 0
        deploy = default_deploy(args.deploy)
        demo = DemoEnvironment(deploy)
        selected = args.case_ids.split(",") if args.case_ids is not None else None
        if args.cases is None and selected is None:
            selected = list(DEFAULT_CASES)
        cases = load_cases(args.cases or TOOLS / "full_cases2.tsv", selected)
        run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(4)
        directory = args.run_dir or demo.state / "runs" / run_id
        if not CASE_ID.fullmatch(directory.name):
            raise DemoFailure("run directory name must be a safe unique identifier")
        with (demo.state / "lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            manifest = DemoRunner(demo).run(
                cases=cases,
                mode=args.mode,
                recordings=args.recordings.resolve(),
                run_directory=directory,
                observe_only=args.observe_only,
                proofs=args.proofs,
            )
        print(
            json.dumps({k: manifest[k] for k in ("run_id", "mode", "status", "business_verified")})
        )
        return 0
    except DemoFailure as exc:
        print(f"Demo stopped: {exc}", file=sys.stderr)
        return exc.exit_code
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        print("Demo stopped: local lifecycle/trace failure; raw details withheld", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
