"""Reproducible v1 schema snapshot; --check refuses unnoticed contract drift."""

import hashlib
import json
import sys
from pathlib import Path

from pydantic import TypeAdapter
from safebank_contracts import plan, protocol, state
from safebank_contracts.schema import plan_tool
from safebank_contracts.tools import READ_ARGS, WRITE_ARGS
from safebank_contracts.views import READ_RESULTS, WRITE_RESULTS

ROOT = Path(__file__).resolve().parents[1] / "docs/contracts"


def catalog() -> dict:
    models = [
        plan.PlanDraft,
        state.BankState,
        plan.ExecutionResult,
        plan.Prepare,
        plan.Execute,
        plan.Reconcile,
        plan.SnapshotRequest,
        plan.InternalRead,
        plan.Ack,
        protocol.PrepareResult,
        protocol.ExecutionStatus,
        protocol.OutboxPage,
        protocol.AckResult,
    ]
    return {
        "version": "1.0.0",
        "read_tools": {
            name: {
                "args": model.model_json_schema(),
                "result": READ_RESULTS[name].model_json_schema(),
            }
            for name, model in READ_ARGS.items()
        },
        "write_tools": {
            name: {
                "args": model.model_json_schema(),
                "result": WRITE_RESULTS[name].model_json_schema(),
            }
            for name, model in WRITE_ARGS.items()
        },
        "models": {model.__name__: model.model_json_schema() for model in models}
        | {"LedgerEffect": TypeAdapter(state.LedgerEffect).json_schema()},
        "llm_tool": plan_tool(),
    }


def main() -> None:
    body = json.dumps(catalog(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    fingerprint = hashlib.sha256(body.encode()).hexdigest() + "  v1.json\n"
    ROOT.mkdir(exist_ok=True)
    for name, content in [("v1.json", body), ("v1.sha256", fingerprint)]:
        target = ROOT / name
        if "--check" in sys.argv:
            if not target.exists() or target.read_text() != content:
                raise SystemExit(
                    f"Contract changed: {name}; update PRD and record a version decision"
                )
        else:
            target.write_text(content)
    print(
        "PASS: contracts v1 match frozen schemas"
        if "--check" in sys.argv
        else "Generated contracts v1 candidate"
    )


if __name__ == "__main__":
    main()
