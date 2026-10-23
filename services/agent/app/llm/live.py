"""Minimal live entry for C1b adapters (manual; offline CI needs no key).

Runs at most ``understand(2) + plan(3) + gather(1)`` synthetic samples (6 base requests,
bounded to 8 total including validation retries).  Evidence records only sanitized tool
schema hashes, response validity and latency — never the API key, headers, or real data.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import httpx
from safebank_contracts.schema import plan_tool

from services.agent.app.graph.state import AgentContext, Understanding, UtteranceRef
from services.agent.app.llm._common import new_ulid
from services.agent.app.llm.client import load_api_key, make_llm
from services.agent.app.llm.gather import GatherAdapter, ReadResult
from services.agent.app.llm.plan import PlanAdapter
from services.agent.app.llm.schema import read_tools, understanding_tool
from services.agent.app.llm.understand import UnderstandAdapter

REQUEST_LIMIT = 8
EVIDENCE_DIR = Path("docs/acceptance/c1b-deepseek-live")


class _BudgetTransport(httpx.BaseTransport):
    def __init__(self, transport: httpx.BaseTransport, limit: int) -> None:
        self._transport = transport
        self._limit = limit
        self.count = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.count += 1
        if self.count > self._limit:
            raise RuntimeError(f"live request budget exceeded ({self._limit})")
        return self._transport.handle_request(request)


def _sha(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _ctx() -> AgentContext:
    return AgentContext(
        assistant_token="live-synthetic", customer_id="cus_syn", session_id="ses_syn"
    )


def main() -> Path:
    key = load_api_key()
    transport = _BudgetTransport(httpx.HTTPTransport(), REQUEST_LIMIT)
    llm = make_llm(api_key=key, http_client=httpx.Client(transport=transport))
    understand = UnderstandAdapter(llm)
    plan = PlanAdapter(llm)

    records: list[dict] = [
        {
            "name": "schemas",
            "understand_tool_sha": _sha(understanding_tool()),
            "plan_tool_sha": _sha(plan_tool()),
            "read_tool_names": sorted(tool["function"]["name"] for tool in read_tools()),
        }
    ]

    def record(name: str, fn) -> None:
        started = time.perf_counter()
        try:
            result = fn()
            records.append(
                {
                    "name": name,
                    "ok": True,
                    "result_kind": type(result).__name__,
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                }
            )
        except Exception as exc:  # noqa: BLE001 — sanitized live evidence
            records.append(
                {
                    "name": name,
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                }
            )

    def understand_sample(text: str) -> Understanding:
        state = {
            "utterances": [UtteranceRef(id=f"utt_syn_{new_ulid()[:8]}", kind="typed", text=text)]
        }
        return understand(state, _ctx())

    for text in ("给王五转800元", "查一下我的活期余额"):
        record(f"understand:{text}", lambda t=text: understand_sample(t))

    def plan_sample() -> object:
        state = {
            "session_id": "ses_syn",
            "utterances": [UtteranceRef(id="utt_syn_plan", kind="typed", text="给王五转800元")],
            "understanding": Understanding(intents=["transfer"], needs_write=True),
            "reads": [],
        }
        return plan(state, _ctx())

    for _ in range(3):
        record("plan", plan_sample)

    def fake_read(_name: str, _args: dict) -> ReadResult:
        return ReadResult(
            data={"accounts": [{"account_id": "acc_zs_chk", "balance_cents": 600000}]},
            source_ref="src_syn",
        )

    def gather_sample() -> object:
        state = {
            "utterances": [UtteranceRef(id="utt_syn_gather", kind="typed", text="我活期余额多少")],
            "understanding": Understanding(intents=["balance"], needs_write=False),
        }
        return GatherAdapter(llm, fake_read, max_rounds=1)(state, _ctx())

    record("gather", gather_sample)

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    out = EVIDENCE_DIR / "results.jsonl"
    with out.open("a") as stream:
        for entry in records:
            stream.write(
                json.dumps(entry, ensure_ascii=False, default=str).replace(key, "[REDACTED]") + "\n"
            )
    print(f"wrote {len(records)} records to {out}")
    return out


if __name__ == "__main__":
    main()
