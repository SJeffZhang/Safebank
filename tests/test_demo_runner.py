"""Offline orchestration tests; subprocess/network boundaries are synthetic."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def runner():
    assert (ROOT / "scripts/demo_runner.py").is_file(), "production demo runner is absent"
    return importlib.import_module("scripts.demo_runner")


def test_make_demo_is_wired_and_lifecycle_accepts_args():
    text = (ROOT / "Makefile").read_text()
    assert "-m scripts.demo_runner" in text
    assert "demo eval reset:" not in text
    assert "-m scripts.demo_environment up $(ARGS)" in text
    assert "-m scripts.demo_environment reset $(ARGS)" in text


def test_canonical_default_cases_cover_six_scenes_without_reordering_file(runner):
    cases = runner.load_cases(
        ROOT / "docs/handoffs/2026-10-30-tools/full_cases2.tsv", list(runner.DEFAULT_CASES)
    )
    assert [x.id for x in cases] == ["01", "16", "27", "34", "36", "23"]
    assert cases[3].customer_id == "cus_ls"
    assert "1000份" in cases[3].text


def test_requested_case_order_is_explicit(runner, tmp_path):
    p = tmp_path / "cases.tsv"
    p.write_text("a\tcus_zs\tfirst\nb\tcus_ls\tsecond\n")
    assert [c.id for c in runner.load_cases(p, ["b", "a"])] == ["b", "a"]


@pytest.mark.parametrize(
    "rows,selected",
    [
        ("../x\tcus_zs\tx\n", ["../x"]),
        ("a\tcus_zs\tx\na\tcus_zs\ty\n", ["a"]),
        ("a\tcus_zs\tx\n", ["missing"]),
        ("a\tcus_zs\tx\n", ["a", "a"]),
        ("a\tcus_zs\tx\n", []),
        ("a\tcus_zs\tx\textra\n", ["a"]),
    ],
)
def test_bad_cases_fail_before_any_bank_request(runner, tmp_path, rows, selected):
    p = tmp_path / "cases.tsv"
    p.write_text(rows)
    with pytest.raises(runner.DemoFailure):
        runner.load_cases(p, selected)


@pytest.mark.parametrize(
    "networks",
    [
        [],
        [{"Name": "n", "Internal": False}],
        [{"Name": "n", "Internal": True}, {"Name": "egress", "Internal": False}],
        [{"Name": "n", "Internal": "true"}],
        [{"Name": "n"}],
    ],
)
def test_replay_refuses_any_unproven_or_external_network(runner, networks):
    with pytest.raises(runner.DemoFailure, match="internal"):
        runner.require_internal_networks(networks)


def test_all_networks_must_be_internal(runner):
    assert runner.require_internal_networks(
        [{"Name": "agent", "Internal": True}, {"Name": "private", "Internal": True}]
    ) == ["agent", "private"]


def test_runtime_overlay_preserves_generation_without_copying_secrets(runner, tmp_path):
    p = runner.write_runtime_overlay(tmp_path, tmp_path / "recordings", "replay", "16")
    config = yaml.safe_load(p.read_text())
    service = config["services"]["agent"]
    assert service["environment"]["LLM_MODE"] == "replay"
    assert service["environment"]["LLM_CASE_ID"] == "16"
    assert service["environment"]["DEEPSEEK_API_KEY"] == ""
    assert service["volumes"][0]["read_only"] is True
    assert "DATABASE_URL" not in p.read_text()
    assert p.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        runner.write_runtime_overlay(tmp_path, tmp_path / "recordings", "replay", "16")


def test_record_overlay_is_writable_but_no_new_secret_is_added(runner, tmp_path):
    config = yaml.safe_load(
        runner.write_runtime_overlay(tmp_path, tmp_path, "record", "01").read_text()
    )
    agent = config["services"]["agent"]
    assert agent["volumes"][0]["read_only"] is False
    assert "DEEPSEEK_API_KEY" not in agent["environment"]


def test_trace_credentials_are_redacted_without_mutating_original(runner):
    original = {
        "authorization": "Bearer abc",
        "body": {"pin": 984321, "otp": "123456"},
        "reply": "api_key=sk-abcdefghijklmnopqrstuvwx",
        "code": "OK",
        "amount_cents": 10000,
    }
    clean = runner.sanitize_trace(original)
    assert original["body"]["pin"] == 984321
    assert "984321" not in json.dumps(clean)
    assert "123456" not in json.dumps(clean)
    assert "abcdefghijklmnopqrstuvwx" not in json.dumps(clean)
    assert clean["amount_cents"] == 10000


def manifest():
    return {
        "run_id": "run1",
        "checkout_sha": "a" * 40,
        "generation": "demo_20261031000000_1234abcd",
        "business_verified": False,
        "status": "OBSERVED",
        "selected_case_ids": ["16"],
        "cases": [
            {
                "case_id": "16",
                "trace_sha256": "b" * 64,
                "driver_exit": 1,
                "business_verified": False,
            }
        ],
    }


def proof():
    return {
        "run_id": "run1",
        "checkout_sha": "a" * 40,
        "generation": "demo_20261031000000_1234abcd",
        "case_id": "16",
        "trace_sha256": "b" * 64,
        "verified": True,
        "verifier": "SQL",
        "checks": {"summary_equals_sql": True},
    }


def test_matching_sql_receipt_is_required_for_verified_result(runner, tmp_path):
    p = tmp_path / "16.json"
    p.write_text(json.dumps(proof()))
    verified = runner.verify_receipts(manifest(), tmp_path)
    assert verified["business_verified"] is True
    assert verified["cases"][0]["business_verified"] is True
    assert manifest()["business_verified"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", "old"),
        ("checkout_sha", "c" * 40),
        ("generation", "demo_20261030000000_1234abcd"),
        ("case_id", "01"),
        ("trace_sha256", "c" * 64),
        ("verified", False),
        ("verified", 1),
        ("verifier", "model"),
        ("checks", {}),
        ("checks", {"sql": False}),
        ("checks", {"sql": 1}),
    ],
)
def test_missing_stale_or_non_sql_receipt_cannot_claim_success(runner, tmp_path, field, value):
    p = proof()
    p[field] = value
    (tmp_path / "16.json").write_text(json.dumps(p))
    with pytest.raises(runner.DemoFailure):
        runner.verify_receipts(manifest(), tmp_path)


def test_missing_sql_receipt_fails_closed(runner, tmp_path):
    with pytest.raises(runner.DemoFailure):
        runner.verify_receipts(manifest(), tmp_path)


@pytest.fixture
def environment(runner, tmp_path):
    import subprocess
    from types import SimpleNamespace

    deploy = tmp_path / "deploy"
    deploy.mkdir()
    state = deploy / ".demo-state"
    generation = "demo_20261031000000_1234abcd"
    (state / generation).mkdir(parents=True)
    (state / generation / "compose.yml").write_text("services: {}\n")
    recordings = tmp_path / "recordings"
    for id_ in ("a", "b"):
        p = recordings / "B2" / id_
        p.mkdir(parents=True)
        (p / "replay.jsonl").write_text("synthetic recording\n")
    demo = SimpleNamespace(
        deploy=deploy,
        state=state,
        base=[str(deploy / "docker-compose.yml")],
        current=lambda: {"generation": generation},
        sql=lambda *a: "l1_seed_v1.0",
    )
    calls = []
    settings = {"exit": 1, "internal": True, "trace_bad": False, "fail_health": False}
    last = {}

    def command(argv, **kwargs):
        calls.append((argv, kwargs))
        output = ""
        code = 0
        if argv[:3] == ["git", "rev-parse", "HEAD"]:
            output = "a" * 40
        elif argv[:2] == ["git", "status"]:
            pass
        elif argv[:4] == ["docker", "compose", "ps", "-q"]:
            output = "agent_container"
        elif argv[:2] == ["docker", "inspect"]:
            output = json.dumps(
                [
                    {
                        "HostConfig": {"NetworkMode": "default"},
                        "NetworkSettings": {"Networks": {"private": {}}},
                    }
                ]
            )
        elif argv[:3] == ["docker", "network", "inspect"]:
            output = json.dumps([{"Name": "private", "Internal": settings["internal"]}])
        elif argv[:3] == ["docker", "compose", "exec"]:
            if "bank-backend" in argv:
                output = json.dumps({"database": "bank_" + generation})
            elif argv[-1] == runner.HEALTH_PROBE:
                output = json.dumps({"gateway_reachable": True, "postgres_agent_reachable": True})
                if settings["fail_health"]:
                    code = 1
                    output = "password=never-print-me"
            elif argv[-1] == runner.OFFLINE_PROBE:
                output = json.dumps({"external_tcp_blocked": ["1.1.1.1", "api.deepseek.com"]})
            elif "operator" in argv:
                output = json.dumps(
                    {
                        "config": {
                            "customer_id": last["customer"],
                            "text": last["text"],
                            "request_id_prefix": last["request_id"],
                        },
                        "exit_code": settings["exit"],
                        "events": [{"kind": "result"}],
                    }
                )
                if settings["trace_bad"]:
                    output = '{"exit_code":1,"events":[]}'
        elif "drive_try_agent.py" in " ".join(argv):
            last.update(
                customer=argv[-3],
                text=argv[-2],
                request_id=kwargs["env"].get("TRY_AGENT_REQUEST_ID"),
            )
            code = settings["exit"]
        elif argv[:3] != ["docker", "compose", "up"]:
            raise AssertionError("unhandled command boundary")
        return subprocess.CompletedProcess(argv, code, output, "")

    return SimpleNamespace(
        runner=runner.DemoRunner(demo, command),
        demo=demo,
        calls=calls,
        recordings=recordings,
        output=tmp_path / "run1",
        settings=settings,
    )


def invoke(env, **kwargs):
    from scripts.demo_runner import Case

    return env.runner.run(
        cases=(Case("a", "cus_zs", "first"), Case("b", "cus_ls", "second")),
        mode="replay",
        recordings=env.recordings,
        run_directory=env.output,
        observe_only=True,
        **kwargs,
    )


def test_actual_orchestration_recreates_each_case_and_never_marks_observed_as_verified(environment):
    env = environment
    result = invoke(env)
    assert result["status"] == "OBSERVED" and result["business_verified"] is False
    assert [c["case_id"] for c in result["cases"]] == ["a", "b"]
    assert all(c["business_verified"] is False for c in result["cases"])
    driver_calls = [(a, k) for a, k in env.calls if "drive_try_agent.py" in " ".join(a)]
    assert len(driver_calls) == 2
    assert "runtime-a.yml" in driver_calls[0][1]["env"]["COMPOSE_FILE"]
    assert "runtime-b.yml" in driver_calls[1][1]["env"]["COMPOSE_FILE"]
    assert driver_calls[0][1]["env"]["TRY_AGENT_OUT"] != driver_calls[1][1]["env"]["TRY_AGENT_OUT"]
    assert json.loads((env.output / "manifest.json").read_text())["business_verified"] is False


@pytest.mark.parametrize("code", [75, 124])
def test_provider_stop_or_timeout_prevents_next_case(environment, code):
    from scripts.demo_runner import DemoFailure

    env = environment
    env.settings["exit"] = code
    with pytest.raises(DemoFailure) as caught:
        invoke(env)
    assert caught.value.exit_code == code
    assert len([a for a, k in env.calls if "drive_try_agent.py" in " ".join(a)]) == 1
    assert not (env.output / "runtime-b.yml").exists()
    assert json.loads((env.output / "manifest.json").read_text())["status"] == "FAILED"


def test_replay_network_failure_precedes_any_runtime_or_bank_call(environment):
    from scripts.demo_runner import DemoFailure

    env = environment
    env.settings["internal"] = False
    with pytest.raises(DemoFailure, match="internal"):
        invoke(env)
    assert not any(a[:3] == ["docker", "compose", "up"] for a, k in env.calls)
    assert not env.output.exists()


def test_preexisting_run_is_not_reused(environment):
    env = environment
    env.output.mkdir()
    (env.output / "original").write_text("preserve")
    with pytest.raises(FileExistsError):
        invoke(env)
    assert (env.output / "original").read_text() == "preserve"
    assert not any(a[:3] == ["docker", "compose", "up"] for a, k in env.calls)


def test_incomplete_or_stale_trace_is_not_accepted(environment):
    from scripts.demo_runner import DemoFailure

    env = environment
    env.settings["trace_bad"] = True
    with pytest.raises(DemoFailure, match="trace"):
        invoke(env)


def test_subprocess_stderr_is_not_repeated_in_failure(environment):
    from scripts.demo_runner import DemoFailure

    env = environment
    env.settings["fail_health"] = True
    with pytest.raises(DemoFailure) as caught:
        invoke(env)
    assert "never-print-me" not in str(caught.value)


def test_default_verified_mode_requires_explicit_proof_workflow_before_any_command(environment):
    from scripts.demo_runner import DemoFailure

    env = environment
    with pytest.raises(DemoFailure, match="proofs"):
        env.runner.run(
            cases=(),
            mode="replay",
            recordings=env.recordings,
            run_directory=env.output,
            observe_only=False,
        )
    assert env.calls == []


def test_live_mode_does_not_require_recordings_or_mount_them(environment):
    from scripts.demo_runner import Case

    env = environment
    missing = env.output.parent / "no-recordings"
    result = env.runner.run(
        cases=(Case("a", "cus_zs", "first"),),
        mode="live",
        recordings=missing,
        run_directory=env.output,
        observe_only=True,
    )
    assert result["status"] == "OBSERVED"
    config = yaml.safe_load((env.output / "runtime-a.yml").read_text())
    assert "volumes" not in config["services"]["agent"]
    assert not missing.exists()


def test_all_replay_files_checked_before_first_case(environment):
    from scripts.demo_runner import DemoFailure

    env = environment
    (env.recordings / "B2/b/replay.jsonl").unlink()
    with pytest.raises(DemoFailure, match="recording missing"):
        invoke(env)
    assert not any("drive_try_agent.py" in " ".join(a) for a, k in env.calls)


def test_failed_observation_can_never_be_verified(runner, tmp_path):
    value = manifest()
    value["status"] = "FAILED"
    (tmp_path / "16.json").write_text(json.dumps(proof()))
    with pytest.raises(runner.DemoFailure):
        runner.verify_receipts(value, tmp_path)


def test_explicit_subset_file_runs_its_rows_in_file_order(runner, monkeypatch, tmp_path):
    import sys
    from types import SimpleNamespace

    deploy = tmp_path / "deploy"
    deploy.mkdir()
    (deploy / "e2e.override.yml").write_text("services: {}\n")
    subset = tmp_path / "subset.tsv"
    subset.write_text("a\tcus_zs\tfirst\nb\tcus_ls\tsecond\n")
    observed = {}

    def run(**kwargs):
        observed["cases"] = kwargs["cases"]
        return {"run_id": "r", "mode": "replay", "status": "OBSERVED", "business_verified": False}

    monkeypatch.setattr(runner, "DemoRunner", lambda demo: SimpleNamespace(run=run))
    monkeypatch.setattr(
        sys, "argv", ["demo", "--deploy", str(deploy), "--cases", str(subset), "--observe-only"]
    )
    assert runner.main() == 0
    assert [c.id for c in observed["cases"]] == ["a", "b"]


def test_real_local_timeout_stops_process_group_without_echoing_output(runner, tmp_path):
    import os
    import sys

    with pytest.raises(runner.DemoFailure) as caught:
        runner.run_command(
            [
                sys.executable,
                "-c",
                "import time; print('private-output',flush=True); time.sleep(2)",
            ],
            cwd=tmp_path,
            env=dict(os.environ),
            timeout=0.05,
        )
    assert caught.value.exit_code == 124
    assert "private-output" not in str(caught.value)


@pytest.mark.parametrize("state", ["RUNNING", "FAILED", "UNKNOWN"])
def test_partial_or_unknown_run_status_cannot_be_verified(runner, tmp_path, state):
    value = manifest()
    value["status"] = state
    (tmp_path / "16.json").write_text(json.dumps(proof()))
    with pytest.raises(runner.DemoFailure):
        runner.verify_receipts(value, tmp_path)


def test_missing_selected_case_cannot_be_hidden_by_complete_first_case(runner, tmp_path):
    value = manifest()
    value["selected_case_ids"] = ["16", "23"]
    (tmp_path / "16.json").write_text(json.dumps(proof()))
    with pytest.raises(runner.DemoFailure):
        runner.verify_receipts(value, tmp_path)


def test_each_run_has_fresh_identity_and_request_bound_completed_trace(environment):
    import uuid

    env = environment
    result = invoke(env)
    calls = [(a, k) for a, k in env.calls if "drive_try_agent.py" in " ".join(a)]
    assert result["run_id"] != env.output.name
    ids = [k["env"].get("TRY_AGENT_REQUEST_ID") for a, k in calls]
    assert all(isinstance(value, str) and str(uuid.UUID(value)) == value for value in ids)
    assert len(set(ids)) == len(ids)
    for case, request_id in zip(result["cases"], ids, strict=True):
        trace = json.loads((env.output / f"{case['case_id']}.trace.json").read_text())
        assert trace["config"]["request_id_prefix"] == request_id


def test_same_basename_new_parent_rejects_old_trace_after_driver_startup_failure(environment):
    import subprocess

    from scripts.demo_runner import Case, DemoFailure, DemoRunner

    env = environment
    one = env.output.parent / "one/same-name"
    two = env.output.parent / "two/same-name"
    kwargs = dict(
        cases=(Case("a", "cus_zs", "first"),),
        mode="replay",
        recordings=env.recordings,
        observe_only=True,
    )
    first = env.runner.run(run_directory=one, **kwargs)
    old_bytes = (one / "a.trace.json").read_bytes()
    old_trace = json.loads(old_bytes)
    old_trace["config"].setdefault("request_id_prefix", "old-request")
    normal = env.runner.command
    second_driver = []

    def failed_startup(argv, **command_kwargs):
        if "drive_try_agent.py" in " ".join(argv):
            second_driver.append(command_kwargs)
            return subprocess.CompletedProcess(argv, 1, "", "Traceback: PIN file unavailable")
        if argv[:3] == ["docker", "compose", "exec"] and "operator" in argv:
            return subprocess.CompletedProcess(argv, 0, json.dumps(old_trace), "")
        return normal(argv, **command_kwargs)

    with pytest.raises(DemoFailure, match="trace"):
        DemoRunner(env.demo, failed_startup).run(run_directory=two, **kwargs)
    first_driver = next(k for a, k in env.calls if "drive_try_agent.py" in " ".join(a))
    assert first_driver["env"]["TRY_AGENT_OUT"] != second_driver[0]["env"]["TRY_AGENT_OUT"]
    assert (one / "a.trace.json").read_bytes() == old_bytes
    assert first["run_id"] != json.loads((two / "manifest.json").read_text())["run_id"]


@pytest.mark.parametrize(
    "request_id", ["../old-trace", "not-a-uuid", "", "01M68W8X2T8ZJEE4V6962ERSS7"]
)
def test_try_agent_rejects_invalid_explicit_request_id_before_pin_prompt(monkeypatch, request_id):
    from scripts import try_agent

    def no_prompt(*args, **kwargs):
        raise AssertionError("invalid request id should be rejected before reading PIN")

    monkeypatch.setattr(try_agent.getpass, "getpass", no_prompt)
    with pytest.raises(SystemExit) as stopped:
        try_agent._parse_args(
            ["--customer-id", "cus_zs", "--text", "查询余额", "--request-id", request_id]
        )
    assert stopped.value.code == 2


def test_try_agent_accepts_explicit_uuid_and_keeps_fresh_default():
    import uuid

    from scripts import try_agent

    args = ["--customer-id", "cus_zs", "--text", "查询余额", "--pin", "synthetic-only"]
    expected = "d174ee1e-31bc-4885-9b54-1c355b14796c"
    assert try_agent._parse_args([*args, "--request-id", expected]).request_id == expected
    assert str(uuid.UUID(try_agent._parse_args(args).request_id))


@pytest.mark.parametrize("request_id", [None, "d174ee1e-31bc-4885-9b54-1c355b14796c"])
def test_driver_forwards_only_explicit_invocation_request_id(monkeypatch, tmp_path, request_id):
    import importlib.util
    import io
    import sys
    from types import SimpleNamespace

    spec = importlib.util.spec_from_file_location(
        "runner_driver", ROOT / "docs/handoffs/2026-10-30-tools/drive_try_agent.py"
    )
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    pins = tmp_path / "synthetic-pins.json"
    pins.write_text('{"cus_zs":"synthetic-only"}')
    captured = []
    child = SimpleNamespace(
        stdout=io.BytesIO(b"EXIT: COMPLETED\n"), stdin=io.BytesIO(), returncode=1, wait=lambda: 1
    )
    monkeypatch.setattr(
        driver.subprocess, "Popen", lambda argv, **kwargs: captured.append(argv) or child
    )
    monkeypatch.setattr(driver.select, "select", lambda *args: ([child.stdout], [], []))
    monkeypatch.setattr(sys, "argv", ["driver", str(tmp_path), str(pins), "cus_zs", "查询余额"])
    if request_id is None:
        monkeypatch.delenv("TRY_AGENT_REQUEST_ID", raising=False)
    else:
        monkeypatch.setenv("TRY_AGENT_REQUEST_ID", request_id)
    assert driver.main() == 1
    if request_id is None:
        assert "--request-id" not in captured[0]
    else:
        index = captured[0].index("--request-id")
        assert captured[0][index + 1] == request_id
