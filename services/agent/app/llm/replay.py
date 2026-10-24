"""C1e LLM recording/replay infrastructure (PRD §6.6, ADR-004).

Request-key hashing, ID canonicalisation, recording transport, replay loader,
and schema-version validation.  Never logs JWT, tokens or API keys.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

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

# ── ID normalisation regex (PRD §6.6) ────────────────────────────────────
# Matches IDs with any recognised prefix (2-4 lowercase letters + underscore)
# followed by either 26 Crockford base32 chars or a UUIDv5 hex pattern.

_ID_PATTERN = re.compile(
    r"\b[a-z]{2,4}_([0-9A-HJKMNP-TV-Z]{26}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b"
)

# ── Wall-field stripping pattern ─────────────────────────────────────────

_WALL_SUFFIX = "_wall"


# ── LLM_MODE ─────────────────────────────────────────────────────────────


class LLMMode:
    LIVE = "live"
    RECORD = "record"
    REPLAY = "replay"

    _VALID = frozenset({LIVE, RECORD, REPLAY})

    @classmethod
    def from_env(cls, default: str = "live") -> str:
        """Read ``LLM_MODE`` from environment, fail-closed on unknown value."""
        value = os.environ.get("LLM_MODE", default).strip().lower()
        if value not in cls._VALID:
            raise ValueError(
                f"LLM_MODE={value!r} is not one of {sorted(cls._VALID)}"
            )
        return value


# ── canonical ─────────────────────────────────────────────────────────────


def canonical_text(text: str) -> str:
    """Replace run-time IDs in *text* with ``<ID1>``, ``<ID2>``, ....

    Seed IDs (from PRD §2.2) are left untouched.  IDs are replaced in order of
    first appearance; the reverse mapping can be applied later via
    ``replay_reverse_map``.

    Returns the canonicalised string — does **not** modify the original.
    """
    seen: dict[str, str] = {}
    counter = 0

    def _replacement(m: re.Match) -> str:
        nonlocal counter
        token = m.group(0)
        if token in _SEED_IDS:
            return token
        if token not in seen:
            counter += 1
            seen[token] = f"<ID{counter}>"
        return seen[token]

    return _ID_PATTERN.sub(_replacement, text)


def canonical_messages(messages: object) -> str:
    """Canonicalise messages for replay-key computation.

    Serialises to JSON (sorted keys, ASCII-encoded), then replaces run-time IDs.
    """
    raw = json.dumps(messages, sort_keys=True, ensure_ascii=True, default=str)
    return canonical_text(raw)


def canonical_tools(tools: object) -> str:
    """Canonicalise tool definitions for replay-key computation."""
    if tools is None:
        return "[]"
    raw = json.dumps(tools, sort_keys=True, ensure_ascii=True, default=str)
    return canonical_text(raw)


def canonical_params(params: dict[str, object]) -> str:
    """Canonicalise request parameters for replay-key computation.

    Per E1, ALL parameters participate — nothing is excluded.
    """
    raw = json.dumps(params, sort_keys=True, ensure_ascii=True, default=str)
    return canonical_text(raw)


# ── replay_key ───────────────────────────────────────────────────────────


def replay_key(
    model: str,
    messages: object,
    tools: object,
    params: dict[str, object],
) -> str:
    """Compute deterministic replay key per PRD §6.6.

    ``sha256(model ‖ canonical(messages) ‖ canonical(tools) ‖ canonical(params))``

    E1: ALL request parameters (temperature, max_tokens, extra_body, etc.)
    are included — nothing is excluded from *params*.
    """
    parts = [
        model,
        canonical_messages(messages),
        canonical_tools(tools),
        canonical_params(params),
    ]
    joined = "\n".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


# ── strip_wall_fields ────────────────────────────────────────────────────


def strip_wall_fields(obj: object) -> object:
    """Recursively remove keys ending with ``_wall`` from dicts.

    Lists and dicts are traversed; other values are returned unchanged.
    Returns a new object — never mutates the input.
    """
    if isinstance(obj, dict):
        return {
            k: strip_wall_fields(v)
            for k, v in obj.items()
            if not _is_wall_key(k)
        }
    if isinstance(obj, (list, tuple)):
        return type(obj)(strip_wall_fields(v) for v in obj)
    return obj


def _is_wall_key(key: object) -> bool:
    return isinstance(key, str) and key.endswith(_WALL_SUFFIX)


# ── replay_schema_version ────────────────────────────────────────────────


def replay_schema_version(
    tools_schema: object,
    system_prompts: dict[str, str],
) -> str:
    """Compute a deterministic schema version for replay files.

    ``sha256(canonical(tools_schema) ‖ canonical(system_prompts))``

    Used in the file header: when an existing replay file's schema version
    does not match the current tools/system prompts, the file is REPLAY_STALE.
    """
    joined = "\n".join(
        [
            canonical_tools(tools_schema),
            canonical_params(system_prompts),
        ]
    )
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


# ── replay directory helpers ─────────────────────────────────────────────


def _replay_dir() -> Path:
    """Resolve the replay directory from environment variables.

    ``LLM_PROFILE`` (default ``b1``) and ``LLM_CASE_ID`` (default ``default``)
    determine the sub-path under ``evaluation/replay/``.
    """
    profile = os.environ.get("LLM_PROFILE", "b1")
    case_id = os.environ.get("LLM_CASE_ID", "default")
    return Path("evaluation") / "replay" / profile / case_id


# ── RecordTransport ──────────────────────────────────────────────────────


class RecordTransport:
    """Wrap an *httpx* transport, writing each response to a replay file.

    The first recorded entry is a metadata header with ``schema_version``,
    ``profile``, ``tools`` and ``systems``.  Subsequent entries are ``{key,
    model, params, response}`` lines.

    Never writes JWT, service tokens, API keys or Authorization headers —
    the ``_scrub`` method strips them from params and response before writing.
    """

    def __init__(
        self,
        inner: object,
        *,
        schema_version: str,
        profile: str,
        tools_schema: object,
        system_prompts: dict[str, str],
    ) -> None:
        self._inner = inner
        self._schema_version = schema_version
        self._profile = profile
        self._header_written = False
        self._dir = _replay_dir()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._tools_schema = tools_schema
        self._system_prompts = system_prompts
        self._path = self._dir / "replay.jsonl"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        response = self._inner(request)  # type: ignore[call-arg]
        self._record(request, response)
        return response

    def _record(self, request: httpx.Request, response: httpx.Response) -> None:
        # Compute key from the original request payload
        try:
            body = json.loads(request.content) if request.content else {}
        except json.JSONDecodeError:
            body = {}

        messages = body.get("messages", [])
        tools = body.get("tools", [])
        # Everything else is a parameter (model, temperature, max_tokens, extra_body, …)
        params = {k: v for k, v in body.items() if k not in ("messages", "tools")}

        key = replay_key(body.get("model", ""), messages, tools, params)

        # Scrub sensitive data before writing
        scrubbed_params = _scrub_sensitive(params)

        try:
            resp_body = response.json()
        except (json.JSONDecodeError, AttributeError):
            resp_body = {"_raw": response.text[:200]}
        scrubbed_resp = _scrub_sensitive(resp_body)

        entry: dict[str, object] = {
            "key": key,
            "model": body.get("model", ""),
            "params": scrubbed_params,
            "response": scrubbed_resp,
        }

        with open(self._path, "a") as fh:
            if not self._header_written:
                header = {
                    "schema_version": self._schema_version,
                    "profile": self._profile,
                    "tools": strip_wall_fields(self._tools_schema),
                    "systems": self._system_prompts,
                }
                fh.write(json.dumps(header, sort_keys=True, ensure_ascii=True) + "\n")
                self._header_written = True
            fh.write(json.dumps(entry, sort_keys=True, ensure_ascii=True) + "\n")


# ── ReplayLoader ─────────────────────────────────────────────────────────


class ReplayLoader:
    """Load a replay file and look up responses by key.

    Reads the first line as a metadata header, then builds an in-memory index
    of ``{key: (model, params, response)}``.

    Raises ``ReplayStaleError`` if the header ``schema_version`` does not match
    the given value, or ``ReplayMissError`` when a key is not found.
    """

    def __init__(
        self,
        *,
        schema_version: str,
    ) -> None:
        self._schema_version = schema_version
        self._dir = _replay_dir()
        self._path = self._dir / "replay.jsonl"
        self._index: dict[str, object] = {}
        self._profile: str = ""
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            raise ReplayMissError(
                f"Replay file not found: {self._path} — run with LLM_MODE=record first"
            )

        lines = self._path.read_text().strip().splitlines()
        if not lines:
            raise ReplayMissError(f"Replay file is empty: {self._path}")

        # First line is the metadata header
        try:
            header = json.loads(lines[0])
        except json.JSONDecodeError as exc:
            raise ReplayStaleError(
                f"Replay file header is not valid JSON: {self._path}"
            ) from exc

        file_version = header.get("schema_version", "")
        if file_version != self._schema_version:
            raise ReplayStaleError(
                f"Replay schema version mismatch: "
                f"file={file_version[:16]}... current={self._schema_version[:16]}..."
            )

        self._profile = header.get("profile", "")

        # Remaining lines are data entries
        for line in lines[1:]:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = entry.get("key")
            if key:
                self._index[key] = entry

    def lookup(self, key: str) -> dict[str, object]:
        """Return the recorded ``{model, params, response}`` for *key*.

        Raises ``ReplayMissError`` when the key is not found.
        """
        entry = self._index.get(key)
        if entry is None:
            raise ReplayMissError(
                f"Replay miss for key {key[:16]}... — "
                f"file={self._path}, keys_loaded={len(self._index)}"
            )
        return {
            "model": entry.get("model"),
            "params": entry.get("params"),
            "response": entry.get("response"),
        }

    @property
    def profile(self) -> str:
        return self._profile

    @property
    def key_count(self) -> int:
        return len(self._index)


# ── Replay errors ────────────────────────────────────────────────────────


class ReplayMissError(RuntimeError):
    """Replay key not found in recorded data (no fallback to live)."""


class ReplayStaleError(RuntimeError):
    """Replay file schema version does not match current tools/systems."""


# ── Sensitive data scrubbing ─────────────────────────────────────────────

_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "Authorization",
        "api_key",
        "api-key",
        "x-api-key",
        "service_token",
        "jwt",
        "token",
        "access_token",
        "assistant_token",
        "auth",
    }
)


def _scrub_sensitive(obj: object, depth: int = 0) -> object:
    """Recursively remove sensitive keys from dicts.

    Depth-limited to avoid infinite recursion on circular structures.
    """
    if depth > 10:
        return "<max-depth>"
    if isinstance(obj, dict):
        scrubbed: dict[str, object] = {}
        for k, v in obj.items():
            if isinstance(k, str) and _is_sensitive_key(k):
                scrubbed[k] = "<REDACTED>"
            else:
                scrubbed[k] = _scrub_sensitive(v, depth + 1)
        return scrubbed
    if isinstance(obj, (list, tuple)):
        return type(obj)(_scrub_sensitive(v, depth + 1) for v in obj)
    return obj


def _is_sensitive_key(key: str) -> bool:
    lower = key.lower()
    for sensitive in _SENSITIVE_KEYS:
        if sensitive.lower() in lower:
            return True
    return False