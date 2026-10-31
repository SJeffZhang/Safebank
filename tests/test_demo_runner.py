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
        current=lambda: {"generation": generation, "initial": {"seed": "l1_seed_v1.0"}},
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


def test_empty_automatic_batch_is_refused_before_any_command(environment):
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


@pytest.mark.parametrize(
    "value",
    [
        {"otp_code": "276543"},
        {"payload": '{"pin":984321,"ok":false}'},
        {"payload": r'{"\u0070\u0069\u006e":984321,"ok":false}'},
        {"payload": '{"ＯＴＰ＿ＣＯＤＥ":"276543","ok":false}'},
    ],
)
def test_explicit_sensitive_keys_and_nested_json_never_persist_credentials(runner, value):
    cleaned = json.dumps(runner.sanitize_trace(value), ensure_ascii=False)
    assert "276543" not in cleaned
    assert "984321" not in cleaned
    assert value != runner.sanitize_trace(value)


@pytest.mark.parametrize(
    "text",
    [
        '{"pin":984321,"ok":',
        r'{"\u006f\u0074\u0070_code":"276543","ok":',
        "{'pin':984321,'ok':",
        '{"ＯＴＰ＿ＣＯＤＥ":"276543","ok":',
    ],
)
def test_malformed_sensitive_json_is_refused_without_repair(runner, text):
    with pytest.raises(runner.DemoFailure) as refused:
        runner.sanitize_trace({"payload": text})
    assert "984321" not in str(refused.value)
    assert "276543" not in str(refused.value)


def test_complete_legitimate_report_and_ordinary_money_keep_semantics(runner):
    value = {
        "total_cents": 644800,
        "previous_month_cents": 133190,
        "source_ref": "src_01M68W92GJFHMKBRB89AB5GVH3",
        "categories": [{"category": "entertainment", "amount_cents": 128000, "share_bp": 1985}],
        "payload": '{"amount_cents":349900,"memo":"normal report","ok":false}',
    }
    clean = runner.sanitize_trace(value)
    assert clean["total_cents"] == 644800
    assert clean["previous_month_cents"] == 133190
    assert clean["source_ref"] == value["source_ref"]
    assert clean["categories"] == value["categories"]
    assert json.loads(clean["payload"]) == json.loads(value["payload"])


def test_benign_malformed_call_remains_malformed(runner):
    malformed = '{"product_id": prd_mmf}'
    clean = runner.sanitize_trace({"payload": malformed})
    assert clean["payload"] == malformed
    with pytest.raises(ValueError):
        json.loads(clean["payload"])


def test_malformed_sensitive_trace_stops_before_any_evidence_file(environment):
    from scripts.demo_runner import DemoFailure

    env = environment
    original = env.runner.command

    def secret_trace(argv, **kwargs):
        result = original(argv, **kwargs)
        if argv[:3] == ["docker", "compose", "exec"] and "operator" in argv:
            trace = json.loads(result.stdout)
            trace["events"].append(
                {"kind": "sse_event", "detail": {"payload": '{"pin":984321,"ok":'}}
            )
            result.stdout = json.dumps(trace)
        return result

    env.runner.command = secret_trace
    with pytest.raises(DemoFailure):
        invoke(env)
    assert not (env.output / "a.trace.json").exists()
    assert not (env.output / "b.trace.json").exists()
    value = json.loads((env.output / "manifest.json").read_text())
    assert value["status"] == "FAILED" and value["cases"] == []


def test_manifest_uses_same_sanitized_typed_text_as_trace(environment):
    from scripts.demo_runner import Case

    env = environment
    result = env.runner.run(
        cases=(Case("a", "cus_zs", '{"pin":984321,"amount_cents":128000}'),),
        mode="replay",
        recordings=env.recordings,
        run_directory=env.output,
        observe_only=True,
    )
    assert "984321" not in json.dumps(result)
    assert "984321" not in (env.output / "a.trace.json").read_text()
    assert json.loads(result["cases"][0]["typed_text"])["amount_cents"] == 128000


def session_events(sid="ses_fixture"):
    return [
        {"kind": "http_request", "detail": {"to": "G15", "url": "http://gateway:8200/sessions"}},
        {
            "kind": "http_response",
            "detail": {"from": "G15", "status": 201, "body": {"session_id": sid}},
        },
        {"kind": "result", "detail": {"event": "session_created", "session_id": sid}},
    ]


def test_session_binding_uses_trusted_response_and_ignores_free_text(runner):
    trace = {
        "events": [
            {"kind": "sse_event", "detail": {"message": "ses_attack"}},
            *session_events(),
            {"kind": "result", "detail": {"event": "l1_complete", "reply": "ses_other"}},
        ]
    }
    assert runner.session_from_trace(trace) == "ses_fixture"


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_body",
        "duplicate_response",
        "wrong_created",
        "wrong_path",
        "wrong_order",
        "invalid_sid",
    ],
)
def test_session_response_must_be_unique_bound_and_valid(runner, mutation):
    events = session_events()
    if mutation == "missing_body":
        events[1]["detail"].pop("body")
    elif mutation == "duplicate_response":
        events.insert(2, events[1])
    elif mutation == "wrong_created":
        events[2]["detail"]["session_id"] = "ses_other"
    elif mutation == "wrong_path":
        events[0]["detail"]["url"] = "http://gateway:8200/plans"
    elif mutation == "wrong_order":
        events[0], events[1] = events[1], events[0]
    else:
        events[1]["detail"]["body"]["session_id"] = "ses_bad';SELECT 1"
    with pytest.raises(runner.DemoFailure):
        runner.session_from_trace({"events": events})


@pytest.fixture
def auto_sql(environment):
    import importlib.util
    from types import SimpleNamespace

    env = environment
    spec = importlib.util.spec_from_file_location(
        "owned_sql_payloads", ROOT / "tests/test_demo_sql.py"
    )
    payloads = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(payloads)
    env.demo.current = lambda: {
        "generation": "demo_20261031000000_1234abcd",
        "initial": {"seed": "l1_seed_v1.0"},
    }
    state = {"case": "16", "sql_failure": None, "trace_mutation": None, "payload_mutation": None}
    calls = []
    original = env.runner.command

    def facts():
        before, after = payloads.good(state["case"])
        raw = json.dumps([before["bank"], after["bank"], after["gateway"]])
        raw = raw.replace(payloads.GEN, "demo_20261031000000_1234abcd")
        raw = raw.replace("ses_fixture", "ses_fixture_" + state["case"])
        values = json.loads(raw)
        if state["payload_mutation"]:
            state["payload_mutation"](values)
        return values

    def compose(*args, **kwargs):
        index = len(calls) % 3
        calls.append((args, kwargs))
        env.calls.append((["SQL", str(index)], {}))
        if state["sql_failure"] == index:
            raise RuntimeError("postgresql://private-password@postgres-bank/secret")
        return json.dumps(facts()[index])

    env.demo.compose = compose

    def command(argv, **kwargs):
        env.settings["exit"] = 1 if state["case"] in {"16", "27"} else 9
        result = original(argv, **kwargs)
        if argv[:3] == ["docker", "compose", "exec"] and "operator" in argv:
            trace = json.loads(result.stdout)
            trace["events"] = session_events("ses_fixture_" + state["case"]) + [
                {
                    "kind": "result",
                    "detail": {
                        "event": "l1_complete",
                        "reply": "report complete",
                        "decision": None,
                    },
                }
            ]
            if state["trace_mutation"]:
                state["trace_mutation"](trace)
            result.stdout = json.dumps(trace)
        return result

    env.runner.command = command
    return SimpleNamespace(env=env, state=state, calls=calls, payloads=payloads)


def run_core(auto, case_id="16", **kwargs):
    from scripts.demo_runner import Case

    auto.state["case"] = case_id
    customer = "cus_ls" if case_id in {"34", "23"} else "cus_zs"
    return auto.env.runner.run(
        cases=(Case(case_id, customer, auto.payloads.TEXTS[case_id]),),
        mode="live",
        recordings=auto.env.recordings,
        run_directory=auto.env.output,
        observe_only=False,
        **kwargs,
    )


@pytest.mark.parametrize("case_id", ["01", "16", "27", "34", "36", "23"])
def test_default_verified_mode_runs_actual_oracle_on_captured_payloads(auto_sql, case_id):
    auto = auto_sql
    value = run_core(auto, case_id)
    assert value["status"] == "VERIFIED" and value["business_verified"] is True
    assert value["cases"][0]["business_verified"] is True
    assert len(auto.calls) == 3
    for args, kwargs in auto.calls:
        assert args[:4] == ("exec", "-T", "-e", "PGOPTIONS=-c default_transaction_read_only=on")
        assert "postgres-bank" in args and "-X" in args and "ON_ERROR_STOP=1" in args
        assert kwargs["data"].lstrip().startswith("SELECT ")
    before = json.loads((auto.env.output / f"{case_id}.before.json").read_text())
    after = json.loads((auto.env.output / f"{case_id}.after.json").read_text())
    proof = json.loads((auto.env.output / "sql-receipts" / f"{case_id}.json").read_text())
    assert before["meta"] == after["meta"]
    assert before["meta"]["session_id"] == "ses_fixture_" + case_id
    assert before["meta"]["run_id"] == value["run_id"]
    assert before["meta"]["trace_sha256"] == value["cases"][0]["trace_sha256"]
    assert proof["verified"] is True
    assert (auto.env.output / f"{case_id}.before.json").stat().st_mode & 0o777 == 0o600
    driver_index = next(
        i for i, (a, k) in enumerate(auto.env.calls) if "drive_try_agent.py" in " ".join(a)
    )
    sql_indices = [i for i, (a, k) in enumerate(auto.env.calls) if a[0] == "SQL"]
    assert sql_indices[0] < driver_index < sql_indices[1] < sql_indices[2]


def test_unsupported_automatic_case_fails_before_any_runtime(environment):
    from scripts.demo_runner import Case, DemoFailure

    env = environment
    with pytest.raises(DemoFailure, match="unsupported"):
        env.runner.run(
            cases=(Case("99", "cus_zs", "unknown"),),
            mode="live",
            recordings=env.recordings,
            run_directory=env.output,
            observe_only=False,
        )
    assert env.calls == []


@pytest.mark.parametrize("failure", [0, 1, 2])
def test_sql_failure_stops_without_false_receipt_and_withholds_connection_details(
    auto_sql, failure
):
    from scripts.demo_runner import DemoFailure

    auto = auto_sql
    auto.state["sql_failure"] = failure
    with pytest.raises(DemoFailure) as stopped:
        run_core(auto)
    assert "private-password" not in str(stopped.value)
    assert not (auto.env.output / "sql-receipts/16.json").exists()
    value = json.loads((auto.env.output / "manifest.json").read_text())
    assert value["status"] == "FAILED" and value["business_verified"] is False
    if failure > 0:
        assert (auto.env.output / "16.before-bank.json").exists()


def test_oracle_mismatch_preserves_snapshots_but_never_issues_receipt(auto_sql):
    from scripts.demo_runner import DemoFailure

    auto = auto_sql
    auto.state["payload_mutation"] = lambda values: values[2].update(reads=[])
    with pytest.raises(DemoFailure):
        run_core(auto)
    assert (auto.env.output / "16.before.json").exists()
    assert (auto.env.output / "16.after.json").exists()
    assert not (auto.env.output / "sql-receipts/16.json").exists()


def test_unbound_session_cannot_trigger_after_sql_or_receipt(auto_sql):
    from scripts.demo_runner import DemoFailure

    auto = auto_sql
    auto.state["trace_mutation"] = lambda trace: trace["events"].clear()
    with pytest.raises(DemoFailure):
        run_core(auto)
    assert len(auto.calls) == 1
    assert not (auto.env.output / "sql-receipts/16.json").exists()


def test_try_agent_session_response_exposes_only_actual_session_id(monkeypatch):
    import httpx

    from scripts import try_agent

    monkeypatch.setenv("FRONTEND_SERVICE_TOKEN", "synthetic-frontend")
    sid = "ses_actual_response"

    def handler(request):
        if request.url.path == "/auth/login":
            return httpx.Response(200, json={"app_token": "synthetic-app"})
        if request.url.path == "/sessions":
            return httpx.Response(201, json={"session_id": sid, "token": "must-not-copy"})
        return httpx.Response(400, json={"code": "EXPECTED_TEST_STOP"})

    cfg = try_agent.Config(
        gateway_url="http://gateway:8200",
        agent_url="http://agent:8100",
        customer_id="cus_zs",
        text="查询余额",
        pin="synthetic",
    )
    events = []
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert try_agent.run(cfg, client, events) == try_agent.EXIT_HTTP_FAILURE
    response = next(
        e.detail for e in events if e.kind == "http_response" and e.detail.get("from") == "G15"
    )
    assert response.get("body") == {"session_id": sid}
    assert "must-not-copy" not in json.dumps(response)


def test_sensitive_sql_payload_is_refused_before_snapshot_or_driver(auto_sql):
    from scripts.demo_runner import DemoFailure

    auto = auto_sql
    auto.state["payload_mutation"] = lambda values: values[0].update(otp_code="276543")
    with pytest.raises(DemoFailure):
        run_core(auto)
    assert not (auto.env.output / "16.before-bank.json").exists()
    assert not any("drive_try_agent.py" in " ".join(a) for a, k in auto.env.calls)
    assert not (auto.env.output / "sql-receipts/16.json").exists()


def test_observation_mode_makes_no_oracle_sql_and_stays_unverified(auto_sql):
    from scripts.demo_runner import Case

    auto = auto_sql
    result = auto.env.runner.run(
        cases=(Case("16", "cus_zs", auto.payloads.TEXTS["16"]),),
        mode="live",
        recordings=auto.env.recordings,
        run_directory=auto.env.output,
        observe_only=True,
    )
    assert result["business_verified"] is False and result["status"] == "OBSERVED"
    assert auto.calls == []
    assert not (auto.env.output / "sql-receipts").exists()


def test_external_proofs_mode_retains_explicit_proof_contract_without_auto_sql(auto_sql, tmp_path):
    from scripts.demo_runner import DemoFailure

    auto = auto_sql
    external = tmp_path / "external-proofs"
    external.mkdir()
    with pytest.raises(DemoFailure, match="receipt"):
        run_core(auto, proofs=external)
    assert auto.calls == []
    assert (auto.env.output / "16.trace.json").exists()
    assert not (auto.env.output / "sql-receipts").exists()


@pytest.mark.parametrize("field", ["text", "customer_id"])
def test_noncanonical_auto_case_refuses_before_runtime(environment, field):
    from scripts.demo_runner import Case, DemoFailure

    env = environment
    case = {"id": "16", "customer_id": "cus_zs", "text": "给我出一份9月的账单报告"}
    case[field] = "different"
    with pytest.raises(DemoFailure, match="unsupported"):
        env.runner.run(
            cases=(Case(**case),),
            mode="live",
            recordings=env.recordings,
            run_directory=env.output,
            observe_only=False,
        )
    assert env.calls == []


def test_first_oracle_failure_stops_next_case_and_retains_fault_snapshots(auto_sql):
    from scripts.demo_runner import Case, DemoFailure

    auto = auto_sql
    auto.state["payload_mutation"] = lambda values: values[2].update(reads=[])
    cases = tuple(Case(i, "cus_zs", auto.payloads.TEXTS[i]) for i in ("16", "27"))
    with pytest.raises(DemoFailure):
        auto.env.runner.run(
            cases=cases,
            mode="live",
            recordings=auto.env.recordings,
            run_directory=auto.env.output,
            observe_only=False,
        )
    drivers = [a for a, k in auto.env.calls if "drive_try_agent.py" in " ".join(a)]
    assert len(drivers) == 1
    assert (auto.env.output / "16.before.json").exists()
    assert (auto.env.output / "16.after.json").exists()
    assert not (auto.env.output / "runtime-27.yml").exists()
    assert not list((auto.env.output / "sql-receipts").iterdir())


def test_error_trace_cannot_be_turned_green_by_correct_sql(auto_sql):
    from scripts.demo_runner import DemoFailure

    auto = auto_sql
    auto.state["trace_mutation"] = lambda trace: trace["events"].append(
        {"kind": "error", "detail": "controlled failure"}
    )
    with pytest.raises(DemoFailure):
        run_core(auto)
    assert len(auto.calls) == 1
    assert not (auto.env.output / "sql-receipts/16.json").exists()


def test_default_replay_cli_automatically_verifies_core_case_without_external_proof(
    auto_sql, monkeypatch
):
    import sys

    from scripts import demo_runner

    auto = auto_sql
    (auto.env.demo.deploy / "e2e.override.yml").write_text("services: {}\n")
    recording = auto.env.recordings / "B2/16/replay.jsonl"
    recording.parent.mkdir()
    recording.write_text("synthetic replay boundary\n")
    monkeypatch.delenv("LLM_MODE", raising=False)
    monkeypatch.setattr(demo_runner, "DemoEnvironment", lambda deploy: auto.env.demo)
    monkeypatch.setattr(demo_runner, "DemoRunner", lambda demo: auto.env.runner)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "demo",
            "--deploy",
            str(auto.env.demo.deploy),
            "--case-ids",
            "16",
            "--recordings",
            str(auto.env.recordings),
            "--run-dir",
            str(auto.env.output),
        ],
    )
    assert demo_runner.main() == 0
    value = json.loads((auto.env.output / "manifest.json").read_text())
    assert value["mode"] == "replay" and value["status"] == "VERIFIED"
    assert value["business_verified"] is True and len(auto.calls) == 3
    assert value["internal_networks"] == ["private"]
    assert value["cases"][0]["offline_probe"]["external_tcp_blocked"] == [
        "1.1.1.1",
        "api.deepseek.com",
    ]


@pytest.mark.parametrize("mutation", ["invalid_event", "invalid_url"])
def test_corrupt_session_metadata_is_a_controlled_refusal(runner, mutation):
    events = session_events()
    if mutation == "invalid_event":
        events.insert(0, None)
    else:
        events[0]["detail"]["url"] = 17
    with pytest.raises(runner.DemoFailure):
        runner.session_from_trace({"events": events})


def test_automatic_seed_label_must_match_actual_before_driver(auto_sql):
    from scripts.demo_runner import DemoFailure

    auto = auto_sql
    current = auto.env.demo.current
    auto.env.demo.current = lambda: {**current(), "initial": {"seed": "unexpected_seed_label"}}
    with pytest.raises(DemoFailure, match="seed"):
        run_core(auto)
    assert not any("drive_try_agent.py" in " ".join(a) for a, k in auto.env.calls)
    assert not (auto.env.output / "sql-receipts/16.json").exists()
    value = json.loads((auto.env.output / "manifest.json").read_text())
    assert value["status"] == "FAILED" and value["business_verified"] is False
    assert (auto.env.output / "16.before-bank.json").exists()


def test_actual_after_seed_is_bound_before_gateway_query_or_receipt(auto_sql):
    from scripts.demo_runner import DemoFailure

    auto = auto_sql
    auto.state["payload_mutation"] = lambda values: values[1]["clock"].update(
        dataset_revision="different_after_seed"
    )
    with pytest.raises(DemoFailure, match="seed"):
        run_core(auto)
    assert len(auto.calls) == 2
    assert (auto.env.output / "16.after-bank.json").exists()
    assert not (auto.env.output / "16.after-gateway.json").exists()
    assert not (auto.env.output / "sql-receipts/16.json").exists()


def final_checkpoint_writer(monkeypatch, auto, *, external, partial, fail):
    from scripts import demo_runner

    original = demo_runner.private_write
    proof_dir = auto.env.output.parent / "external-proofs"
    proof_dir.mkdir()
    state = {"failed": False, "fault_path": None}

    def write(path, data):
        if path.name.startswith("manifest.next"):
            value = json.loads(data)
            if external and value["status"] == "OBSERVED":
                for case in value["cases"]:
                    receipt = {
                        **{key: value[key] for key in ("run_id", "checkout_sha", "generation")},
                        "case_id": case["case_id"],
                        "trace_sha256": case["trace_sha256"],
                        "verifier": "SQL",
                        "verified": True,
                        "checks": {"synthetic_external_proof": True},
                    }
                    target = proof_dir / f"{case['case_id']}.json"
                    if not target.exists():
                        original(target, json.dumps(receipt))
            if fail and value["status"] == "VERIFIED" and not state["failed"]:
                state["failed"] = True
                state["fault_path"] = path
                if partial:
                    original(path, "partial-verification-checkpoint")
                raise OSError("synthetic final checkpoint failure")
        return original(path, data)

    monkeypatch.setattr(demo_runner, "private_write", write)
    return proof_dir, state


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize("partial", [False, True])
def test_final_persistence_failure_clears_all_verification_flags_and_preserves_evidence(
    auto_sql, monkeypatch, external, partial
):
    auto = auto_sql
    proofs, state = final_checkpoint_writer(
        monkeypatch, auto, external=external, partial=partial, fail=True
    )
    kwargs = {"proofs": proofs} if external else {}
    with pytest.raises(OSError, match="synthetic final checkpoint failure"):
        run_core(auto, **kwargs)
    value = json.loads((auto.env.output / "manifest.json").read_text())
    assert value["status"] == "FAILED"
    assert value["business_verified"] is False
    assert value["cases"] and all(case["business_verified"] is False for case in value["cases"])
    assert (auto.env.output / "16.trace.json").exists()
    receipt = (proofs if external else auto.env.output / "sql-receipts") / "16.json"
    assert receipt.exists()
    if partial:
        assert state["fault_path"].read_text() == "partial-verification-checkpoint"
    assert len(auto.calls) == (0 if external else 3)


@pytest.mark.parametrize("external", [False, True])
def test_successful_persistence_retains_verified_flags_in_both_proof_modes(
    auto_sql, monkeypatch, external
):
    auto = auto_sql
    proofs, _ = final_checkpoint_writer(
        monkeypatch, auto, external=external, partial=False, fail=False
    )
    value = run_core(auto, **({"proofs": proofs} if external else {}))
    disk = json.loads((auto.env.output / "manifest.json").read_text())
    assert disk == value and disk["status"] == "VERIFIED" and disk["business_verified"] is True
    assert all(case["business_verified"] is True for case in disk["cases"])
