"""Portable, fail-closed demo observations and coordinator SQL-receipt verification.

This runner never resets databases. Replay additionally requires Docker-internal
agent networks and actual blocked external TCP probes, with L2/PG reachability.
Driver exit labels are observations; only matching coordinator SQL receipts can
mark a run business-verified. See docs/acceptance/demo-runner.md.
"""

from __future__ import annotations

import argparse
import ast
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
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from scripts import demo_sql
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


# Same field-token strategy as the independently reviewed runtime recorder at
# a2f9d86: one credential predicate for structured, nested and malformed data.
_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "api_key",
        "api-key",
        "x-api-key",
        "service_token",
        "jwt",
        "access_token",
        "assistant_token",
        "auth",
        "password",
        "pin",
        "otp",
        "csrf",
        "cookie",
        "secret",
    }
)
_SECRET_TEXT = re.compile(
    r"(?i)\bBearer\s+[^\s\"\\]+|\bsk-[A-Za-z0-9_-]+|"
    r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|"
    r"(?:\b(?:pin|otp|password)\s*(?:is|[:=])?\s*|(?:验证码|密码)\s*[:：=]?\s*)[A-Za-z0-9_-]+"
)


def _is_sensitive_key(key: str) -> bool:
    lower = unicodedata.normalize("NFKC", key).casefold()
    lower = "".join(char for char in lower if unicodedata.category(char) != "Cf")
    return (
        lower == "token"
        or lower.endswith("_token")
        or any(sensitive in lower for sensitive in _SENSITIVE_KEYS)
    )


def _malformed_secret_fields(text: str) -> list[object]:
    """Recover field tokens, not a repaired object, using one secret-key predicate.

    Recoverable JSON values use the JSON decoder (including unicode escapes).
    An incomplete value still produces an entry so recording can fail closed.
    No malformed input is made into an executable valid tool call.
    """
    decoder = json.JSONDecoder()
    fields: list[object] = []
    offset = 0
    while offset < len(text):
        char = text[offset]
        if char == '"':
            try:
                key, end = decoder.raw_decode(text, offset)
            except ValueError:
                offset += 1
                continue
        elif char == "'":
            # Invalid JSON sometimes uses Python-style single-quoted keys.
            end = offset + 1
            while end < len(text):
                if text[end] == "\\":
                    end += 2
                    continue
                if text[end] == "'":
                    break
                end += 1
            end = min(end + 1, len(text))
            try:
                key = ast.literal_eval(text[offset:end])
            except (ValueError, SyntaxError):
                offset += 1
                continue
        elif char.isalpha() or char == "_":
            end = offset + 1
            while end < len(text) and (text[end].isalnum() or text[end] in "_-"):
                end += 1
            key = text[offset:end]
        else:
            offset += 1
            continue
        offset = end
        while end < len(text) and text[end].isspace():
            end += 1
        if (
            not isinstance(key, str)
            or not _is_sensitive_key(key)
            or end == len(text)
            or text[end] != ":"
        ):
            continue
        end += 1
        while end < len(text) and text[end].isspace():
            end += 1
        try:
            value, _ = decoder.raw_decode(text, end)
        except ValueError:
            value = None  # A damaged secret value is reason to reject, not to guess.
        fields.append(value)
    return fields


def _secret_values(obj: object) -> set[str]:
    values: set[str] = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            if _is_sensitive_key(str(key)) and isinstance(value, (str, int)):
                values.add(str(value))
            else:
                values.update(_secret_values(value))
    elif isinstance(obj, list):
        for value in obj:
            values.update(_secret_values(value))
    elif isinstance(obj, str):
        try:
            parsed = json.loads(obj)
        except ValueError:
            values.update(
                str(value)
                for value in _malformed_secret_fields(obj)
                if isinstance(value, (str, int))
            )
        else:
            if isinstance(parsed, (dict, list, str)):
                values.update(_secret_values(parsed))
    return values


def _remove_known_secrets(obj: object, secrets: set[str]) -> object:
    if isinstance(obj, dict):
        return {k: _remove_known_secrets(v, secrets) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_remove_known_secrets(v, secrets) for v in obj]
    if isinstance(obj, str):
        for secret in sorted(secrets, key=len, reverse=True):
            if secret:
                obj = obj.replace(secret, "<REDACTED>")
        return obj
    if isinstance(obj, int) and str(obj) in secrets:
        return "<REDACTED>"
    return obj


def _scrub_sensitive(obj: object, depth: int = 0) -> object:
    if depth > 30:
        return "<max-depth>"
    if isinstance(obj, dict):
        return {
            k: "<REDACTED>" if _is_sensitive_key(str(k)) else _scrub_sensitive(v, depth + 1)
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return type(obj)(_scrub_sensitive(v, depth + 1) for v in obj)
    if isinstance(obj, str):
        try:
            parsed = json.loads(obj)
        except ValueError:
            if _malformed_secret_fields(obj):
                # Reject the whole response before any header/temp/file write.
                # Scrubbing part of damaged JSON cannot establish what was hidden
                # in its remaining fields or echoed elsewhere in the response.
                raise DemoFailure("malformed sensitive JSON; trace evidence refused") from None
            return _SECRET_TEXT.sub("<REDACTED>", obj)
        if isinstance(parsed, (dict, list, str)):
            return json.dumps(
                _scrub_sensitive(parsed, depth + 1), ensure_ascii=False, separators=(",", ":")
            )
        return _SECRET_TEXT.sub("<REDACTED>", obj)
    return obj


def sanitize_trace(value: Any) -> Any:
    """Scrub valid data, reject damaged sensitive fields, never repair a tool call."""
    known = _secret_values(value)
    return _remove_known_secrets(_scrub_sensitive(value), known)


def require_safe_sql_snapshot(value: Any, depth: int = 0) -> None:
    """Reject credentials without modifying the financial facts sent to the oracle."""
    if depth > 30:
        raise DemoFailure("SQL snapshot nesting exceeds evidence limit")
    if isinstance(value, dict):
        for key, child in value.items():
            # These fixed SELECT projections prove authorization, not credentials.
            if key == "authorization_epoch":
                if child is not None and type(child) is not int:
                    raise DemoFailure("invalid authorization epoch in SQL snapshot")
                continue
            if key == "otp_verified":
                if child is not None and type(child) is not bool:
                    raise DemoFailure("invalid OTP confirmation flag in SQL snapshot")
                continue
            if _is_sensitive_key(str(key)):
                raise DemoFailure("sensitive SQL snapshot field; evidence refused")
            require_safe_sql_snapshot(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            require_safe_sql_snapshot(child, depth + 1)
    elif isinstance(value, str):
        clean = sanitize_trace(value)
        if clean != value:
            try:
                # Benign nested JSON may be canonicalized without changing facts.
                if json.loads(clean) == json.loads(value):
                    return
            except ValueError:
                pass
            raise DemoFailure("sensitive SQL snapshot text; evidence refused")


def session_from_trace(trace: dict) -> str:
    """Use only the trusted CLI's paired session HTTP events, never model text."""
    events = trace.get("events", [])
    if not isinstance(events, list):
        raise DemoFailure("invalid trusted session event list")
    request, response, created = [], [], []
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise DemoFailure("invalid trusted session event")
        detail = event.get("detail")
        if not isinstance(detail, dict):
            continue
        kind = event.get("kind")
        if kind == "http_request" and detail.get("to") == "G15":
            request.append((index, detail))
        elif kind == "http_response" and detail.get("from") == "G15":
            response.append((index, detail))
        elif kind == "result" and detail.get("event") == "session_created":
            created.append((index, detail))
    if len(request) != 1 or len(response) != 1 or len(created) != 1:
        raise DemoFailure("missing or multiple trusted session responses")
    req_i, req = request[0]
    res_i, res = response[0]
    created_i, result = created[0]
    url = req.get("url")
    if not isinstance(url, str):
        raise DemoFailure("invalid trusted session request URL")
    try:
        parsed = urlsplit(url)
    except ValueError:
        raise DemoFailure("invalid trusted session request URL") from None
    body = res.get("body")
    sid = body.get("session_id") if isinstance(body, dict) else None
    if (
        not req_i < res_i < created_i
        or parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != "/sessions"
        or parsed.query
        or parsed.fragment
        or type(res.get("status")) is not int
        or res["status"] not in {200, 201}
        or not isinstance(body, dict)
        or set(body) != {"session_id"}
        or not isinstance(sid, str)
        or re.fullmatch(r"ses_[A-Za-z0-9_-]{1,128}", sid) is None
        or result.get("session_id") != sid
    ):
        raise DemoFailure("invalid or unbound trusted session response")
    return sid


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
        initial = current.get("initial", {})
        seed = initial.get("seed") if isinstance(initial, dict) else None
        if not isinstance(seed, str) or not seed:
            raise DemoFailure("current generation initialization seed is missing")
        return {"generation": generation, "checkout_sha": sha, "seed": seed}

    def sql_snapshot(self, kind: str, generation: str, query: str) -> dict:
        """Execute a fixed oracle SELECT only in host-side Postgres, read-only."""
        if kind not in {"bank", "gateway"}:
            raise DemoFailure("unsupported SQL snapshot database")
        generation_names(generation)
        try:
            raw = self.demo.compose(
                "exec",
                "-T",
                "-e",
                "PGOPTIONS=-c default_transaction_read_only=on",
                "postgres-bank",
                "psql",
                "-X",
                "-U",
                "postgres",
                "-d",
                f"{kind}_{generation}",
                "-v",
                "ON_ERROR_STOP=1",
                "-tA",
                overlay=Path(self.files[-1]),
                data=query,
            )
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("snapshot is not an object")
            require_safe_sql_snapshot(payload)
            return payload
        except (RuntimeError, OSError, ValueError, subprocess.SubprocessError):
            raise DemoFailure("read-only SQL snapshot failed; raw details withheld") from None

    def automatic_receipt(
        self,
        *,
        case: Case,
        bank_before: dict,
        trace: dict,
        manifest: dict,
        row: dict,
        directory: Path,
        proofs: Path,
        seen_sessions: set[str],
    ) -> None:
        sid = session_from_trace(trace)
        if sid in seen_sessions:
            raise DemoFailure("session reused across independent cases")
        seen_sessions.add(sid)
        expected_exit = 1 if case.id in {"16", "27"} else 9
        if row["driver_exit"] != expected_exit or any(
            event.get("kind") == "error" for event in trace["events"]
        ):
            raise DemoFailure("case did not reach its required successful driver outcome")
        binding = {
            **{key: manifest[key] for key in ("run_id", "checkout_sha", "generation")},
            "case_id": case.id,
            "trace_sha256": row["trace_sha256"],
            "session_id": sid,
        }
        before = {"meta": binding, "bank": bank_before}
        before_path = directory / f"{case.id}.before.json"
        private_write(before_path, json.dumps(before, ensure_ascii=False, indent=2) + "\n")
        queries = demo_sql.build_queries(sid)
        bank_after = self.sql_snapshot("bank", manifest["generation"], queries["bank"])
        private_write(
            directory / f"{case.id}.after-bank.json",
            json.dumps({"meta": binding, "bank": bank_after}, ensure_ascii=False, indent=2) + "\n",
        )
        gateway_after = self.sql_snapshot("gateway", manifest["generation"], queries["gateway"])
        private_write(
            directory / f"{case.id}.after-gateway.json",
            json.dumps({"meta": binding, "gateway": gateway_after}, ensure_ascii=False, indent=2)
            + "\n",
        )
        after = {"meta": binding, "bank": bank_after, "gateway": gateway_after}
        after_path = directory / f"{case.id}.after.json"
        private_write(after_path, json.dumps(after, ensure_ascii=False, indent=2) + "\n")
        try:
            receipt = demo_sql.verify_case(case.id, before, after, binding)
        except demo_sql.OracleError:
            raise DemoFailure("case-specific SQL verification failed") from None
        private_write(proofs / f"{case.id}.json", json.dumps(receipt, indent=2) + "\n")
        row.update(
            session_id=sid,
            sql_before_sha256=hashlib.sha256(before_path.read_bytes()).hexdigest(),
            sql_after_sha256=hashlib.sha256(after_path.read_bytes()).hexdigest(),
        )

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
        automatic = not observe_only and proofs is None
        if automatic and (
            not cases
            or any(
                case.id not in demo_sql.CASES
                or case.text != demo_sql.CASES[case.id]
                or case.customer_id != ("cus_ls" if case.id in {"34", "23"} else "cus_zs")
                for case in cases
            )
        ):
            raise DemoFailure(
                "unsupported automatic case; use explicit external proofs or observation"
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
            "run_id": secrets.token_hex(16),
            "run_directory_name": run_directory.name,
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
        if automatic:
            proofs = run_directory / "sql-receipts"
            proofs.mkdir(mode=0o700)
        seen_sessions: set[str] = set()

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
                request_id = str(uuid.uuid4())
                trace_path = f"/tmp/demo-{manifest['run_id']}-{case.id}-{request_id}.json"
                driver_env = {
                    **self.env,
                    "TRY_AGENT_OUT": trace_path,
                    "TRY_AGENT_REQUEST_ID": request_id,
                    "GW_PUBLIC": self.env.get("GW_PUBLIC", "172.30.10.12"),
                }
                bank_before = None
                if automatic:
                    bank_before = self.sql_snapshot(
                        "bank", manifest["generation"], demo_sql.bank_snapshot_query()
                    )
                    private_write(
                        run_directory / f"{case.id}.before-bank.json",
                        json.dumps(
                            {
                                "capture": "before_driver",
                                "run_id": manifest["run_id"],
                                "checkout_sha": manifest["checkout_sha"],
                                "generation": manifest["generation"],
                                "case_id": case.id,
                                "bank": bank_before,
                            },
                            ensure_ascii=False,
                            indent=2,
                        )
                        + "\n",
                    )
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
                    or trace["config"].get("request_id_prefix") != request_id
                    or type(trace.get("exit_code")) is not int
                    or trace["exit_code"] != result.returncode
                    or not isinstance(trace.get("events"), list)
                    or not trace["events"]
                ):
                    raise DemoFailure("missing, stale or invalid current trace; batch stopped")
                clean_trace = sanitize_trace(trace)
                trace_bytes = (
                    json.dumps(clean_trace, ensure_ascii=False, indent=2) + "\n"
                ).encode()
                private_write(run_directory / f"{case.id}.trace.json", trace_bytes)
                manifest["cases"].append(
                    {
                        "case_id": case.id,
                        "customer_id": case.customer_id,
                        "typed_text": clean_trace["config"]["text"],
                        "driver_exit": result.returncode,
                        "request_id": request_id,
                        "trace_sha256": hashlib.sha256(trace_bytes).hexdigest(),
                        "business_verified": False,
                        "health": health,
                        "offline_probe": offline,
                    }
                )
                checkpoint()
                if automatic:
                    self.automatic_receipt(
                        case=case,
                        bank_before=bank_before,
                        trace=trace,
                        manifest=manifest,
                        row=manifest["cases"][-1],
                        directory=run_directory,
                        proofs=proofs,
                        seen_sessions=seen_sessions,
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
