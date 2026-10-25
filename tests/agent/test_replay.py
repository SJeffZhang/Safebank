"""C1e replay.py pure-function tests.

No PG, no real API calls, no replay file I/O except via temp directories.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import mock

import httpx
import pytest

from services.agent.app.llm import replay as _replay

# ── httpx transport helpers (used by RecordTransport tests) ─────────────


def _ok_response(req: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})


def _empty_response(req: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"choices": []})


# ── ID helpers ──────────────────────────────────────────────────────────


def _rid(prefix: str, idx: int = 0) -> str:
    """Build a runtime ID with exactly 26 Crockford base32 chars after prefix."""
    # 26-char Crockford base32 suffix (0-9, A-H, J-N, P-T, V-Z)
    suffix_parts = [
        "N9JH2M4PQ6RVX8T3EFGAB7K5ZD",  # 26 chars
        "W1CX3YR5TA8BDEFGH2J4KMNP7Q",  # 26 chars
        "S9VW2BX4CY6DZ8AF3EG5HJ7KPMN",  # 26 chars
    ]
    return f"{prefix}_{suffix_parts[idx % len(suffix_parts)]}"


def _uuid_id(prefix: str, idx: int = 0) -> str:
    """Build a runtime ID with UUIDv5 hex pattern after prefix."""
    uuid_parts = [
        "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "11111111-2222-3333-4444-555555555555",
        "feedface-dead-beef-cafe-babe12345678",
    ]
    return f"{prefix}_{uuid_parts[idx % len(uuid_parts)]}"


# ── LLMMode tests ────────────────────────────────────────────────────────


class TestLLMMode:
    def test_default_live(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            assert _replay.LLMMode.from_env() == "live"

    def test_explicit_live(self):
        with mock.patch.dict("os.environ", {"LLM_MODE": "live"}):
            assert _replay.LLMMode.from_env() == "live"

    def test_record(self):
        with mock.patch.dict("os.environ", {"LLM_MODE": "record"}):
            assert _replay.LLMMode.from_env() == "record"

    def test_replay(self):
        with mock.patch.dict("os.environ", {"LLM_MODE": "replay"}):
            assert _replay.LLMMode.from_env() == "replay"

    def test_unknown_fail_closed(self):
        with mock.patch.dict("os.environ", {"LLM_MODE": "bogus"}):
            with pytest.raises(ValueError, match="LLM_MODE"):
                _replay.LLMMode.from_env()

    def test_empty_fail_closed(self):
        with mock.patch.dict("os.environ", {"LLM_MODE": ""}):
            with pytest.raises(ValueError, match="LLM_MODE"):
                _replay.LLMMode.from_env()


# ── canonical_text tests ─────────────────────────────────────────────────


class TestCanonicalText:
    def test_runtime_id_replaced(self):
        text = f"transfer 80000 from {_rid('acc', 0)} to {_rid('pye', 1)}"
        result = _replay.canonical_text(text)
        # Both runtime IDs should be replaced with sequential <IDn>
        assert "<ID1>" in result
        assert "<ID2>" in result
        # Original runtime IDs should be gone
        assert _rid("acc", 0) not in result
        assert _rid("pye", 1) not in result

    def test_seed_id_not_replaced(self):
        # cus_zs and acc_zs_chk are on the seed whitelist
        text = "customer cus_zs balance from acc_zs_chk"
        result = _replay.canonical_text(text)
        assert "cus_zs" in result
        assert "acc_zs_chk" in result
        assert "<ID" not in result

    def test_same_id_same_placeholder(self):
        rid = _rid("acc", 0)
        text = f"from {rid} to same {rid}"
        result = _replay.canonical_text(text)
        # Same ID should get the same <IDn>
        assert result.count("<ID1>") == 2
        assert "<ID2>" not in result

    def test_different_ids_increment(self):
        # Use different prefixes with same known-good 26-char suffix
        text = f"a={_rid('acc', 0)} b={_rid('crd', 0)} c={_rid('pye', 0)}"
        # But since they all use the same 26-char suffix, canonical gives them
        # different <IDn> because the full token (prefix_suffix) differs.
        # Wait — same suffix → same ID after canonical → only 1 <ID>.
        # Use different prefixes with different 26-char suffixes
        text = f"a={_rid('acc', 0)} b={_rid('crd', 1)}"
        result = _replay.canonical_text(text)
        assert "<ID1>" in result
        assert "<ID2>" in result

    def test_uuid5_id_replaced(self):
        text = f"step {_uuid_id('step', 0)} and hnt {_uuid_id('hnt', 1)}"
        result = _replay.canonical_text(text)
        assert "step_a1b2c3d4" not in result
        assert "<ID1>" in result
        assert "<ID2>" in result

    def test_no_ids_unchanged(self):
        text = "plain text with no IDs at all"
        result = _replay.canonical_text(text)
        assert result == text

    def test_id_mid_text(self):
        rid = _rid("cus", 0)
        text = f"hello {rid} world"
        result = _replay.canonical_text(text)
        assert "hello <ID1> world" in result

    def test_digits_only_not_matched(self):
        text = "account 6222021234567890"
        result = _replay.canonical_text(text)
        # Pure numeric card numbers not matched by the ID pattern
        assert "6222021234567890" in result

    def test_no_prefix_no_match(self):
        # UUID without a prefix (e.g. bare UUID in text) should NOT be matched
        text = "bare 00000000-1111-2222-3333-444444444444 in text"
        result = _replay.canonical_text(text)
        # Without a [a-z]{2,4}_ prefix, the pattern won't match
        assert "00000000-1111-2222-3333-444444444444" in result


# ── canonical_messages tests ─────────────────────────────────────────────


class TestCanonicalMessages:
    def test_messages_runtime_ids_replaced(self):
        rid = _rid("ses", 0)
        messages = [
            {"role": "user", "content": f"look up {rid}"},
        ]
        result = _replay.canonical_messages(messages)
        assert rid not in result
        assert "<ID1>" in result

    def test_messages_seed_ids_preserved(self):
        messages = [
            {"role": "user", "content": "look up cus_zs"},
        ]
        result = _replay.canonical_messages(messages)
        assert "cus_zs" in result
        assert "<ID" not in result


class TestCanonicalTools:
    def test_tools_canonicalised(self):
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_accounts",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
        result = _replay.canonical_tools(tools)
        assert "get_accounts" in result

    def test_none_tools(self):
        assert _replay.canonical_tools(None) == "[]"


class TestCanonicalParams:
    def test_params_canonicalised(self):
        params = {
            "temperature": 0,
            "max_tokens": 1024,
            "extra_body": {"thinking": {"type": "disabled"}},
        }
        result = _replay.canonical_params(params)
        assert "temperature" in result
        assert "max_tokens" in result
        assert "thinking" in result


# ── replay_key tests ─────────────────────────────────────────────────────


class TestReplayKey:
    def test_deterministic(self):
        messages = [{"role": "user", "content": "hello"}]
        tools = [{"type": "function", "function": {"name": "test"}}]
        params = {"temperature": 0}

        k1 = _replay.replay_key("deepseek-v4-pro", messages, tools, params)
        k2 = _replay.replay_key("deepseek-v4-pro", messages, tools, params)
        assert k1 == k2
        assert len(k1) == 64  # sha256 hex

    def test_different_model_different_key(self):
        messages = [{"role": "user", "content": "hello"}]
        k1 = _replay.replay_key("deepseek-v4-pro", messages, None, {})
        k2 = _replay.replay_key("deepseek-flash", messages, None, {})
        assert k1 != k2

    def test_different_temperature_different_key(self):
        messages = [{"role": "user", "content": "hello"}]
        k1 = _replay.replay_key("m", messages, None, {"temperature": 0})
        k2 = _replay.replay_key("m", messages, None, {"temperature": 1})
        assert k1 != k2

    def test_id_normalised_key_same(self):
        rid_a = _rid("acc", 0)
        rid_b = _rid("acc", 1)  # Different 26-char suffix, same prefix
        messages_a = [{"role": "user", "content": f"look up {rid_a}"}]
        messages_b = [{"role": "user", "content": f"look up {rid_b}"}]
        k1 = _replay.replay_key("m", messages_a, None, {})
        k2 = _replay.replay_key("m", messages_b, None, {})
        assert k1 == k2  # Different runtime IDs → same canonical form

    def test_seed_id_key_differs_from_runtime(self):
        rid = _rid("cus", 0)
        msg_seed = [{"role": "user", "content": "look up cus_zs"}]
        msg_runtime = [{"role": "user", "content": f"look up {rid}"}]
        k1 = _replay.replay_key("m", msg_seed, None, {})
        k2 = _replay.replay_key("m", msg_runtime, None, {})
        # Seed ID preserved verbatim, runtime ID replaced → different keys
        assert k1 != k2


# ── strip_wall_fields tests ──────────────────────────────────────────────


class TestStripWallFields:
    def test_strips_wall_keys(self):
        obj = {
            "balance_cents": 100000,
            "balance_wall": "2026-10-25T00:00:00Z",
            "nested": {"x_wall": 1, "y": 2},
        }
        result = _replay.strip_wall_fields(obj)
        assert "balance_cents" in result
        assert "balance_wall" not in result
        assert "x_wall" not in result["nested"]
        assert result["nested"]["y"] == 2

    def test_strips_from_lists(self):
        obj = [{"a": 1, "b_wall": "xxx"}, {"c_wall": "yyy", "d": 2}]
        result = list(_replay.strip_wall_fields(obj))
        assert result[0] == {"a": 1}
        assert result[1] == {"d": 2}

    def test_key_not_ending_wall_preserved(self):
        obj = {"normal": "value_wall_suffix"}
        result = _replay.strip_wall_fields(obj)
        assert "normal" in result

    def test_primitive_unchanged(self):
        assert _replay.strip_wall_fields(42) == 42
        assert _replay.strip_wall_fields("hello") == "hello"
        assert _replay.strip_wall_fields(None) is None

    def test_does_not_mutate_input(self):
        obj = {"x": 1, "y_wall": 2}
        _replay.strip_wall_fields(obj)
        assert "y_wall" in obj  # Original not mutated


# ── replay_schema_version tests ──────────────────────────────────────────


class TestReplaySchemaVersion:
    def test_deterministic(self):
        v1 = _replay.replay_schema_version([{"name": "test"}], {"system": "hello"})
        v2 = _replay.replay_schema_version([{"name": "test"}], {"system": "hello"})
        assert v1 == v2
        assert len(v1) == 64

    def test_different_tools_different_version(self):
        v1 = _replay.replay_schema_version([{"name": "a"}], {"s": "x"})
        v2 = _replay.replay_schema_version([{"name": "b"}], {"s": "x"})
        assert v1 != v2


# ── RecordTransport tests ────────────────────────────────────────────────


class TestRecordTransport:
    def test_writes_header_and_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(_replay, "_replay_dir", return_value=Path(tmp)):
                transport = _replay.RecordTransport(
                    _ok_response,
                    schema_version="abc123",
                    profile="b1",
                    tools_schema=[],
                    system_prompts={"sys": "test"},
                )
                req = httpx.Request(
                    "POST",
                    "https://api.deepseek.com/beta/chat/completions",
                    json={
                        "model": "deepseek-v4-pro",
                        "messages": [{"role": "user", "content": "hi"}],
                        "temperature": 0,
                    },
                )
                response = transport(req)
                assert response.status_code == 200

                replay_file = Path(tmp) / "replay.jsonl"
                assert replay_file.exists()
                lines = replay_file.read_text().strip().splitlines()
                assert len(lines) >= 2  # header + 1 entry

                header = json.loads(lines[0])
                assert header["schema_version"] == "abc123"

    def test_sensitive_keys_scrubbed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(_replay, "_replay_dir", return_value=Path(tmp)):
                transport = _replay.RecordTransport(
                    _empty_response,
                    schema_version="abc123",
                    profile="b1",
                    tools_schema=[],
                    system_prompts={"sys": "test"},
                )
                req = httpx.Request(
                    "POST",
                    "https://api.deepseek.com/beta/chat/completions",
                    json={
                        "model": "deepseek-v4-pro",
                        "messages": [{"role": "user", "content": "hi"}],
                        "api_key": "sk-secret-12345",
                        "authorization": "Bearer token123",
                    },
                )
                transport(req)

                replay_file = Path(tmp) / "replay.jsonl"
                lines = replay_file.read_text().strip().splitlines()
                data_entry = json.loads(lines[1])
                params = data_entry.get("params", {})
                assert params.get("api_key") == "<REDACTED>"
                assert params.get("authorization") == "<REDACTED>"

    def test_no_jwt_in_recording(self):
        """Verify JWT tokens are scrubbed from recorded entries (E6)."""
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(_replay, "_replay_dir", return_value=Path(tmp)):
                transport = _replay.RecordTransport(
                    _empty_response,
                    schema_version="abc123",
                    profile="b1",
                    tools_schema=[],
                    system_prompts={"sys": "test"},
                )
                req = httpx.Request(
                    "POST",
                    "https://api.deepseek.com/beta/chat/completions",
                    json={
                        "model": "deepseek-v4-pro",
                        "messages": [],
                        "Authorization": ("Bearer eyJhbGciOiJFZERTQSJ9.eyJzdWIiOiJjdXNfYSJ9.sig"),
                        "assistant_token": "tok_deadbeef",
                    },
                )
                transport(req)

                replay_file = Path(tmp) / "replay.jsonl"
                content = replay_file.read_text()
                assert "eyJhbGci" not in content  # JWT not leaked
                assert "tok_deadbeef" not in content  # token not leaked
                assert "<REDACTED>" in content  # redacted present


# ── ReplayLoader tests ───────────────────────────────────────────────────


class TestReplayLoader:
    def _write_replay_file(self, directory: Path, schema_version: str, entries: list[dict]) -> Path:
        path = directory / "replay.jsonl"
        with open(path, "w") as fh:
            fh.write(
                json.dumps(
                    {
                        "schema_version": schema_version,
                        "profile": "b1",
                        "tools": [],
                        "systems": {},
                    }
                )
                + "\n"
            )
            for entry in entries:
                fh.write(json.dumps(entry) + "\n")
        return path

    def test_load_and_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_replay_file(
                Path(tmp),
                "abc123",
                [
                    {
                        "key": "k1",
                        "model": "deepseek-v4-pro",
                        "params": {},
                        "response": {"choices": [{"message": {"content": "ok"}}]},
                    }
                ],
            )
            with mock.patch.object(_replay, "_replay_dir", return_value=Path(tmp)):
                loader = _replay.ReplayLoader(schema_version="abc123")
                assert loader.key_count == 1
                entry = loader.lookup("k1")
                assert entry["response"]["choices"][0]["message"]["content"] == "ok"

    def test_miss_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_replay_file(
                Path(tmp),
                "abc123",
                [{"key": "k1", "model": "m", "params": {}, "response": {}}],
            )
            with mock.patch.object(_replay, "_replay_dir", return_value=Path(tmp)):
                loader = _replay.ReplayLoader(schema_version="abc123")
                with pytest.raises(_replay.ReplayMissError, match="Replay miss"):
                    loader.lookup("nope")

    def test_stale_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_replay_file(Path(tmp), "old_version", [])
            with mock.patch.object(_replay, "_replay_dir", return_value=Path(tmp)):
                with pytest.raises(_replay.ReplayStaleError, match="version mismatch"):
                    _replay.ReplayLoader(schema_version="new_version")

    def test_missing_file_raises(self):
        with mock.patch.object(_replay, "_replay_dir", return_value=Path("/nonexistent/path")):
            with pytest.raises(_replay.ReplayMissError, match="Replay file not found"):
                _replay.ReplayLoader(schema_version="abc")

    def test_empty_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            replay_path = Path(tmp) / "replay.jsonl"
            replay_path.write_text("")
            with mock.patch.object(_replay, "_replay_dir", return_value=Path(tmp)):
                with pytest.raises(_replay.ReplayMissError, match="empty"):
                    _replay.ReplayLoader(schema_version="abc")


# ── _scrub_sensitive tests ───────────────────────────────────────────────


class TestScrubSensitive:
    def test_scrubs_authorization(self):
        result = _replay._scrub_sensitive({"Authorization": "Bearer xyz"})
        assert result["Authorization"] == "<REDACTED>"

    def test_scrubs_api_key(self):
        result = _replay._scrub_sensitive({"api_key": "sk-secret"})
        assert result["api_key"] == "<REDACTED>"

    def test_scrubs_service_token(self):
        result = _replay._scrub_sensitive({"service_token": "abc"})
        assert result["service_token"] == "<REDACTED>"

    def test_scrubs_nested(self):
        result = _replay._scrub_sensitive({"headers": {"Authorization": "Bearer x"}})
        assert result["headers"]["Authorization"] == "<REDACTED>"

    def test_preserves_non_sensitive(self):
        result = _replay._scrub_sensitive({"model": "deepseek-v4-pro", "temperature": 0})
        assert result["model"] == "deepseek-v4-pro"
        assert result["temperature"] == 0

    def test_depth_limit(self):
        deeply_nested = {}
        current = deeply_nested
        for _ in range(15):
            current["next"] = {}
            current = current["next"]
        result = _replay._scrub_sensitive(deeply_nested)
        # Should not raise RecursionError
        assert isinstance(result, dict)

    # ── P2-2 token_usage fields must survive scrubbing ──────────────────

    def test_preserves_token_usage(self):
        """completion_tokens/prompt_tokens/total_tokens must NOT be scrubbed."""
        usage = {
            "completion_tokens": 150,
            "prompt_tokens": 800,
            "total_tokens": 950,
        }
        result = _replay._scrub_sensitive(usage)
        assert result["completion_tokens"] == 150
        assert result["prompt_tokens"] == 800
        assert result["total_tokens"] == 950
