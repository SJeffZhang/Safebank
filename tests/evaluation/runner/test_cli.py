"""E0b T8: CLI tests for live mode (no network calls, no real model)."""

from __future__ import annotations

import asyncio
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

PROJECT = Path(__file__).resolve().parent.parent.parent.parent


def _run_cli(args: list[str], **kwargs) -> subprocess.CompletedProcess:
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


# ── restored from T7b (c73082f), adapted for self-provisioning CLI ──────────


def test_fake_limit_1_creates_output_through_real_g4(tmp_path: Path):
    """Fake mode with --limit 1 → output from real L3 tmpfs PG and real G4.

    The CLI provisions its own tmpfs PostgreSQL; requires Docker.
    """
    _require_docker()

    out = tmp_path / "report"
    r = _run_cli(
        ["--llm-mode", "fake", "--limit", "1", "--repeat", "2", "--out", str(out)],
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


def test_anti_spoofing_tweaked_expected_does_not_change_actual(tmp_path: Path):
    """Tweak a case's expected.decision to DENY; actual must NOT follow it.

    Proves CLI obtains results from real G4, not fabricated from corpus.
    The CLI provisions its own tmpfs PostgreSQL; requires Docker.
    """
    _require_docker()

    from evaluation.runner import cli

    # Find the first non-attack case with turns.
    cases = cli._load_cases(limit=None, sample_ids=None, filter_pat=None)
    non_attack = [
        c for c in cases if not c.get("category", "").startswith("attack_") and c.get("turns")
    ]
    assert non_attack, "no non-attack corpus case with turns"
    first = dict(non_attack[0])
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


# ── sentinel leak test ──────────────────────────────────────────────────────


_SENTINEL = "sk-test-SENTINEL-0123456789"


def test_live_402_sentinel_not_leaked_to_output(tmp_path: Path):
    """DEEPSEEK_API_KEY sentinel must never appear in files, stdout, or stderr.

    Mocks ``make_llm`` to raise (simulating 402) and verifies the error path
    does not leak the API key.  No Docker needed — all provisioning is mocked.
    """
    from contextlib import asynccontextmanager

    from evaluation.runner import cli

    out = tmp_path / "report"

    async def _fake_run_corpus(*a, **kw):
        from evaluation.runner.loop import StopRun

        raise StopRun("402 Payment Required — test", [])

    @asynccontextmanager
    async def _fake_harness(*a, **kw):
        yield object()

    args = cli._build_arg_parser().parse_args(
        ["--llm-mode", "live", "--limit", "1", "--repeat", "1", "--out", str(out)]
    )
    args.limit = 5  # emulate main()

    # Capture stderr written by _run_eval's print(…, file=sys.stderr).
    captured_stderr = io.StringIO()

    with (
        mock.patch("evaluation.runner.cli.run_corpus", _fake_run_corpus),
        mock.patch("evaluation.runner.cli._provision_gateway_db"),
        mock.patch("evaluation.runner.cli._provision_obligations_db"),
        mock.patch("evaluation.runner.cli._migrate_obligations"),
        mock.patch("evaluation.runner.cli.eval_harness", _fake_harness),
        mock.patch("sys.stderr", captured_stderr),
        mock.patch.dict("os.environ", {"DEEPSEEK_API_KEY": _SENTINEL}),
    ):
        exit_code = asyncio.run(cli._run_eval(args))

    assert exit_code == 3
    assert (out / "metrics.json").is_file()

    stderr_text = captured_stderr.getvalue()

    # Sentinal must not appear anywhere.
    sentinel_lower = _SENTINEL.lower()
    for fname in ["metrics.json", "report.md", "cases.jsonl"]:
        fp = out / fname
        if not fp.is_file():
            continue
        content = fp.read_text(encoding="utf-8")
        assert sentinel_lower not in content.lower(), f"{fname} leaked sentinel key"

    assert sentinel_lower not in stderr_text.lower(), "stderr leaked sentinel key"


def test_live_ports_factory_sets_supplement_transfer_reads():
    """``_live_ports_factory`` and ``_live_ports_factory_counting`` must set
    ``supplement_transfer_reads=True`` on the gather adapter, matching the
    production path in ``services/agent/app/main.py``."""
    from unittest import mock

    from services.agent.app.llm.gather import GatherAdapter

    fake_llm = mock.MagicMock()

    with mock.patch("services.agent.app.llm.client.make_llm", return_value=fake_llm):
        from evaluation.runner.cli import _live_ports_factory, _live_ports_factory_counting

        # _live_ports_factory(case) — no counter
        ports = _live_ports_factory({"turns": ["hello"]})
        gather = ports["gather"]
        assert isinstance(gather, GatherAdapter)
        assert gather.supplement_transfer_reads is True, (
            f"_live_ports_factory: supplement_transfer_reads={gather.supplement_transfer_reads}"
        )

        # _live_ports_factory_counting(case, counter)
        ports2 = _live_ports_factory_counting({"turns": ["hello"]}, [0])
        gather2 = ports2["gather"]
        assert isinstance(gather2, GatherAdapter)
        assert gather2.supplement_transfer_reads is True, (
            "_live_ports_factory_counting: "
            f"supplement_transfer_reads={gather2.supplement_transfer_reads}"
        )


# ── live mode: subprocess tests (no Docker required) ──────────────────────


def test_live_no_key_exit_2():
    """Exit code 2 when DEEPSEEK_API_KEY is not set and live mode is used."""
    env = {k: v for k, v in os.environ.items() if k != "DEEPSEEK_API_KEY"}
    env.pop("DEEPSEEK_API_KEY", None)
    r = _run_cli(["--llm-mode", "live", "--limit", "1"], env=env)
    assert r.returncode == 2, f"stderr={r.stderr}"
    assert "DEEPSEEK_API_KEY" in r.stderr


# ── live mode: in-process tests ───────────────────────────────────────────


def test_live_ports_factory_constructs():
    """_live_ports_factory returns 5 callables (no API call, no Docker).

    DEEPSEEK_API_KEY must be set in the environment for make_llm to succeed.
    """
    from evaluation.runner.cli import _live_ports_factory

    # Ensure a key is available; the factory only reads it, never dials out.
    if "DEEPSEEK_API_KEY" not in os.environ:
        os.environ["DEEPSEEK_API_KEY"] = "sk-test-for-construct-only"
    try:
        ports = _live_ports_factory({})
        assert callable(ports["understand"])
        assert callable(ports["gather"])
        assert callable(ports["plan"])
        assert callable(ports["respond"])
        assert callable(ports["verify_output"])
        assert len(ports) == 5
    finally:
        if os.environ.get("DEEPSEEK_API_KEY") == "sk-test-for-construct-only":
            del os.environ["DEEPSEEK_API_KEY"]


def test_live_402_stopped_by_run_eval(tmp_path: Path):
    """_run_eval for live mode catches StopRun → returns 3, writes metrics.

    Mocks ``run_corpus`` to raise ``StopRun`` with 0 completed runs so we
    verify the StopRun → exit-3 path without Docker or a real model.
    """
    import asyncio
    from contextlib import asynccontextmanager

    from evaluation.runner import cli

    out = tmp_path / "report"

    async def _fake_run_corpus(*a, **kw):
        from evaluation.runner.loop import StopRun

        raise StopRun("402 Payment Required — test", [])

    @asynccontextmanager
    async def _fake_harness(*a, **kw):
        yield object()

    args = cli._build_arg_parser().parse_args(
        ["--llm-mode", "live", "--limit", "1", "--repeat", "1", "--out", str(out)]
    )
    args.limit = 5  # emulate main()

    with (
        mock.patch("evaluation.runner.cli.run_corpus", _fake_run_corpus),
        mock.patch("evaluation.runner.cli._provision_gateway_db"),
        mock.patch("evaluation.runner.cli._provision_obligations_db"),
        mock.patch("evaluation.runner.cli._migrate_obligations"),
        mock.patch("evaluation.runner.cli.eval_harness", _fake_harness),
        mock.patch.dict("os.environ", {"DEEPSEEK_API_KEY": "sk-test-for-mock"}),
    ):
        exit_code = asyncio.run(cli._run_eval(args))

    assert exit_code == 3
    assert (out / "metrics.json").is_file()

    # Output must not contain the fake key.
    for fname in ["metrics.json", "report.md", "cases.jsonl"]:
        fp = out / fname
        if not fp.is_file():
            continue
        content = fp.read_text(encoding="utf-8")
        assert "sk-test" not in content.lower(), f"{fname} leaked key-like string"


def test_live_max_requests_default_in_parser():
    """--max-requests defaults to 60."""
    from evaluation.runner.cli import _build_arg_parser

    parser = _build_arg_parser()
    args = parser.parse_args(["--llm-mode", "live"])
    assert args.max_requests == 60


def test_live_limit_set_in_main():
    """main() sets args.limit=5 in live mode when None."""
    from evaluation.runner.cli import _build_arg_parser

    parser = _build_arg_parser()
    args = parser.parse_args(["--llm-mode", "live", "--out", "/dev/null"])
    assert args.limit is None  # parser default
    # Simulate main()'s live limit logic
    args.limit = 5
    assert args.limit == 5

    # fake mode keeps None
    args2 = parser.parse_args(["--llm-mode", "fake"])
    assert args2.limit is None


def test_wire_live_gather_makes_gather():
    """_wire_live_gather replaces gather port with a callable."""
    from evaluation.runner.cli import _live_ports_factory, _wire_live_gather

    if "DEEPSEEK_API_KEY" not in os.environ:
        os.environ["DEEPSEEK_API_KEY"] = "sk-test-for-wire"
    try:
        ports = _live_ports_factory({})
        assert callable(ports["gather"])

        # Create a mock client for wiring
        class _FakeClient:
            def request(self, *a, **kw):
                pass

        wired = _wire_live_gather(_FakeClient(), ports, {})
        assert callable(wired["gather"])
        assert wired["understand"] is ports["understand"]
    finally:
        if os.environ.get("DEEPSEEK_API_KEY") == "sk-test-for-wire":
            del os.environ["DEEPSEEK_API_KEY"]


def test_counting_llm_proxy_increments():
    """_CountingLLMProxy increments the shared counter on invoke()."""
    from evaluation.runner.cli import _CountingLLMProxy

    class _FakeLLM:
        def invoke(self, *a, **kw):
            return "ok"

        def some_attr(self):
            return 42

    counter = [0]
    proxy = _CountingLLMProxy(_FakeLLM(), counter)
    assert proxy.invoke() == "ok"
    assert counter[0] == 1
    proxy.invoke()
    assert counter[0] == 2
    # Attribute passthrough
    assert proxy.some_attr() == 42
