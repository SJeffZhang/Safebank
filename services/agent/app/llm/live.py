"""Manual live entry for C1b adapters (offline CI needs no key).

Runs ``understand(2) + plan(3 different scenarios) + gather(1)`` (6 base requests, hard
budget 8 total).  Any sample failure, semantic assertion failure, or budget exhaustion makes
the process exit non-zero; evidence is written *before* exiting and never contains the key,
headers, or real personal data.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path

import httpx
from pydantic import ValidationError
from safebank_contracts.plan import PlanDraft
from safebank_contracts.schema import plan_tool

from services.agent.app.graph.state import (
    AgentContext,
    ReadRecord,
    Understanding,
    UtteranceRef,
)
from services.agent.app.llm.client import load_api_key, make_llm
from services.agent.app.llm.gather import GatherAdapter, ReadResult
from services.agent.app.llm.plan import PlanAdapter
from services.agent.app.llm.redact import resolve_pointer
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


def _error_fields(exc: Exception) -> list[dict] | None:
    if isinstance(exc, ValidationError):
        return [
            {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
            for error in exc.errors()
        ]
    return None


def _draft_summary(draft: PlanDraft) -> dict:
    first = draft.steps[0]
    args = first.args.model_dump(mode="json")
    return {
        "tool": first.tool,
        "n_steps": len(draft.steps),
        "amount_cents": args.get("amount_cents"),
        "entity": args.get("payee_id") or args.get("to_account_id") or args.get("card_id"),
        "source_kinds": sorted(
            {source.source.kind for step in draft.steps for source in step.arg_sources}
        ),
        "sources": [
            {"param": source.param, "kind": source.source.kind}
            for step in draft.steps
            for source in step.arg_sources
        ],
    }


def _read_value(reads: list[ReadRecord], ref: str, pointer: str):
    read = next((r for r in reads if r.source_ref == ref), None)
    if read is None:
        return None
    try:
        return resolve_pointer(read.data, pointer)
    except ValueError:
        return None


def _check_read_param(sources, param, expected, reads, ref, pointer) -> list[str]:
    problems = []
    source = sources.get(param)
    if source is None:
        problems.append(f"{param} has no source")
    elif source.kind != "read":
        problems.append(f"{param} source kind {source.kind} != read")
    elif source.ref != ref:
        problems.append(f"{param} ref {source.ref} != {ref}")
    else:
        value = _read_value(reads, ref, pointer)
        if value != expected:
            problems.append(f"{param} read value {value!r} != {expected!r}")
    return problems


def _check_utterance_param(sources, param, text, digits) -> list[str]:
    problems = []
    source = sources.get(param)
    if source is None:
        problems.append(f"{param} has no source")
    elif source.kind != "utterance":
        problems.append(f"{param} source kind {source.kind} != utterance")
    else:
        span = source.span
        if not (0 <= span[0] < span[1] <= len(text)):
            problems.append(f"{param} span {span} out of bounds")
        elif text[span[0] : span[1]] not in (digits, digits + "元"):
            problems.append(f"{param} span {span} slice {text[span[0] : span[1]]!r} != {digits!r}")
    return problems


def _source_problems(draft: PlanDraft, utterances: list, reads: list[ReadRecord]) -> list[str]:
    problems: list[str] = []
    utt_by_id = {utterance.id: utterance for utterance in utterances}
    read_by_ref = {read.source_ref: read for read in reads}
    step_ids: set[str] = set()
    for step in draft.steps:
        for source in step.arg_sources:
            kind = source.source.kind
            if kind in ("hint", "action", "derived"):
                problems.append(f"unexpected source kind {kind} in live sample")
            elif kind == "utterance":
                ref = source.source.ref
                if ref not in utt_by_id:
                    problems.append(f"utterance ref {ref} not provided")
                else:
                    span = source.source.span
                    length = len(utt_by_id[ref].text)
                    if not (0 <= span[0] < span[1] <= length):
                        problems.append(f"utterance span {span} out of bounds for {ref}")
            elif kind == "read":
                ref = source.source.ref
                if ref not in read_by_ref:
                    problems.append(f"read ref {ref} not provided")
                else:
                    try:
                        resolve_pointer(read_by_ref[ref].data, source.source.pointer)
                    except ValueError as exc:
                        problems.append(f"read pointer {source.source.pointer} not in {ref}: {exc}")
            elif kind == "step":
                if source.source.ref not in step_ids:
                    problems.append(f"step ref {source.source.ref} not defined earlier")
        step_ids.add(step.step_id)
    return problems


def _check_transfer(draft: PlanDraft, state: dict) -> list[str]:
    if not draft.steps or draft.steps[0].tool != "transfer":
        return ["expected transfer step"]
    step = draft.steps[0]
    args = step.args
    problems = []
    if args.from_account_id != "acc_zs_chk":
        problems.append(f"from_account_id={args.from_account_id}")
    if args.payee_id != "pye_zs_ww":
        problems.append(f"payee_id={args.payee_id}")
    if args.to_account_id is not None:
        problems.append(f"to_account_id={args.to_account_id}")
    if args.amount_cents != 80000:
        problems.append(f"amount_cents={args.amount_cents}")
    sources = {source.param: source.source for source in step.arg_sources}
    if not sources:
        problems.append("empty arg_sources")
    reads = state["reads"]
    problems += _check_read_param(
        sources, "from_account_id", "acc_zs_chk", reads, "src_syn_acct", "/accounts/0/account_id"
    )
    problems += _check_read_param(
        sources, "payee_id", "pye_zs_ww", reads, "src_syn_payee", "/payees/0/payee_id"
    )
    problems += _check_utterance_param(sources, "amount_cents", state["utterances"][0].text, "800")
    return problems + _source_problems(draft, state["utterances"], reads)


def _check_freeze(draft: PlanDraft, state: dict) -> list[str]:
    if not draft.steps or draft.steps[0].tool != "freeze_card":
        return ["expected freeze_card step"]
    step = draft.steps[0]
    if step.args.card_id != "crd_zs_1":
        return [f"card_id={step.args.card_id}"]
    sources = {source.param: source.source for source in step.arg_sources}
    problems = []
    if not sources:
        problems.append("empty arg_sources")
    problems += _check_read_param(
        sources, "card_id", "crd_zs_1", state["reads"], "src_syn_card", "/cards/0/card_id"
    )
    return problems + _source_problems(draft, state["utterances"], state["reads"])


def _check_limits(draft: PlanDraft, state: dict) -> list[str]:
    if not draft.steps or draft.steps[0].tool != "update_card_limits":
        return ["expected update_card_limits step"]
    step = draft.steps[0]
    args = step.args
    if args.card_id != "crd_zs_1" or args.single_limit_cents != 1000:
        return [f"card_id={args.card_id} single_limit_cents={args.single_limit_cents}"]
    if args.daily_limit_cents is not None:
        return [f"daily_limit_cents={args.daily_limit_cents} expected None"]
    sources = {source.param: source.source for source in step.arg_sources}
    problems = []
    if not sources:
        problems.append("empty arg_sources")
    problems += _check_read_param(
        sources, "card_id", "crd_zs_1", state["reads"], "src_syn_card", "/cards/0/card_id"
    )
    problems += _check_utterance_param(
        sources, "single_limit_cents", state["utterances"][0].text, "1000"
    )
    if "daily_limit_cents" in sources:
        problems.append("daily_limit_cents must not declare a source (sentinel)")
    return problems + _source_problems(draft, state["utterances"], state["reads"])


def _plan_state(text: str, utt_id: str, reads: list[ReadRecord], intents: tuple[str, ...]) -> dict:
    return {
        "session_id": "ses_syn",
        "utterances": [UtteranceRef(id=utt_id, kind="typed", text=text)],
        "understanding": Understanding(intents=list(intents), needs_write=True),
        "reads": reads,
    }


def run_scenarios(llm) -> tuple[list[dict], int]:
    usage: list[dict] = []

    def observer(message) -> None:
        usage.append(dict(message.usage_metadata or {}))

    understand = UnderstandAdapter(llm, max_retries=0, observer=observer)
    plan = PlanAdapter(llm, max_retries=1, observer=observer)

    records: list[dict] = [
        {
            "name": "schemas",
            "understand_tool_sha": _sha(understanding_tool()),
            "plan_tool_sha": _sha(plan_tool()),
            "read_tool_names": sorted(tool["function"]["name"] for tool in read_tools()),
        }
    ]
    failed = 0

    def record(name: str, fn, check: Callable[[object], list[str]] | None = None) -> None:
        nonlocal failed
        started = time.perf_counter()
        try:
            result = fn()
            problems = check(result) if check is not None else []
            entry = {
                "name": name,
                "ok": not problems,
                "latency_ms": round((time.perf_counter() - started) * 1000),
            }
            if isinstance(result, PlanDraft):
                entry["draft"] = _draft_summary(result)
            else:
                entry["result_kind"] = type(result).__name__
            if problems:
                entry["problems"] = problems
                failed += 1
            records.append(entry)
        except Exception as exc:  # noqa: BLE001 — sanitized live evidence
            entry = {
                "name": name,
                "ok": False,
                "error_type": type(exc).__name__,
                "latency_ms": round((time.perf_counter() - started) * 1000),
            }
            fields = _error_fields(exc)
            if fields is not None:
                entry["validation_errors"] = fields
            failed += 1
            records.append(entry)

    def understand_sample(text: str) -> Understanding:
        state = {"utterances": [UtteranceRef(id="utt_syn_1", kind="typed", text=text)]}
        return understand(state, _ctx())

    for text in ("给王五转800元", "查一下我的活期余额"):
        record(f"understand:{text}", lambda t=text: understand_sample(t))

    account_read = ReadRecord(
        source_ref="src_syn_acct",
        tool="get_accounts",
        data={"accounts": [{"account_id": "acc_zs_chk", "balance_cents": 600000}]},
    )
    payee_read = ReadRecord(
        source_ref="src_syn_payee",
        tool="list_payees",
        data={"payees": [{"payee_id": "pye_zs_ww", "name": "王五"}]},
    )
    card_read = ReadRecord(
        source_ref="src_syn_card",
        tool="get_cards",
        data={"cards": [{"card_id": "crd_zs_1", "status": "ACTIVE"}]},
    )

    transfer_state = _plan_state(
        "给王五转800元", "utt_syn_t1", [account_read, payee_read], ("transfer",)
    )
    freeze_state = _plan_state("冻结crd_zs_1这张卡", "utt_syn_t2", [card_read], ("card",))
    limits_state = _plan_state(
        "把crd_zs_1的单笔限额降到1000分", "utt_syn_t3", [card_read], ("card",)
    )
    record(
        "plan:transfer",
        lambda: plan(transfer_state, _ctx()),
        lambda draft: _check_transfer(draft, transfer_state),
    )
    record(
        "plan:freeze",
        lambda: plan(freeze_state, _ctx()),
        lambda draft: _check_freeze(draft, freeze_state),
    )
    record(
        "plan:limits",
        lambda: plan(limits_state, _ctx()),
        lambda draft: _check_limits(draft, limits_state),
    )

    def fake_read(_name: str, _args: dict) -> ReadResult:
        return ReadResult(
            data={"accounts": [{"account_id": "acc_zs_chk", "balance_cents": 600000}]},
            source_ref="src_syn",
        )

    def gather_sample() -> object:
        state = {
            "utterances": [UtteranceRef(id="utt_syn_g", kind="typed", text="我活期余额多少")],
            "understanding": Understanding(intents=["balance"], needs_write=False),
        }
        return GatherAdapter(llm, fake_read, max_rounds=3, observer=observer)(state, _ctx())

    record("gather", gather_sample)

    records.append({"name": "usage", "requests": len(usage), "tokens": usage})
    return records, (1 if failed else 0)


def main() -> int:
    key = load_api_key()
    transport = _BudgetTransport(httpx.HTTPTransport(), REQUEST_LIMIT)
    llm = make_llm(api_key=key, http_client=httpx.Client(transport=transport))
    records, exit_code = run_scenarios(llm)

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    out = EVIDENCE_DIR / "results.jsonl"
    with out.open("a") as stream:
        for entry in records:
            stream.write(
                json.dumps(entry, ensure_ascii=False, default=str).replace(key, "[REDACTED]") + "\n"
            )
    status = "FAILED" if exit_code else "OK"
    print(f"{status}: wrote {len(records)} records to {out} ({transport.count} requests)")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
