"""Exercise the copied live driver's process boundary without Docker or a model."""

import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

PATH = Path("docs/handoffs/2026-10-30-tools/drive_try_agent.py")
spec = importlib.util.spec_from_file_location("live_driver", PATH)
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)


@pytest.mark.parametrize("status", [402, 403, 429])
def test_quota_error_stops_before_another_prompt(status, monkeypatch, tmp_path):
    pins = tmp_path / "pins.json"
    pins.write_text('{"cus_zs":"123456"}')
    real_popen = subprocess.Popen
    children = []

    def spawn(*args, **kwargs):
        child = real_popen(
            [
                sys.executable,
                "-u",
                "-c",
                f"import time; print('Error code: {status}', flush=True); time.sleep(0.5)",
            ],
            **kwargs,
        )
        children.append(child)
        return child

    monkeypatch.setattr(driver.subprocess, "Popen", spawn)
    monkeypatch.setattr(sys, "argv", ["driver", str(tmp_path), str(pins), "cus_zs", "查询"])
    try:
        assert driver.main() == 75
        assert children[0].poll() is not None
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait()


def test_silent_child_times_out(monkeypatch, tmp_path):
    pins = tmp_path / "pins.json"
    pins.write_text('{"cus_zs":"123456"}')
    real_popen = subprocess.Popen
    children = []

    def spawn(*args, **kwargs):
        child = real_popen([sys.executable, "-c", "import time; time.sleep(0.5)"], **kwargs)
        children.append(child)
        return child

    monkeypatch.setenv("TRY_AGENT_TIMEOUT_SECONDS", "0.1")
    monkeypatch.setattr(driver.subprocess, "Popen", spawn)
    monkeypatch.setattr(sys, "argv", ["driver", str(tmp_path), str(pins), "cus_zs", "查询"])
    started = time.monotonic()
    try:
        assert driver.main() == 124
        assert time.monotonic() - started < 3
        assert children[0].poll() is not None
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait()


def test_amount_is_not_a_quota_error():
    assert not driver.quota_status("Reply: 消费402元，余额403元，转账429元")


@pytest.mark.parametrize("code", [75, 124])
def test_batch_stops_before_second_case(code, tmp_path):
    """Run the real batch shell with fake Docker/driver, including a spaced path."""
    scripts = tmp_path / "helper scripts"
    scripts.mkdir()
    source = PATH.parent / "run_full2.sh"
    (scripts / source.name).write_text(source.read_text())
    (scripts / "drive_try_agent.py").write_text(f"raise SystemExit({code})\n")
    (scripts / "trace_err.py").write_text("")
    snapshot = scripts / "snap3.sh"
    snapshot.write_text('#!/bin/sh\necho "$1" >> "$CALLS"\necho snapshot\n')
    snapshot.chmod(0o755)
    cases = scripts / "cases.tsv"
    cases.write_text("01\tcus_zs\t查询\n02\tcus_ls\t查询\n")
    root = tmp_path / "worktrees"
    (root / "e2e-deploy").mkdir(parents=True)
    bins = tmp_path / "bin"
    bins.mkdir()
    for name in ("docker", "git"):
        command = bins / name
        command.write_text("#!/bin/sh\necho bank\n")
        command.chmod(0o755)
    calls = tmp_path / "calls"
    result = subprocess.run(
        ["bash", str(scripts / source.name)],
        env={
            **os.environ,
            "PATH": str(bins) + os.pathsep + os.environ["PATH"],
            "LIVE_WORKTREE_ROOT": str(root),
            "CASES": str(cases),
            "RUN_TAG": "test",
            "CALLS": str(calls),
        },
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == code, result.stdout + result.stderr
    assert calls.read_text().splitlines() == ["cus_zs", "cus_zs"]
    assert "DONE" not in result.stdout
