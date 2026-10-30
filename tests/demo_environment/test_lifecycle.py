"""Local lifecycle contracts: no Docker or model calls in these fault-path tests."""

import json
import os
from unittest.mock import Mock

import pytest
import yaml

from scripts.demo_environment import DemoEnvironment, database_url, generation_names, private_write


@pytest.mark.parametrize("name", ["x; DROP DATABASE bank", "demo_20261030000000_abc", "../bank"])
def test_database_names_reject_injection(name):
    with pytest.raises(ValueError):
        generation_names(name)


def test_url_rotation_changes_only_database():
    assert database_url("postgresql://user:secret@postgres-bank/old", "new") == (
        "postgresql://user:secret@postgres-bank/new"
    )


def test_private_files_do_not_overwrite_previous_generation(tmp_path):
    p = tmp_path / "secrets"
    private_write(p, "original")
    with pytest.raises(FileExistsError):
        private_write(p, "replacement")
    assert p.read_text() == "original"
    assert os.stat(p).st_mode & 0o777 == 0o600


def environment(tmp_path, monkeypatch):
    deploy = tmp_path / "e2e-deploy"
    deploy.mkdir()
    (deploy / "e2e.override.yml").write_text("services: {}")
    secrets = deploy / ".secrets"
    secrets.mkdir()
    for kind, variable in (("bank", "BANK"), ("gateway", "GATEWAY"), ("agent", "AGENT")):
        (secrets / f"{kind}.env").write_text(
            f"{variable}_DATABASE_URL=postgresql://{kind}_app:fake@postgres-{kind}/{kind}\n"
        )
    (secrets / "postgres-bank-password").write_text("admin-sentinel")
    demo = DemoEnvironment(deploy)
    monkeypatch.setattr("scripts.demo_environment.subprocess.check_output", lambda *a, **k: "sha\n")
    monkeypatch.setattr(demo, "sql", Mock(return_value=""))
    monkeypatch.setattr(demo, "verify", Mock(return_value={"digest": "same-initial-state"}))
    return demo


def test_reset_keeps_previous_state_on_switch_failure(tmp_path, monkeypatch):
    demo = environment(tmp_path, monkeypatch)
    prior = {"generation": "demo_20261030000000_1234abcd"}
    (demo.state / "current.json").write_text(json.dumps(prior))
    previous = demo.state / prior["generation"]
    previous.mkdir()
    (previous / "compose.yml").write_text("services: {}")
    calls = []

    def compose(*args, **kwargs):
        calls.append((args, kwargs))
        if args[:2] == ("images", "-q"):
            return "a" * 64
        if args[:4] == ("up", "-d", "--no-build", "--force-recreate"):
            if kwargs["overlay"].name != "rollback.yml":
                raise RuntimeError("healthcheck failed")
        return ""

    monkeypatch.setattr(demo, "compose", compose)
    with pytest.raises(RuntimeError, match="healthcheck"):
        demo.reset()
    assert demo.current() == prior
    assert calls[-1][1]["overlay"].name == "rollback.yml"
    demo.up()
    retained = yaml.safe_load(calls[-1][1]["overlay"].read_text())
    assert retained["services"]["bank-backend"]["image"] == "a" * 64
    assert not any("DROP" in str(c) or "TRUNCATE" in str(c) for c in demo.sql.call_args_list)


def test_new_generation_has_fresh_keys_and_admin_only_in_maintenance(tmp_path, monkeypatch):
    demo = environment(tmp_path, monkeypatch)
    monkeypatch.setattr(
        demo,
        "compose",
        Mock(side_effect=lambda *args, **kwargs: "a" * 64 if args[:2] == ("images", "-q") else ""),
    )
    first = demo.reset()
    second = demo.reset()
    assert first["databases"] != second["databases"]
    assert first["initial"] == second["initial"]
    assert demo.current()["generation"] == second["generation"]
    first_dir = demo.state / first["generation"]
    second_dir = demo.state / second["generation"]
    assert (first_dir / "jwt-public.pem").read_bytes() != (
        second_dir / "jwt-public.pem"
    ).read_bytes()
    config = yaml.safe_load((second_dir / "compose.yml").read_text())
    for name, service in config["services"].items():
        if name.endswith("-maintenance"):
            assert "admin-sentinel" in json.dumps(service)
        else:
            assert "admin-sentinel" not in json.dumps(service)
    assert first_dir.exists()


def test_up_requires_existing_generation_and_never_seeds(tmp_path, monkeypatch):
    demo = environment(tmp_path, monkeypatch)
    compose = Mock(return_value="")
    monkeypatch.setattr(demo, "compose", compose)
    with pytest.raises(RuntimeError, match="demo-reset"):
        demo.up()
    compose.assert_not_called()
    (demo.state / "current.json").write_text(
        json.dumps({"generation": "demo_20261030000000_1234abcd"})
    )
    assert demo.up()["url"] == "http://127.0.0.1:3001"
    assert compose.call_count == 1


def test_default_deploy_works_from_e2e_and_other_worktree(tmp_path, monkeypatch):
    from scripts.demo_environment import default_deploy

    common = tmp_path / "Fintech2026/.git"
    common.mkdir(parents=True)
    monkeypatch.setattr(
        "scripts.demo_environment.subprocess.check_output", lambda *a, **k: str(common)
    )
    monkeypatch.chdir(tmp_path)
    assert default_deploy() == tmp_path / "Fintech2026-wt/e2e-deploy"
    target = tmp_path / "Fintech2026-wt/e2e-deploy"
    target.mkdir(parents=True)
    (target / "e2e.override.yml").write_text("services: {}")
    monkeypatch.chdir(target)
    assert default_deploy() == target


@pytest.mark.parametrize("corrupt", ["clock", "balance", "seed", "migration"])
def test_verify_rejects_wrong_initial_state(tmp_path, monkeypatch, corrupt):
    demo = environment(tmp_path, monkeypatch)
    fixture = demo.deploy / "services/bank-backend/fixtures"
    fixture.mkdir(parents=True)
    seed = {
        "dataset_revision": "seed1",
        "sim_now": "2026-10-15T09:00:00+08:00",
        "accounts": [
            {
                "id": "acc_zs_chk",
                "customer_id": "cus_zs",
                "credits_posted_cents": 600000,
                "debits_posted_cents": 0,
                "debits_pending_cents": 0,
                "credits_pending_cents": 0,
            }
        ],
    }
    (fixture / "l1_seed_v1.json").write_text(json.dumps(seed))
    data = {
        "customers": 4,
        "balanced": True,
        "orders": 0,
        "seed": "seed1",
        "sim_now": seed["sim_now"],
        "accounts": [["acc_zs_chk", 600000, 0, 0, 0]],
        "bank_revision": "head",
    }
    if corrupt == "clock":
        data["sim_now"] = "2026-10-16T09:00:00+08:00"
    elif corrupt == "balance":
        data["accounts"][0][1] = 600001
    elif corrupt == "seed":
        data["seed"] = "old"
    else:
        data["bank_revision"] = "old"
    monkeypatch.setattr(demo, "sql", Mock(side_effect=[json.dumps(data), "head"]))
    monkeypatch.setattr(
        "scripts.demo_environment.ScriptDirectory.from_config",
        lambda cfg: Mock(get_current_head=lambda: "head"),
    )
    with pytest.raises(RuntimeError):
        DemoEnvironment.verify(demo, {"bank": "bank_demo", "gateway": "gateway_demo"})


def test_rollback_pins_previous_container_images_before_rebuild(tmp_path, monkeypatch):
    demo = environment(tmp_path, monkeypatch)
    calls = []
    failed = False
    old_image = "sha256:" + "a" * 64

    def compose(*args, **kwargs):
        nonlocal failed
        calls.append((args, kwargs))
        if args[:2] == ("images", "-q"):
            return old_image + "\n"
        if args[:4] == ("up", "-d", "--no-build", "--force-recreate") and not failed:
            failed = True
            raise RuntimeError("injected switch failure")
        return ""

    monkeypatch.setattr(demo, "compose", compose)
    with pytest.raises(RuntimeError, match="switch failure"):
        demo.reset()
    rollback = calls[-1][1]["overlay"]
    assert rollback is not None
    config = yaml.safe_load(rollback.read_text())
    for name in ("frontend", "operator", "agent", "gateway", "bank-backend"):
        assert config["services"][name]["image"] == old_image
    assert demo.current() is None


def test_missing_previous_container_fails_before_build(tmp_path, monkeypatch):
    demo = environment(tmp_path, monkeypatch)
    prior = {"generation": "demo_20261030000000_1234abcd"}
    directory = demo.state / prior["generation"]
    directory.mkdir()
    (directory / "compose.yml").write_text("services: {}")
    (demo.state / "current.json").write_text(json.dumps(prior))
    compose = Mock(return_value="")
    monkeypatch.setattr(demo, "compose", compose)
    with pytest.raises(RuntimeError, match="cannot pin previous"):
        demo.reset()
    assert not any(call.args[0] == "build" for call in compose.call_args_list)
    assert demo.current() == prior
