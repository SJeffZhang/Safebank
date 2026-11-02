"""Portable deployment resolution must never guess another machine's checkout."""

from pathlib import Path

import pytest

from scripts.demo_environment import default_deploy


def deployed(path: Path) -> Path:
    path.mkdir()
    (path / "e2e.override.yml").write_text("services: {}\n")
    return path


def test_explicit_deployment_takes_precedence_over_env(monkeypatch, tmp_path):
    explicit = deployed(tmp_path / "explicit")
    env = deployed(tmp_path / "env")
    monkeypatch.setenv("SAFEBANK_DEMO_DEPLOY", str(env))
    assert default_deploy(explicit) == explicit.resolve()


def test_deploy_environment_overrides_current_directory(monkeypatch, tmp_path):
    env = deployed(tmp_path / "env")
    cwd = deployed(tmp_path / "cwd")
    monkeypatch.setenv("SAFEBANK_DEMO_DEPLOY", str(env))
    monkeypatch.chdir(cwd)
    assert default_deploy() == env.resolve()


def test_deploy_environment_is_validated_not_silently_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv("SAFEBANK_DEMO_DEPLOY", str(tmp_path / "missing"))
    monkeypatch.chdir(deployed(tmp_path / "cwd"))
    with pytest.raises(ValueError, match="e2e.override.yml"):
        default_deploy()


def test_current_directory_deployment_is_supported(monkeypatch, tmp_path):
    monkeypatch.delenv("SAFEBANK_DEMO_DEPLOY", raising=False)
    monkeypatch.chdir(deployed(tmp_path / "cwd"))
    assert default_deploy() == Path.cwd()


def test_no_deployment_fails_without_creating_or_guessing_checkout(monkeypatch, tmp_path):
    monkeypatch.delenv("SAFEBANK_DEMO_DEPLOY", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="SAFEBANK_DEMO_DEPLOY"):
        default_deploy()
    assert list(tmp_path.iterdir()) == []
