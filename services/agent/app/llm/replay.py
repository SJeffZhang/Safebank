"""C1e LLM recording/replay infrastructure (PRD §6.6, ADR-004).

Request-key hashing, ID canonicalisation, recording transport, replay loader,
and schema-version validation.  Never logs JWT, tokens or API keys.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
import threading
import uuid
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

import httpx

# ── Seed ID whitelist (PRD §2.2, extracted 2026-10-25) ──────────────────
# These fixed seed IDs are preserved verbatim during canonicalisation.
# When seed data is implemented dynamically this will read from the seed
# source instead of a constant (tracked in C1e progress docs as pending).

_SEED_IDS: frozenset[str] = frozenset(
    {
        # Customers
        "cus_zs",  # 张三
        "cus_ww",  # 王五
        "cus_zl",  # 赵六
        "cus_ls",  # 李四
        "cus_sb",  # 孙八
        # Accounts
        "acc_zs_chk",
        "acc_zs_sav",
        "acc_ww_chk",
        "acc_zl_chk",
        "acc_ls_chk",
        "acc_sb_chk",
        # Cards
        "crd_zs_1",
        "crd_ww_1",
        "crd_zl_1",
        "crd_ls_1",
        # Payees
        "pye_zs_ww",
        "pye_ww_zs",
        "pye_zs_zl",
        # Products
        "prd_mmf_1",
        "prd_fund_1",
        "prd_fund_2",
        # Merchants
        "mer_zs_net",
        "mer_zs_rent",
        # Billing
        "bil_zs_net",
        "bil_zs_rent",
        # Standing instructions
        "si_zs_net",
        "si_zs_rent",
        # Orders
        "ord_zs_mmf_1",
    }
)

# Runtime bank IDs, distinct from opaque provider tool-call IDs.
_ID_PATTERN = re.compile(
    r"\b[a-z]{2,4}_([0-9A-HJKMNP-TV-Z]{26}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b"
)
_PLACEHOLDER = re.compile(r"<(?:ID|CALL|NEWCALL|UNBOUND)\d+>")
_SAFE_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")


class LLMMode:
    LIVE = "live"
    RECORD = "record"
    REPLAY = "replay"
    _VALID = frozenset({LIVE, RECORD, REPLAY})

    @classmethod
    def from_env(cls, default: str = "live") -> str:
        value = os.environ.get("LLM_MODE", default).strip().lower()
        if value not in cls._VALID:
            raise ValueError("LLM_MODE must be live, record or replay")
        return value


class ReplayMissError(RuntimeError):
    """Missing recording/key/sequence entry; never fall back to a provider."""


class ReplayStaleError(RuntimeError):
    """Incompatible, corrupt or unbound recording; never reuse old authority."""


def _json(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=True, default=str)


def strip_wall_fields(obj: object) -> object:
    """Strip wall fields, including JSON encoded inside provider message content."""
    if isinstance(obj, dict):
        return {
            k: strip_wall_fields(v)
            for k, v in obj.items()
            if not (isinstance(k, str) and k.endswith("_wall"))
        }
    if isinstance(obj, (list, tuple)):
        return type(obj)(strip_wall_fields(v) for v in obj)
    if isinstance(obj, str):
        try:
            parsed = json.loads(obj)
        except (ValueError, TypeError):
            return obj
        if isinstance(parsed, (dict, list)):
            return _json(strip_wall_fields(parsed))
    return obj


class _Bindings:
    """One mapping shared across all request fields; tool IDs are structural."""

    def __init__(self) -> None:
        self.ids: dict[str, str] = {}
        self.calls: dict[str, str] = {}

    def bank_text(self, text: str, *, response: bool = False) -> str:
        def replace(match: re.Match) -> str:
            token = match.group(0)
            if token in _SEED_IDS:
                return token
            if token not in self.ids:
                kind = "UNBOUND" if response else "ID"
                self.ids[token] = f"<{kind}{len(self.ids) + 1}>"
            return self.ids[token]

        return _ID_PATTERN.sub(replace, text)

    def walk(self, obj: object, *, response: bool = False, in_calls: bool = False) -> object:
        if isinstance(obj, dict):
            result = {}
            for key in sorted(obj):
                value = obj[key]
                if key == "tool_call_id" or (in_calls and key == "id"):
                    if not isinstance(value, str) or not value:
                        raise ReplayStaleError("Invalid provider tool-call ID")
                    if value not in self.calls:
                        kind = "NEWCALL" if response else "CALL"
                        self.calls[value] = f"<{kind}{len(self.calls) + 1}>"
                    result[key] = self.calls[value]
                else:
                    result[key] = self.walk(value, response=response, in_calls=key == "tool_calls")
            return result
        if isinstance(obj, list):
            return [self.walk(v, response=response, in_calls=in_calls) for v in obj]
        if isinstance(obj, str):
            return self.bank_text(obj, response=response)
        return obj

    def restore(self, obj: object) -> object:
        reverse = {v: k for k, v in (self.ids | self.calls).items()}
        generated: dict[str, str] = {}

        def replace(match: re.Match) -> str:
            token = match.group(0)
            if token.startswith("<NEWCALL"):
                return generated.setdefault(token, "call_replay_" + uuid.uuid4().hex)
            if token not in reverse or token.startswith("<UNBOUND"):
                raise ReplayStaleError("Unbound replay ID; refusing recorded authority")
            return reverse[token]

        # JSON substitution handles IDs embedded in function.arguments strings as well.
        return json.loads(_PLACEHOLDER.sub(replace, _json(obj)))


def canonical_text(text: str) -> str:
    return _Bindings().bank_text(text)


def canonical_messages(messages: object) -> str:
    return _json(_Bindings().walk(strip_wall_fields(messages)))


def canonical_tools(tools: object) -> str:
    return canonical_messages(tools if tools is not None else [])


def canonical_params(params: dict[str, object]) -> str:
    return canonical_messages(params)


def _request_key(body: dict) -> tuple[str, _Bindings]:
    bindings = _Bindings()
    normalized = bindings.walk(strip_wall_fields(body))
    return hashlib.sha256(_json(normalized).encode()).hexdigest(), bindings


def replay_key(model: str, messages: object, tools: object, params: dict[str, object]) -> str:
    return _request_key({**params, "model": model, "messages": messages, "tools": tools or []})[0]


def replay_schema_version(
    tools_schema: object, system_prompts: dict[str, str], model_settings: dict | None = None
) -> str:
    return hashlib.sha256(
        _json(
            {
                "format": 2,
                "tools": tools_schema,
                "systems": system_prompts,
                "settings": model_settings or {},
            }
        ).encode()
    ).hexdigest()


def _replay_dir() -> Path:
    profile = os.environ.get("LLM_PROFILE", "b1")
    case_id = os.environ.get("LLM_CASE_ID", "default")
    for value in (profile, case_id):
        if not _SAFE_COMPONENT.fullmatch(value):
            raise ValueError("Unsafe LLM_PROFILE or LLM_CASE_ID")
    return Path(os.environ.get("LLM_REPLAY_ROOT", "evaluation/replay")) / profile / case_id


def runtime_location() -> tuple[Path, str]:
    profile = os.environ.get("LLM_PROFILE", "B2")
    case_id = os.environ.get("LLM_CASE_ID", "default")
    if profile not in {"B1", "B2"}:
        raise ValueError("Production LLM_PROFILE must be B1 or B2")
    if not _SAFE_COMPONENT.fullmatch(case_id):
        raise ValueError("Unsafe LLM_CASE_ID")
    root = Path(os.environ.get("LLM_REPLAY_ROOT", "evaluation/replay"))
    return root / profile / case_id / "replay.jsonl", profile


def _validated_lines(path: Path, schema_version: str, profile: str) -> tuple[dict, list[dict]]:
    if not path.exists():
        raise ReplayMissError(f"Replay file not found: {path}")
    try:
        lines = path.read_text().splitlines()
        if not lines:
            raise ReplayMissError(f"Replay file is empty: {path}")
        header = json.loads(lines[0])
        if not isinstance(header, dict) or header.get("schema_version") != schema_version:
            raise ReplayStaleError("Replay schema version mismatch")
        if header.get("profile") != profile:
            raise ReplayStaleError("Replay profile mismatch")
        entries = []
        for line in lines[1:]:
            entry = json.loads(line)
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("key"), str)
                or not isinstance(entry.get("response"), dict)
                or not isinstance(entry.get("params"), dict)
                or not isinstance(entry.get("model"), str)
            ):
                raise ReplayStaleError("Corrupt replay entry")
            if header.get("format") == 2:
                digest = entry.pop("digest", None)
                if digest != hashlib.sha256(_json(entry).encode()).hexdigest():
                    raise ReplayStaleError("Corrupt replay checksum")
                entry["digest"] = digest
            entries.append(entry)
        return header, entries
    except (ValueError, UnicodeError, TypeError) as exc:
        raise ReplayStaleError("Corrupt replay JSON") from exc


class RecordTransport:
    """Record successful JSON responses atomically under a cross-process file lock.

    No request messages or HTTP headers are persisted. Response references use
    placeholders; only the current replay request can bind their authority.
    """

    def __init__(
        self,
        inner: Callable,
        *,
        schema_version: str,
        profile: str,
        tools_schema: object,
        system_prompts: dict[str, str],
        path: Path | None = None,
    ) -> None:
        self._inner = inner
        self._schema_version = schema_version
        self._profile = profile
        self._path = path if path is not None else _replay_dir() / "replay.jsonl"
        self._header = {
            "format": 2,
            "schema_version": schema_version,
            "profile": profile,
            "tools": _scrub_sensitive(tools_schema),
            "systems": _scrub_sensitive(system_prompts),
        }
        if self._path.exists():
            _validated_lines(self._path, schema_version, profile)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        response = self._inner(request)
        response.read()
        if response.is_success:
            self._record(request, response)
        return response

    def _record(self, request: httpx.Request, response: httpx.Response) -> None:
        body = json.loads(request.content)
        if not isinstance(body, dict):
            raise ReplayStaleError("Invalid provider request")
        body.setdefault("tools", [])
        key, bindings = _request_key(body)
        # Known credentials echoed by the provider must not survive plain-text output.
        secrets = _secret_values(body) | _secret_values(response.json())
        for name, value in request.headers.items():
            if _is_sensitive_key(name):
                secrets.add(value)
                if value.lower().startswith("bearer "):
                    secrets.add(value[7:])
        normalized = bindings.walk(response.json(), response=True)
        entry = {
            "key": key,
            "model": body.get("model", ""),
            "params": _scrub_sensitive(
                {k: v for k, v in body.items() if k not in {"messages", "tools"}}
            ),
            "response": _scrub_sensitive(normalized),
        }
        entry["params"] = _remove_known_secrets(entry["params"], secrets)
        entry["response"] = _remove_known_secrets(entry["response"], secrets)
        entry["digest"] = hashlib.sha256(_json(entry).encode()).hexdigest()
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path = self._path.with_suffix(".lock")
        with os.fdopen(os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600), "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            entries = []
            if self._path.exists():
                _, entries = _validated_lines(self._path, self._schema_version, self._profile)
            # Replace a complete file; a crash cannot leave a half header/entry visible.
            with tempfile.NamedTemporaryFile(
                mode="w", dir=self._path.parent, prefix=".replay-", delete=False
            ) as output:
                output.write(_json(self._header) + "\n")
                for item in [*entries, entry]:
                    output.write(_json(item) + "\n")
                output.flush()
                os.fsync(output.fileno())
                temporary = output.name
            os.replace(temporary, self._path)


class ReplayLoader:
    """Consume a per-key response sequence; exhaustion and corruption fail closed."""

    def __init__(
        self, *, schema_version: str, profile: str | None = None, path: Path | None = None
    ) -> None:
        self._path = path if path is not None else _replay_dir() / "replay.jsonl"
        self._profile = profile if profile is not None else os.environ.get("LLM_PROFILE", "b1")
        _, entries = _validated_lines(self._path, schema_version, self._profile)
        self._index: dict[str, list[dict]] = defaultdict(list)
        self._positions: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()
        for entry in entries:
            self._index[entry["key"]].append(entry)

    def lookup(self, key: str) -> dict[str, object]:
        with self._lock:
            entries = self._index.get(key, [])
            position = self._positions[key]
            if position >= len(entries):
                raise ReplayMissError(f"Replay miss or sequence exhausted for key {key[:16]}")
            self._positions[key] += 1
            return json.loads(_json(entries[position]))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        body.setdefault("tools", [])
        key, bindings = _request_key(body)
        entry = self.lookup(key)
        response = bindings.restore(entry["response"])
        return httpx.Response(200, json=response, request=request)

    @property
    def profile(self) -> str:
        return self._profile

    @property
    def key_count(self) -> int:
        return len(self._index)


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


_QUOTED_SECRET = re.compile(
    r"(?i)([\"'](?:[A-Za-z0-9_-]*_)?"
    r"(?:pin|otp|token|password|secret|jwt|api_key|authorization|cookie)"
    r"[\"']\s*:\s*[\"'])([^\"']*)"
)


def _is_sensitive_key(key: str) -> bool:
    lower = key.lower()
    return (
        lower == "token"
        or lower.endswith("_token")
        or any(sensitive in lower for sensitive in _SENSITIVE_KEYS)
    )


def _secret_values(obj: object) -> set[str]:
    values: set[str] = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            if _is_sensitive_key(key) and isinstance(value, (str, int)):
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
            values.update(match.group(2) for match in _QUOTED_SECRET.finditer(obj))
        else:
            if isinstance(parsed, (dict, list)):
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
            k: "<REDACTED>" if _is_sensitive_key(k) else _scrub_sensitive(v, depth + 1)
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return type(obj)(_scrub_sensitive(v, depth + 1) for v in obj)
    if isinstance(obj, str):
        try:
            parsed = json.loads(obj)
        except ValueError:
            quoted = _QUOTED_SECRET.sub(r"\1<REDACTED>", obj)
            return _SECRET_TEXT.sub("<REDACTED>", quoted)
        if isinstance(parsed, (dict, list)):
            return _json(_scrub_sensitive(parsed, depth + 1))
        return _SECRET_TEXT.sub("<REDACTED>", obj)
    return obj
