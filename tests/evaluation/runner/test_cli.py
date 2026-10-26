"""E0b T7: CLI tests (unit + integration)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent.parent.parent
E0B = Path(__file__).resolve().parent.parent.parent / "evaluation" / "runner"


# ── unit tests ────────────────────────────────────────────────────────────────


def test_live_rejects_without_key():
    """Exit code 2 when DEEPSEEK_API_KEY is not set."""
    env = {k: v for k, v in os.environ.items() if k != "DEEPSEEK_API_KEY"}
    env.pop("DEEPSEEK_API_KEY", None)
    r = subprocess.run(
        [sys.executable, "-m", "evaluation.runner.cli", "--llm-mode", "live", "--limit", "1"],
        capture_output=True,
        text=True,
        cwd=PROJECT,
        env=env,
    )
    assert r.returncode == 2, f"stderr={r.stderr}"
    assert "DEEPSEEK_API_KEY" in r.stderr


def test_live_rejects_with_key():
    """Exit code 2 even when key is set (not yet connected)."""
    env = {**os.environ, "DEEPSEEK_API_KEY": "sk-test-fake-12345678"}
    r = subprocess.run(
        [sys.executable, "-m", "evaluation.runner.cli", "--llm-mode", "live", "--limit", "1"],
        capture_output=True,
        text=True,
        cwd=PROJECT,
        env=env,
    )
    assert r.returncode == 2
    assert "协调者" in r.stderr


def test_replay_rejects():
    """Exit code 2 for replay mode."""
    r = subprocess.run(
        [sys.executable, "-m", "evaluation.runner.cli", "--llm-mode", "replay", "--limit", "1"],
        capture_output=True,
        text=True,
        cwd=PROJECT,
    )
    assert r.returncode == 2
    assert "尚未接" in r.stderr


def test_fake_no_matching_cases():
    """Exit code 1 when filter matches nothing."""
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "evaluation.runner.cli",
            "--llm-mode",
            "fake",
            "--filter",
            "zzz_no_such_case",
        ],
        capture_output=True,
        text=True,
        cwd=PROJECT,
    )
    assert r.returncode == 1
    assert "没有匹配" in r.stderr or "没有匹配" in r.stdout


# ── integration test: fake end-to-end via subprocess ──────────────────────────


def test_fake_limit_3_creates_three_files(tmp_path: Path):
    """Fake mode with --limit 3 produces metrics.json, report.md, cases.jsonl
    and metrics.sample_count == 3."""
    out = tmp_path / "report"
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "evaluation.runner.cli",
            "--llm-mode",
            "fake",
            "--limit",
            "3",
            "--repeat",
            "2",
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        cwd=PROJECT,
    )
    assert r.returncode == 0, f"stderr={r.stderr} stdout={r.stdout}"

    assert (out / "metrics.json").is_file()
    assert (out / "report.md").is_file()
    assert (out / "cases.jsonl").is_file()

    metrics = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["totals"]["runs"] == 6  # 3 cases × 2 repeats

    cases_lines = (out / "cases.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(cases_lines) == 6  # 3 cases × 2 repeats


def test_fake_filter_and_sample_ids():
    """--sample-ids and --filter both narrow the case set."""
    r1 = subprocess.run(
        [
            sys.executable,
            "-m",
            "evaluation.runner.cli",
            "--llm-mode",
            "fake",
            "--sample-ids",
            "tr-colloquial-001",
            "--limit",
            "5",
            "--repeat",
            "1",
        ],
        capture_output=True,
        text=True,
        cwd=PROJECT,
    )
    assert r1.returncode == 0

    r2 = subprocess.run(
        [
            sys.executable,
            "-m",
            "evaluation.runner.cli",
            "--llm-mode",
            "fake",
            "--filter",
            "transfer",
            "--limit",
            "5",
            "--repeat",
            "1",
        ],
        capture_output=True,
        text=True,
        cwd=PROJECT,
    )
    assert r2.returncode == 0
