"""E0b T7b: CLI tests (unit + integration).

DB tests require ``GATEWAY_DB_TESTS=1 OBLIGATIONS_DB_TESTS=1`` and Docker.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

PROJECT = Path(__file__).resolve().parent.parent.parent.parent


# ── helpers ────────────────────────────────────────────────────────────────────


def _run_cli(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run the CLI with extra env, capture output."""
    env = kwargs.pop("env", dict(os.environ))
    return subprocess.run(
        [sys.executable, "-m", "evaluation.runner.cli", *args],
        capture_output=True,
        text=True,
        cwd=PROJECT,
        env=env,
        **kwargs,
    )


def _require_docker() -> None:
    """Skip if Docker is not available."""
    r = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip("Docker not available")


def _require_real_pg() -> None:
    """Skip if real-PG test env vars are not set."""
    if os.getenv("GATEWAY_DB_TESTS") != "1" or os.getenv("OBLIGATIONS_DB_TESTS") != "1":
        pytest.skip("requires GATEWAY_DB_TESTS=1 OBLIGATIONS_DB_TESTS=1")


_ENV_REAL_PG = {
    **os.environ,
    "GATEWAY_DB_TESTS": "1",
    "OBLIGATIONS_DB_TESTS": "1",
}


# ── live / replay tests (no Docker needed) ────────────────────────────────────


def test_live_rejects_without_key():
    """Exit code 2 when DEEPSEEK_API_KEY is not set."""
    env = {k: v for k, v in os.environ.items() if k != "DEEPSEEK_API_KEY"}
    env.pop("DEEPSEEK_API_KEY", None)
    r = _run_cli(["--llm-mode", "live", "--limit", "1"], env=env)
    assert r.returncode == 2, f"stderr={r.stderr}"
    assert "DEEPSEEK_API_KEY" in r.stderr


def test_live_rejects_with_key():
    """Exit code 2 even when key is set (not yet connected)."""
    env = {**os.environ, "DEEPSEEK_API_KEY": "sk-test-fake-12345678"}
    r = _run_cli(["--llm-mode", "live", "--limit", "1"], env=env)
    assert r.returncode == 2
    assert "协调者" in r.stderr


def test_replay_rejects():
    """Exit code 2 for replay mode."""
    r = _run_cli(["--llm-mode", "replay", "--limit", "1"])
    assert r.returncode == 2
    assert "尚未接" in r.stderr


def test_fake_no_matching_cases():
    """Exit code 1 when filter matches nothing (no DB needed)."""
    r = _run_cli(["--llm-mode", "fake", "--filter", "zzz_no_such_case"])
    assert r.returncode == 1
    assert "没有匹配" in r.stderr or "没有匹配" in r.stdout


# ── real-DB integration tests ─────────────────────────────────────────────────


@pytest.mark.usefixtures("gateway_db", "obligations_db")
def test_fake_limit_1_creates_output_through_real_g4(tmp_path: Path):
    """Fake mode with --limit 1 → output from real L3 tmpfs PG and real G4."""
    _require_docker()
    _require_real_pg()

    out = tmp_path / "report"
    r = _run_cli(
        ["--llm-mode", "fake", "--limit", "1", "--repeat", "2", "--out", str(out)],
        env=_ENV_REAL_PG,
    )
    assert r.returncode == 0, f"stderr={r.stderr}\nstdout={r.stdout}"

    assert (out / "metrics.json").is_file()
    assert (out / "report.md").is_file()
    assert (out / "cases.jsonl").is_file()

    metrics = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["totals"]["runs"] == 2  # 1 case x 2 repeats

    cases_lines = (out / "cases.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(cases_lines) == 2

    # Meta must show l3=real-tmpfs (not "fake").
    report = (out / "report.md").read_text(encoding="utf-8")
    assert "real-tmpfs" in report, f"report missing l3=real-tmpfs:\n{report}"


@pytest.mark.usefixtures("gateway_db", "obligations_db")
def test_anti_spoofing_tweaked_expected_does_not_change_actual(tmp_path: Path):
    """Tweak a case's expected.decision to DENY; actual must NOT follow it.

    Proves CLI obtains results from real G4, not fabricated from corpus.
    """
    _require_docker()
    _require_real_pg()

    from evaluation.runner import cli
    from evaluation.runner.cli import _load_cases

    # Find the first benign case with turns.
    cases = _load_cases(limit=None, sample_ids=None, filter_pat=None)
    benign = [c for c in cases if c.get("category", "").startswith("benign") and c.get("turns")]
    assert benign, "no benign corpus case with turns"
    first = dict(benign[0])
    original_expected = first.get("expected", {})

    # Build a tweaked version: expected.outcome = "DENY".
    tweaked = dict(first)
    tweaked["expected"] = dict(original_expected, outcome="DENY")

    # Write the tweaked case as a single-line JSONL file.
    tweaked_dir = tmp_path / "corpus"
    tweaked_dir.mkdir()
    (tweaked_dir / "tweaked.jsonl").write_text(
        json.dumps(tweaked, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    out = tmp_path / "report"
    with mock.patch.object(cli, "CORPUS_DIR", tweaked_dir):
        exit_code = cli.main(["--llm-mode", "fake", "--repeat", "1", "--out", str(out)])

    assert exit_code == 0, f"CLI exit code {exit_code}"

    # Read the actual CaseRun.
    cases_data = [
        json.loads(line)
        for line in (out / "cases.jsonl").read_text(encoding="utf-8").strip().splitlines()
    ]
    assert len(cases_data) == 1

    actual_outcome = cases_data[0].get("actual_outcome")
    # The tweaked expected was DENY. If actual is also DENY AND the original
    # expected was NOT DENY, the CLI fabricated results from expected.
    if original_expected.get("outcome") != "DENY" and actual_outcome == "DENY":
        raise AssertionError(
            f"actual_outcome={actual_outcome} matches tweaked expected=DENY "
            f"(original expected={original_expected.get('outcome')}). "
            f"Results appear fabricated, not from real G4."
        )

    # Meta must show real-tmpfs.
    report = (out / "report.md").read_text(encoding="utf-8")
    assert "real-tmpfs" in report
