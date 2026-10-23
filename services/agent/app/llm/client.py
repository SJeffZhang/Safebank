"""ChatDeepSeek factory pinned to the beta strict endpoint (ADR-002).

``thinking.type=disabled`` + ``temperature=0``.  The API key lives only on the returned
client object and is never written into state, configurable, checkpoints, prompts or logs.
"""

from __future__ import annotations

import os
from pathlib import Path

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


def make_llm(api_key: str | None = None, http_client=None) -> ChatDeepSeek:
    """Build a DeepSeek client for the beta strict endpoint, thinking disabled.

    ``api_key=None`` defers to the ``DEEPSEEK_API_KEY`` environment variable (the default
    factory).  ``http_client`` is an optional ``httpx.Client`` used by tests to capture the
    final HTTP payload without a network.
    """

    kwargs: dict = {
        "model": MODEL,
        "api_base": BETA_API_BASE,
        "temperature": 0,
        "max_retries": 0,
        "extra_body": {"thinking": {"type": "disabled"}},
    }
    if api_key is not None:
        kwargs["api_key"] = api_key
    if http_client is not None:
        kwargs["http_client"] = http_client
    return ChatDeepSeek(**kwargs)
