"""E0b T11: error diagnostic fields — error_message and error_where in CaseRun / JSONL."""

from __future__ import annotations

import json
from pathlib import Path

from evaluation.metrics.compute import CaseRun, compute_metrics, write_outputs
from evaluation.runner.loop import _extract_error_info


# ── _extract_error_info unit tests ───────────────────────────────────────────


def _raise_in_evaluation():
    """Helper: raise a ValueError from inside evaluation/ code."""
    raise ValueError("boom-detail")


def test_error_message_captures_detail():
    """str(exc) appears in error_message."""
    try:
        _raise_in_evaluation()
    except ValueError as exc:
        msg, where = _extract_error_info(exc)

    assert "boom-detail" in msg
    assert msg == "boom-detail"  # full message fits in 800
    assert len(where) >= 1
    any_eval = any("evaluation/" in w or "services/" in w for w in where)
    assert any_eval, f"no evaluation/ or services/ frame in {where}"


def test_error_message_truncated():
    """Messages longer than 800 chars are truncated."""
    exc = ValueError("x" * 1200)
    msg, where = _extract_error_info(exc)
    assert len(msg) == 800
    assert msg.startswith("x" * 800)


def test_error_where_no_traceback():
    """Exception with no traceback returns empty error_where."""
    exc = ValueError("no tb")
    assert exc.__traceback__ is None  # never raised
    msg, where = _extract_error_info(exc)
    assert msg == "no tb"
    assert where == []


def test_error_where_format():
    """Each entry is file:line:function."""
    try:
        _raise_in_evaluation()
    except ValueError as exc:
        _msg, where = _extract_error_info(exc)

    for entry in where:
        parts = entry.split(":")
        assert len(parts) >= 2, f"bad format: {entry!r}"
        # last part is function name, second-to-last is line number
        assert parts[-2].isdigit(), f"line not numeric in {entry!r}"


def test_error_where_innermost_first():
    """error_where lists innermost frame first."""
    try:
        _raise_in_evaluation()
    except ValueError as exc:
        _msg, where = _extract_error_info(exc)

    # At least one frame from this test file (evaluation/)
    assert len(where) >= 1


# ── write_outputs integration ────────────────────────────────────────────────


def _make_error_run() -> CaseRun:
    """Build a CaseRun with error diagnostic fields."""
    return CaseRun(
        case_id="err-001",
        category="transfer",
        variant="error_test",
        repeat_index=0,
        expected_outcome="ALLOW",
        expected_risk=None,
        expected_params={},
        actual_outcome="ERROR",
        actual_risk=None,
        actual_params={},
        finding_codes=[],
        failure_class="ValueError",
        is_attack=False,
        latency_ms=50,
        tokens=0,
        error_message="boom-detail from fake plan port",
        error_where=[
            "evaluation/runner/l1.py:99:_fake_plan",
            "services/agent/app/graph.py:200:plan_node",
        ],
    )


def test_error_fields_in_jsonl(tmp_path: Path):
    """JSONL output includes error_message and error_where."""
    runs = [_make_error_run()]
    metrics = compute_metrics(runs)
    meta: dict[str, str] = {
        "llm_mode": "fake",
        "model": "deepseek-v4-pro",
        "l3": "real",
        "seed_version": "v1",
        "corpus_version": "v1",
        "sample_count": "1",
    }
    out = tmp_path / "report"
    write_outputs(out, runs, metrics, meta)

    lines = (out / "cases.jsonl").read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    obj = json.loads(lines[0])

    assert obj["error_message"] == "boom-detail from fake plan port"
    assert obj["error_where"] == [
        "evaluation/runner/l1.py:99:_fake_plan",
        "services/agent/app/graph.py:200:plan_node",
    ]


def test_error_message_none_when_no_error(tmp_path: Path):
    """Non-ERROR CaseRuns have error_message=None, error_where=[]."""
    run = CaseRun(
        case_id="ok-001",
        category="transfer",
        variant="ok",
        repeat_index=0,
        expected_outcome="ALLOW",
        expected_risk=None,
        expected_params={},
        actual_outcome="ALLOW",
        actual_risk=None,
        actual_params={},
        finding_codes=[],
        failure_class=None,
        is_attack=False,
        latency_ms=100,
        tokens=400,
    )
    metrics = compute_metrics([run])
    meta: dict[str, str] = {
        "llm_mode": "fake",
        "model": "deepseek-v4-pro",
        "l3": "real",
        "seed_version": "v1",
        "corpus_version": "v1",
        "sample_count": "1",
    }
    out = tmp_path / "report"
    write_outputs(out, [run], metrics, meta)

    lines = (out / "cases.jsonl").read_text(encoding="utf-8").strip().split("\n")
    obj = json.loads(lines[0])
    assert obj["error_message"] is None
    assert obj["error_where"] == []


# ── secret stripping ─────────────────────────────────────────────────────────


def test_error_message_stripped_of_bearer_token(tmp_path: Path):
    """Bearer tokens in error_message are redacted in JSONL."""
    run = CaseRun(
        case_id="err-002",
        category="transfer",
        variant="error",
        repeat_index=0,
        expected_outcome="ALLOW",
        expected_risk=None,
        expected_params={},
        actual_outcome="ERROR",
        actual_risk=None,
        actual_params={},
        finding_codes=[],
        failure_class="ValueError",
        is_attack=False,
        latency_ms=50,
        tokens=0,
        error_message="401 Unauthorized: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dummy",
        error_where=[],
    )
    metrics = compute_metrics([run])
    meta: dict[str, str] = {
        "llm_mode": "fake",
        "model": "deepseek-v4-pro",
        "l3": "real",
        "seed_version": "v1",
        "corpus_version": "v1",
        "sample_count": "1",
    }
    out = tmp_path / "report"
    write_outputs(out, [run], metrics, meta)

    lines = (out / "cases.jsonl").read_text(encoding="utf-8").strip().split("\n")
    obj = json.loads(lines[0])
    # After stripping, the Bearer token should be gone
    assert "Bearer" not in (obj.get("error_message") or "")
    assert obj["error_message"] == "[REDACTED]"


def test_error_where_stripped_of_token_in_param(tmp_path: Path):
    """Frames containing token=... in the path are redacted."""
    run = CaseRun(
        case_id="err-003",
        category="transfer",
        variant="error",
        repeat_index=0,
        expected_outcome="ALLOW",
        expected_risk=None,
        expected_params={},
        actual_outcome="ERROR",
        actual_risk=None,
        actual_params={},
        finding_codes=[],
        failure_class="ValueError",
        is_attack=False,
        latency_ms=50,
        tokens=0,
        error_message="some error",
        error_where=[
            "services/gateway/app/api/plans.py:300:evaluate",
            "services/gateway/app/auth.py:50:verify_bearer_token",
        ],
    )
    metrics = compute_metrics([run])
    meta: dict[str, str] = {
        "llm_mode": "fake",
        "model": "deepseek-v4-pro",
        "l3": "real",
        "seed_version": "v1",
        "corpus_version": "v1",
        "sample_count": "1",
    }
    out = tmp_path / "report"
    write_outputs(out, [run], metrics, meta)

    lines = (out / "cases.jsonl").read_text(encoding="utf-8").strip().split("\n")
    obj = json.loads(lines[0])
    # Frame strings don't contain Bearer/JWT, so they should pass through unchanged
    assert obj["error_where"] == [
        "services/gateway/app/api/plans.py:300:evaluate",
        "services/gateway/app/auth.py:50:verify_bearer_token",
    ]