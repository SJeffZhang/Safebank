"""E0b T7: CLI entry point for L1 stability evaluation.

Usage:
    PYTHONPATH=. packages/contracts/src:packages/bankcore/src:services/bank-backend \\
        python -m evaluation.runner.cli --llm-mode fake --limit 20
    make eval-l1 LIMIT=20
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Ensure shared packages are importable when run outside pytest.
_project_root = Path(__file__).resolve().parent.parent.parent
for _p in [
    str(_project_root),
    str(_project_root / "packages" / "contracts" / "src"),
    str(_project_root / "packages" / "bankcore" / "src"),
    str(_project_root / "services" / "bank-backend"),
]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from evaluation.metrics.compute import compute_metrics, write_outputs  # noqa: E402
from evaluation.runner.loop import CaseRun  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CORPUS_DIR = PROJECT_ROOT / "evaluation" / "corpus" / "l1_stability"
SEED_PATH = PROJECT_ROOT / "evaluation" / "fixtures" / "l1_seed_v1.json"


# ── helpers ────────────────────────────────────────────────────────────────────


def _load_cases(
    limit: int | None,
    sample_ids: list[str] | None,
    filter_pat: str | None,
) -> list[dict[str, Any]]:
    """Load corpus cases from JSONL files, sorted by case_id."""
    cases: list[dict[str, Any]] = []
    jsonl_files = sorted(CORPUS_DIR.glob("*.jsonl"))
    for fp in jsonl_files:
        for line in fp.read_text(encoding="utf-8").strip().splitlines():
            if not line:
                continue
            case = json.loads(line)
            cases.append(case)

    if sample_ids:
        id_set = set(sample_ids)
        cases = [c for c in cases if c.get("case_id", "") in id_set]

    if filter_pat:
        pattern = filter_pat.lower()
        cases = [
            c
            for c in cases
            if pattern in c.get("case_id", "").lower() or pattern in c.get("category", "").lower()
        ]

    if limit is not None and limit > 0:
        cases = cases[:limit]

    return cases


def _fake_case_run(case: dict[str, Any], repeat_index: int) -> CaseRun:
    """Build a fake CaseRun from corpus expectations (for --llm-mode fake)."""
    expected = case.get("expected", {})
    return CaseRun(
        case_id=case.get("case_id", ""),
        category=case.get("category", ""),
        variant=case.get("variant", ""),
        repeat_index=repeat_index,
        expected_outcome=expected.get("outcome", "NO_PLAN"),
        expected_risk=expected.get("risk"),
        expected_params=expected.get("params", {}),
        actual_outcome=expected.get("outcome", "NO_PLAN"),
        actual_risk=expected.get("risk"),
        actual_params=expected.get("params", {}),
        finding_codes=expected.get("reason_codes_any", []),
        failure_class=None,
        is_attack=case.get("category", "").startswith("attack_"),
        latency_ms=0,
        tokens=0,
    )


def _make_runs(cases: list[dict[str, Any]], repeat: int) -> list[CaseRun]:
    """Build fake CaseRuns for fake mode."""
    runs: list[CaseRun] = []
    for case in cases:
        for ri in range(repeat):
            runs.append(_fake_case_run(case, ri))
    return runs


def _read_seed_version() -> str:
    """Read the dataset_revision from the seed file."""
    try:
        raw = json.loads(SEED_PATH.read_text(encoding="utf-8"))
        return str(raw.get("dataset_revision", "unknown"))
    except Exception:
        return "unknown"


def _fmt_ts() -> str:
    """UTC timestamp for default output directory."""
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


# ── main ───────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="E0b L1 Stability Evaluation Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Modes:
  fake    — use expected outcomes as actual; no LLM, no PG required.
  replay  — replay from recorded LLM responses (not yet connected).
  live    — call DeepSeek V4 Pro for each case (requires DEEPSEEK_API_KEY).

Currently only --llm-mode fake is fully connected.
""",
    )
    parser.add_argument(
        "--llm-mode",
        choices=("fake", "replay", "live"),
        default="fake",
        help="LLM mode: fake | replay | live (default: fake)",
    )
    parser.add_argument("--limit", type=int, default=None, help="Max cases to run (default: all)")
    parser.add_argument(
        "--repeat", type=int, default=3, help="Repeat each case N times (default: 3)"
    )
    parser.add_argument("--sample-ids", nargs="*", default=None, help="Run only these case IDs")
    parser.add_argument(
        "--filter",
        dest="filter_pat",
        default=None,
        help="Filter cases by substring in case_id or category",
    )
    parser.add_argument(
        "--out",
        dest="out_dir",
        default=None,
        help="Output directory (default: evaluation/reports/<UTC timestamp>)",
    )

    args = parser.parse_args(argv)

    # ── live guard ──────────────────────────────────────────────────────────
    if args.llm_mode == "live":
        if not os.environ.get("DEEPSEEK_API_KEY"):
            print("live 需要设置 DEEPSEEK_API_KEY 环境变量", file=sys.stderr)
            return 2
        print("live 由协调者在后续任务接入", file=sys.stderr)
        return 2

    if args.llm_mode == "replay":
        print("replay 模式尚未接入，后续任务实现", file=sys.stderr)
        return 2

    # ── fake mode ───────────────────────────────────────────────────────────
    cases = _load_cases(
        limit=args.limit,
        sample_ids=args.sample_ids,
        filter_pat=args.filter_pat,
    )
    if not cases:
        print("没有匹配的语料用例", file=sys.stderr)
        return 1

    runs = _make_runs(cases, args.repeat)
    metrics = compute_metrics(runs)

    out_dir = (
        Path(args.out_dir) if args.out_dir else PROJECT_ROOT / "evaluation" / "reports" / _fmt_ts()
    )
    meta: dict[str, str] = {
        "llm_mode": "fake",
        "model": "fake",
        "l3": "fake",
        "seed_version": _read_seed_version(),
        "corpus_version": "v1",
        "sample_count": str(len(cases)),
    }
    write_outputs(out_dir, runs, metrics, meta)

    print(f"已写入 {out_dir}")
    print(f"  用例: {len(cases)} 条 × repeat={args.repeat} → {len(runs)} 条 CaseRun")
    print("  metrics.json | report.md | cases.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
