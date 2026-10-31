"""ChatDeepSeek factory pinned to the beta strict endpoint (ADR-002).

``thinking.type=disabled`` + ``temperature=0``.  The API key lives only on the returned
client object and is never written into state, configurable, checkpoints, prompts or logs.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import os
from pathlib import Path

import httpx
from langchain_deepseek import ChatDeepSeek

MODEL = "deepseek-v4-pro"
BETA_API_BASE = "https://api.deepseek.com/beta"


def load_api_key() -> str:
    """Read DEEPSEEK_API_KEY from the environment, then from ``.env``; never print it."""

    key = os.getenv("DEEPSEEK_API_KEY", "")
    if key:
        return key
    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("DEEPSEEK_API_KEY="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    return value
    raise RuntimeError("DEEPSEEK_API_KEY is not set; no API calls made")


def _recording_contract(settings: dict) -> tuple[str, list, dict[str, str]]:
    """Fingerprint actual schemas, adapter prompts/builders and model settings.

    Source hashes deliberately invalidate recordings on adapter implementation
    changes too: dynamic prompt construction must never evade the freeze gate.
    """
    from safebank_contracts.schema import plan_tool

    from services.agent.app.llm.replay import replay_schema_version
    from services.agent.app.llm.schema import read_tools, understanding_tool

    tools = [understanding_tool(), plan_tool(), *read_tools()]
    systems = {}
    for name in (
        "understand",
        "gather",
        "plan",
        "respond",
        "verify",
        "schema",
        "_common",
        "fixed_templates",
        "redact",
        "replay",
        "client",
    ):
        module = importlib.import_module(f"services.agent.app.llm.{name}")
        systems[f"{name}.source"] = hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
        for key, value in vars(module).items():
            if "SYSTEM" in key and isinstance(value, str):
                systems[f"{name}.{key}"] = value
    return replay_schema_version(tools, systems, settings), tools, systems


def _async_transport(handler) -> httpx.MockTransport:
    async def send(request: httpx.Request) -> httpx.Response:
        # Sync injected transports remain the sole network authority. Offload
        # their work and detach the sync response stream for httpx.AsyncClient.
        response = await asyncio.to_thread(handler, request)
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            content=response.content,
            request=request,
        )

    return httpx.MockTransport(send)


def make_llm(api_key: str | None = None, http_client=None) -> ChatDeepSeek:
    """Build the production DeepSeek client, optionally recording or replaying.

    Default live behavior is retained. Record/replay files are selected only by
    process configuration, never by L1/user request fields. Replay constructs
    exclusively in-process transports and needs no credentials. Injected sync
    HTTP clients also service async calls, keeping tests hermetic.
    """
    from services.agent.app.llm.replay import (
        LLMMode,
        RecordTransport,
        ReplayLoader,
        runtime_location,
    )

    mode = LLMMode.from_env()
    kwargs: dict = {
        "model": MODEL,
        "api_base": BETA_API_BASE,
        "temperature": 0,
        "max_retries": 0,
        "extra_body": {"thinking": {"type": "disabled"}},
    }
    if mode == LLMMode.REPLAY:
        path, profile = runtime_location()
        version, _, _ = _recording_contract(kwargs)
        loader = ReplayLoader(schema_version=version, profile=profile, path=path)
        kwargs["api_key"] = "offline-replay-placeholder"
        kwargs["http_client"] = httpx.Client(transport=httpx.MockTransport(loader), trust_env=False)
        kwargs["http_async_client"] = httpx.AsyncClient(
            transport=httpx.MockTransport(loader), trust_env=False
        )
    elif mode == LLMMode.RECORD:
        path, profile = runtime_location()
        version, tools, systems = _recording_contract(kwargs)
        # Constructing these transports is allowed only in live/record modes.
        provider = http_client if http_client is not None else httpx.Client()
        recorder = RecordTransport(
            provider.send,
            schema_version=version,
            profile=profile,
            tools_schema=tools,
            system_prompts=systems,
            path=path,
        )
        kwargs["http_client"] = httpx.Client(
            transport=httpx.MockTransport(recorder), trust_env=False
        )
        # Current production adapters invoke synchronously; use the same recorder
        # and injected provider for ainvoke too, never an implicit second client.
        kwargs["http_async_client"] = httpx.AsyncClient(
            transport=_async_transport(recorder), trust_env=False
        )
        if api_key is not None:
            kwargs["api_key"] = api_key
    else:
        if api_key is not None:
            kwargs["api_key"] = api_key
        if http_client is not None:
            kwargs["http_client"] = http_client
            kwargs["http_async_client"] = httpx.AsyncClient(
                transport=_async_transport(http_client.send), trust_env=False
            )
    return ChatDeepSeek(**kwargs)
