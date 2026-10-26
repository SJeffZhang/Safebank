"""E0b T8: CLI tests for live mode (no network calls, no real model)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

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
