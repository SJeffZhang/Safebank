"""Production make_llm record/replay acceptance; all provider IO is in-process."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from langchain_core.messages import HumanMessage, ToolMessage
from openai import APIStatusError

from services.agent.app.llm.client import make_llm
from services.agent.app.llm.replay import ReplayMissError, ReplayStaleError
from tests.agent.llm_mock import text_response, tool_response

OLD_UTT = "utt_01J9JH2M4PQ6RVX8T3EFGAB7K5"
NEW_UTT = "utt_02J9JH2M4PQ6RVX8T3EFGAB7K5"
OLD_SRC = "src_a1b2c3d4-e5f6-7890-abcd-ef1234567890"
NEW_SRC = "src_11111111-2222-3333-4444-555555555555"
CALL = "call_00_sDkWb8RQFLPMXzFy3j6n7444"


@pytest.fixture
def recording(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_MODE", "record")
    monkeypatch.setenv("LLM_PROFILE", "B2")
    monkeypatch.setenv("LLM_CASE_ID", "transfer")
    monkeypatch.setenv("LLM_REPLAY_ROOT", str(tmp_path))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    return tmp_path / "B2" / "transfer" / "replay.jsonl"


def no_network(request):
    raise AssertionError("replay attempted provider IO")


def replay(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "replay")
    # No API key; an injected network trap must never be used.
    return make_llm(http_client=httpx.Client(transport=httpx.MockTransport(no_network)))


def test_factory_records_and_rebinds_fresh_ids_and_real_provider_calls(recording, monkeypatch):
    calls = []

    def provider(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            body = tool_response("propose", {"utterance_id": OLD_UTT, "source_ref": OLD_SRC}).json()
            body["choices"][0]["message"]["tool_calls"][0]["id"] = CALL
            return httpx.Response(200, json=body)
        return text_response("完成")

    llm = make_llm("synthetic-key", httpx.Client(transport=httpx.MockTransport(provider)))
    before = [
        HumanMessage(content=json.dumps({"utt": OLD_UTT, "src": OLD_SRC, "expires_wall": "old"}))
    ]
    first = llm.invoke(before)
    llm.invoke([*before, first, ToolMessage(content="ok", tool_call_id=CALL)])
    assert recording.exists()
    raw = recording.read_text()
    assert "synthetic-key" not in raw
    assert OLD_UTT not in raw and OLD_SRC not in raw
    assert calls[0]["model"] == "deepseek-v4-pro"
    assert calls[0]["thinking"] == {"type": "disabled"}

    offline = replay(monkeypatch)
    after = [
        HumanMessage(content=json.dumps({"utt": NEW_UTT, "src": NEW_SRC, "expires_wall": "new"}))
    ]
    rebound = offline.invoke(after)
    assert rebound.tool_calls[0]["args"] == {"utterance_id": NEW_UTT, "source_ref": NEW_SRC}
    assert rebound.tool_calls[0]["id"] != CALL
    second = offline.invoke(
        [*after, rebound, ToolMessage(content="ok", tool_call_id=rebound.tool_calls[0]["id"])]
    )
    assert second.content == "完成"
    assert len(calls) == 2


def test_repeated_key_responses_are_consumed_in_order(recording, monkeypatch):
    responses = iter([text_response("bad"), text_response("good")])
    llm = make_llm(
        "synthetic", httpx.Client(transport=httpx.MockTransport(lambda _: next(responses)))
    )
    assert llm.invoke("same").content == "bad"
    assert llm.invoke("same").content == "good"
    offline = replay(monkeypatch)
    assert offline.invoke("same").content == "bad"
    assert offline.invoke("same").content == "good"
    with pytest.raises(ReplayMissError):
        offline.invoke("same")


@pytest.mark.parametrize("status", [402, 403, 429])
def test_provider_errors_propagate_once_without_recording(recording, status):
    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"message": "quota", "type": "quota"}})

    llm = make_llm("synthetic", httpx.Client(transport=httpx.MockTransport(provider)))
    with pytest.raises(APIStatusError):
        llm.invoke("hello")
    assert len(calls) == 1
    assert not recording.exists()


@pytest.mark.parametrize("mutation", ["schema", "profile", "corruption"])
def test_invalid_recordings_fail_closed(recording, monkeypatch, mutation):
    make_llm(
        "synthetic", httpx.Client(transport=httpx.MockTransport(lambda _: text_response("ok")))
    ).invoke("hi")
    lines = recording.read_text().splitlines()
    header = json.loads(lines[0])
    if mutation == "schema":
        header["schema_version"] = "obsolete"
    elif mutation == "profile":
        header["profile"] = "B1"
    else:
        lines.append("{corrupt")
    lines[0] = json.dumps(header)
    recording.write_text("\n".join(lines) + "\n")
    with pytest.raises(ReplayStaleError):
        replay(monkeypatch)


def test_missing_recording_needs_no_key_and_never_constructs_network(recording, monkeypatch):
    monkeypatch.setenv("LLM_MODE", "replay")
    monkeypatch.setattr(httpx, "HTTPTransport", lambda *a, **k: pytest.fail("live transport"))
    monkeypatch.setattr(
        httpx, "AsyncHTTPTransport", lambda *a, **k: pytest.fail("async live transport")
    )
    with pytest.raises(ReplayMissError):
        make_llm()


def test_replay_sync_and_async_construct_no_live_transport(recording, monkeypatch):
    llm = make_llm(
        "synthetic", httpx.Client(transport=httpx.MockTransport(lambda _: text_response("ok")))
    )
    llm.invoke("sync")
    asyncio.run(llm.ainvoke("async"))
    monkeypatch.setenv("LLM_MODE", "replay")
    monkeypatch.setattr(httpx, "HTTPTransport", lambda *a, **k: pytest.fail("sync live transport"))
    monkeypatch.setattr(
        httpx, "AsyncHTTPTransport", lambda *a, **k: pytest.fail("async live transport")
    )
    offline = make_llm()
    assert offline.invoke("sync").content == "ok"
    assert asyncio.run(offline.ainvoke("async")).content == "ok"


def test_never_reuses_response_only_authorization_id(recording, monkeypatch):
    llm = make_llm(
        "synthetic",
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _: tool_response("propose", {"source_ref": OLD_SRC})
            )
        ),
    )
    llm.invoke("no source in this request")
    with pytest.raises(ReplayStaleError, match="Unbound"):
        replay(monkeypatch).invoke("no source in this request")


def test_changed_business_payload_misses_instead_of_rebinding(recording, monkeypatch):
    make_llm(
        "synthetic", httpx.Client(transport=httpx.MockTransport(lambda _: text_response("ok")))
    ).invoke("transfer 100 cents")
    with pytest.raises(ReplayMissError):
        replay(monkeypatch).invoke("transfer 200 cents")


def test_changed_production_prompt_invalidates_recording(recording, monkeypatch):
    from services.agent.app.llm import plan

    make_llm(
        "synthetic", httpx.Client(transport=httpx.MockTransport(lambda _: text_response("ok")))
    ).invoke("hi")
    monkeypatch.setattr(plan, "_SYSTEM", plan._SYSTEM + " revised policy")
    with pytest.raises(ReplayStaleError, match="schema"):
        replay(monkeypatch)


def test_changed_actual_schema_or_model_setting_invalidates(recording, monkeypatch):
    from services.agent.app.llm import client, schema
    from services.agent.app.llm.replay import replay_schema_version

    first = client._recording_contract({"temperature": 0})[0]
    assert client._recording_contract({"temperature": 1})[0] != first
    monkeypatch.setattr(schema, "read_tools", lambda: [{"name": "modified"}])
    assert client._recording_contract({"temperature": 0})[0] != first
    assert replay_schema_version([], {}, {"model": "a"}) != replay_schema_version(
        [], {}, {"model": "b"}
    )


def test_secrets_in_headers_arguments_and_plain_content_never_written(recording):
    secret_key = "synthetic-secret-key"
    jwt = "eyJhbGciOiJFZERTQSJ9.eyJzdWIiOiJjdXNfYSJ9.sig"

    def provider(request):
        result = tool_response("propose", {"pin": "984321", "otp": "276543", "jwt": jwt}).json()
        result["choices"][0]["message"]["content"] = (
            f"{secret_key} Bearer {jwt} PIN is 984321 验证码：276543"
        )
        return httpx.Response(200, json=result)

    make_llm(secret_key, httpx.Client(transport=httpx.MockTransport(provider))).invoke("hi")
    raw = recording.read_text()
    for secret in (secret_key, jwt, "984321", "276543", "Bearer"):
        assert secret not in raw
    assert recording.stat().st_mode & 0o777 == 0o600
    assert (
        json.loads(recording.read_text().splitlines()[1])["response"]["usage"]["total_tokens"] == 15
    )


def test_two_recorders_preserve_one_header_and_sequence(recording, monkeypatch):
    first = make_llm(
        "synthetic", httpx.Client(transport=httpx.MockTransport(lambda _: text_response("one")))
    )
    second = make_llm(
        "synthetic", httpx.Client(transport=httpx.MockTransport(lambda _: text_response("two")))
    )
    first.invoke("hi")
    second.invoke("hi")
    assert len(recording.read_text().splitlines()) == 3
    offline = replay(monkeypatch)
    assert offline.invoke("hi").content == "one"
    assert offline.invoke("hi").content == "two"


@pytest.mark.parametrize(
    "name,value", [("LLM_MODE", "guess"), ("LLM_PROFILE", "B0"), ("LLM_CASE_ID", "../../elsewhere")]
)
def test_invalid_runtime_configuration_fails_before_provider(recording, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        make_llm("synthetic", httpx.Client(transport=httpx.MockTransport(no_network)))


def test_record_mode_rejects_stale_existing_file_before_provider(recording, monkeypatch):
    make_llm(
        "synthetic", httpx.Client(transport=httpx.MockTransport(lambda _: text_response("ok")))
    ).invoke("hi")
    lines = recording.read_text().splitlines()
    header = json.loads(lines[0])
    header["schema_version"] = "stale"
    lines[0] = json.dumps(header)
    recording.write_text("\n".join(lines) + "\n")
    with pytest.raises(ReplayStaleError):
        make_llm("synthetic", httpx.Client(transport=httpx.MockTransport(no_network)))


def test_malformed_retry_does_not_persist_quoted_secrets(recording):
    def provider(request):
        result = tool_response("propose", {}).json()
        result["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = (
            '{"pin": "984321", "otp": "276543", "refresh_token": "synthetic-refresh"'
        )
        return httpx.Response(200, json=result)

    make_llm("synthetic", httpx.Client(transport=httpx.MockTransport(provider))).invoke("hi")
    for secret in ("984321", "276543", "synthetic-refresh"):
        assert secret not in recording.read_text()


def test_response_token_fields_are_scrubbed_without_removing_usage(recording):
    def provider(request):
        result = text_response("ok").json()
        result["refresh_token"] = "opaque-refresh-credential"
        result["token"] = "opaque-token-credential"
        return httpx.Response(200, json=result)

    make_llm("synthetic", httpx.Client(transport=httpx.MockTransport(provider))).invoke("hi")
    raw = recording.read_text()
    assert "opaque-refresh-credential" not in raw
    assert "opaque-token-credential" not in raw
    assert json.loads(raw.splitlines()[1])["response"]["usage"]["total_tokens"] == 15


def test_malformed_tool_retry_sequence_is_preserved(recording, monkeypatch):
    from tests.agent.llm_mock import malformed_tool_response

    responses = iter([malformed_tool_response("propose"), tool_response("propose", {"ok": True})])
    llm = make_llm(
        "synthetic", httpx.Client(transport=httpx.MockTransport(lambda _: next(responses)))
    )
    assert llm.invoke("identical retry").invalid_tool_calls
    assert llm.invoke("identical retry").tool_calls[0]["args"] == {"ok": True}
    offline = replay(monkeypatch)
    assert offline.invoke("identical retry").invalid_tool_calls
    assert offline.invoke("identical retry").tool_calls[0]["args"] == {"ok": True}


def test_checksum_damage_and_unbound_literal_placeholder_fail_closed(recording, monkeypatch):
    make_llm(
        "synthetic", httpx.Client(transport=httpx.MockTransport(lambda _: text_response("<ID999>")))
    ).invoke("hi")
    with pytest.raises(ReplayStaleError, match="Unbound"):
        replay(monkeypatch).invoke("hi")
    lines = recording.read_text().splitlines()
    entry = json.loads(lines[1])
    entry["response"]["choices"][0]["message"]["content"] = "silently changed"
    lines[1] = json.dumps(entry)
    recording.write_text("\n".join(lines) + "\n")
    with pytest.raises(ReplayStaleError, match="checksum"):
        replay(monkeypatch)


def test_atomic_failure_preserves_previous_recording(recording, monkeypatch):
    from services.agent.app.llm import replay as module

    responses = iter([text_response("one"), text_response("two")])
    llm = make_llm(
        "synthetic", httpx.Client(transport=httpx.MockTransport(lambda _: next(responses)))
    )
    llm.invoke("hi")
    original = recording.read_bytes()

    def fail_replace(*args):
        raise OSError("synthetic interrupted replace")

    monkeypatch.setattr(module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="interrupted replace"):
        llm.invoke("hi")
    assert recording.read_bytes() == original
    assert replay(monkeypatch).invoke("hi").content == "one"


def test_numeric_secret_echo_stays_valid_json(recording):
    def provider(request):
        result = text_response("ok").json()
        result["pin"] = 984321
        result["numeric_echo"] = 984321
        return httpx.Response(200, json=result)

    make_llm("synthetic", httpx.Client(transport=httpx.MockTransport(provider))).invoke("hi")
    entry = json.loads(recording.read_text().splitlines()[1])
    assert entry["response"]["numeric_echo"] == "<REDACTED>"
    assert "984321" not in recording.read_text()
