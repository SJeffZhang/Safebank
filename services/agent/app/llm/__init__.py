"""C1b DeepSeek strict adapters (understand / gather / plan).

These adapters fit the C1a ``Ports`` shape; they propose/collect, never authorize or write.
"""

from __future__ import annotations

from services.agent.app.llm.client import BETA_API_BASE, MODEL, load_api_key, make_llm
from services.agent.app.llm.gather import GatherAdapter, ReadExecutor, ReadResult
from services.agent.app.llm.plan import PlanAdapter
from services.agent.app.llm.schema import read_tools, understanding_tool
from services.agent.app.llm.understand import UnderstandAdapter

__all__ = (
    "BETA_API_BASE",
    "GatherAdapter",
    "MODEL",
    "PlanAdapter",
    "ReadExecutor",
    "ReadResult",
    "UnderstandAdapter",
    "load_api_key",
    "make_llm",
    "read_tools",
    "understanding_tool",
)
