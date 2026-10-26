"""E0b T6: per-sample evidence archiving — unit + pure-function tests.

All tests use fake transport data and constructed CaseRuns — no real DB,
no real L2/G4, no live LLM, no HMAC dependency.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from evaluation.metrics.compute import (
    CaseRun,
    _strip_secrets,
    compute_metrics,
    extract_g4_evidence,
    write_outputs,
)

# ── helpers ──────────────────────────────────────────────────────────────────


def _make_minimal_run(**overrides: Any) -> CaseRun:
    """Minimal CaseRun with sensible defaults."""
    kw: dict[str, Any] = dict(
        case_id="tc-01",
        category="transfer",
        variant="v1",
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
        tokens=0,
    )
    kw.update(overrides)
    return CaseRun(**kw)


def _fake_transport_evaluate(
    decision: str = "ALLOW",
    risk: str | None = None,
    findings: list[dict] | None = None,
    plan_draft: dict | None = None,
    status: int = 200,
    error_code: str | None = None,
) -> list[dict]:
    """Create a fake transport.requests entry for a single G4 evaluate call."""
    req_body: dict = {"plan_draft": plan_draft or _fake_plan_draft(), "request_id": "req_01"}
    resp_body: dict = {"decision": decision}
    if risk is not None:
        resp_body["risk"] = risk
    if findings is not None:
        resp_body["findings"] = findings
    if error_code:
        resp_body = {"error": {"code": error_code}}
    return [
        {
            "method": "POST",
            "url": "http://gateway:8200/plans/evaluate",
            "status": status,
            "request": req_body,
            "json": resp_body,
        }
    ]


def _fake_plan_draft(**overrides: Any) -> dict:
    """A minimal plan_draft dict matching the G4 wire shape."""
    draft: dict[str, Any] = {
        "request_id": "req_01",
        "session_id": "ses_test",
        "utterance_ids": ["utt_01"],
        "intents": ["freeze_card"],
        "steps": [
            {
                "step_id": "s1",
                "tool": "freeze_card",
                "args": {"card_id": "crd_zs_1"},
                "depends_on": [],
                "arg_sources": [
                    {
                        "param": "card_id",
                        "source": {
                            "kind": "utterance",
                            "ref": "utt_01",
                            "span": [0, 5],
                        },
                    }
                ],
            }
        ],
        "rationale": "test",
    }
    draft.update(overrides)
    return draft


def _fake_multi_step_plan_draft() -> dict:
    """PlanDraft with two steps, different amounts and sources."""
    return {
        "request_id": "req_multi",
        "session_id": "ses_test",
        "utterance_ids": ["utt_m1"],
        "intents": ["freeze_card", "transfer"],
        "steps": [
            {
                "step_id": "s1",
                "tool": "freeze_card",
                "args": {"card_id": "crd_zs_1"},
                "depends_on": [],
                "arg_sources": [
                    {
                        "param": "card_id",
                        "source": {"kind": "utterance", "ref": "utt_m1", "span": [0, 5]},
                    }
                ],
            },
            {
                "step_id": "s2",
                "tool": "transfer",
                "args": {"amount_cents": 100000, "payee_id": "pay_ww"},
                "depends_on": ["s1"],
                "arg_sources": [
                    {
                        "param": "amount_cents",
                        "source": {"kind": "utterance", "ref": "utt_m1", "span": [6, 10]},
                    },
                    {
                        "param": "payee_id",
                        "source": {
                            "kind": "read",
                            "ref": "src_read_01",
                            "pointer": "/items/0/id",
                        },
                    },
                ],
            },
        ],
        "rationale": "multi-step test",
    }


# ── _strip_secrets ──────────────────────────────────────────────────────────


class TestStripSecrets:
    """Pure-function: recursive stripping of auth/key/PIN/Bearer/JWT."""

    def test_strips_token_key_fields(self):
        obj = {
            "access_token": "abc123",
            "api_key": "secret-key",
            "data": {"Authorization": "Bearer xyz"},
            "safe_field": "keep me",
        }
        result = _strip_secrets(obj)
        assert result["access_token"] == "[REDACTED]"
        assert result["api_key"] == "[REDACTED]"
        assert result["data"]["Authorization"] == "[REDACTED]"
        assert result["safe_field"] == "keep me"

    def test_strips_bearer_value(self):
        obj = {"header": "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dummy"}
        result = _strip_secrets(obj)
        assert result["header"] == "[REDACTED]"

    def test_strips_jwt_string(self):
        # JWT: base64url.base64url.base64url, ≥50 chars
        jwt = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIn0."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        )
        obj = {"token": jwt}
        result = _strip_secrets(obj)
        assert result["token"] == "[REDACTED]"

    def test_keeps_short_strings(self):
        obj = {"id": "crd_zs_1", "name": "王五", "amount": "100000"}
        result = _strip_secrets(obj)
        assert result == obj

    def test_keeps_business_fields(self):
        """card_id, payee_id, account_no etc. should NOT be stripped."""
        obj = {"card_id": "crd_zs_1", "payee_id": "pay_ww", "session_id": "ses_01"}
        result = _strip_secrets(obj)
        assert result == obj

    def test_strips_nested_list(self):
        obj = {"items": [{"api_key": "k1"}, {"safe": "v1"}]}
        result = _strip_secrets(obj)
        assert result["items"][0]["api_key"] == "[REDACTED]"
        assert result["items"][1]["safe"] == "v1"

    def test_strips_pin_fields(self):
        """PIN-related fields should be stripped."""
        obj = {"pin": "123456", "otp_pin_code": "789012"}
        result = _strip_secrets(obj)
        assert result["pin"] == "[REDACTED]"
        assert result["otp_pin_code"] == "[REDACTED]"

    def test_strips_secret_fields(self):
        obj = {"secret": "s3cr3t", "client_secret": "cs"}
        result = _strip_secrets(obj)
        assert result["secret"] == "[REDACTED]"
        assert result["client_secret"] == "[REDACTED]"

    def test_strips_jwt_key(self):
        obj = {"jwt": "something", "assistant_jwt": "token-value"}
        result = _strip_secrets(obj)
        assert result["jwt"] == "[REDACTED]"
        assert result["assistant_jwt"] == "[REDACTED]"

    def test_handles_primitives(self):
        assert _strip_secrets(42) == 42
        assert _strip_secrets("hello") == "hello"
        assert _strip_secrets(None) is None
        assert _strip_secrets(True) is True

    def test_depth_limit(self):
        """Deeply nested structures should be handled (capped)."""
        obj: Any = {"a": {}}
        cur = obj["a"]
        for _ in range(20):
            cur["a"] = {}
            cur = cur["a"]
        # Should not raise RecursionError
        result = _strip_secrets(obj)
        assert isinstance(result, dict)


# ── extract_g4_evidence ─────────────────────────────────────────────────────


class TestExtractG4Evidence:
    """Pure function: extract evidence from transport requests."""

    def test_single_evaluate_200(self):
        pd = _fake_plan_draft()
        reqs = _fake_transport_evaluate(
            decision="REQUIRE_APPROVAL",
            risk="YELLOW",
            findings=[{"code": "X1"}],
            plan_draft=pd,
        )
        evidence = extract_g4_evidence(reqs)
        assert evidence["plan_draft"] == pd
        assert evidence["g4_decision"] == "REQUIRE_APPROVAL"
        assert evidence["g4_findings"] == [{"code": "X1"}]
        assert len(evidence["g4_attempts"]) == 1
        att = evidence["g4_attempts"][0]
        assert att["status"] == 200
        assert att["decision"] == "REQUIRE_APPROVAL"
        assert att["risk"] == "YELLOW"
        assert att["error_code"] is None

    def test_multi_step_plan_draft_all_steps_preserved(self):
        """Every step with its arg_sources appears in evidence."""
        pd = _fake_multi_step_plan_draft()
        reqs = _fake_transport_evaluate(plan_draft=pd)
        evidence = extract_g4_evidence(reqs)
        steps = evidence["plan_draft"]["steps"]
        assert len(steps) == 2
        # Step 1: freeze_card with utterance source
        assert steps[0]["tool"] == "freeze_card"
        assert len(steps[0]["arg_sources"]) == 1
        # Step 2: transfer with two arg_sources (amount + payee_id)
        assert steps[1]["tool"] == "transfer"
        assert len(steps[1]["arg_sources"]) == 2
        kinds = {s["source"]["kind"] for s in steps[1]["arg_sources"]}
        assert kinds == {"utterance", "read"}

    def test_multiple_g4_attempts_recorded(self):
        """Every G4 call is recorded, not just the last one."""
        pd = _fake_plan_draft()
        reqs = [
            {  # first attempt: 422
                "method": "POST",
                "url": "http://gateway:8200/plans/evaluate",
                "status": 422,
                "request": {"plan_draft": pd, "request_id": "req_01"},
                "json": {"error": {"code": "PARAM_MISMATCH"}},
            },
            {  # second attempt: 200
                "method": "POST",
                "url": "http://gateway:8200/plans/evaluate",
                "status": 200,
                "request": {"plan_draft": pd, "request_id": "req_02"},
                "json": {"decision": "ALLOW"},
            },
        ]
        evidence = extract_g4_evidence(reqs)
        assert len(evidence["g4_attempts"]) == 2
        assert evidence["g4_attempts"][0]["status"] == 422
        assert evidence["g4_attempts"][0]["error_code"] == "PARAM_MISMATCH"
        assert evidence["g4_attempts"][0]["decision"] is None
        assert evidence["g4_attempts"][1]["status"] == 200
        assert evidence["g4_attempts"][1]["decision"] == "ALLOW"
        assert evidence["g4_attempts"][1]["error_code"] is None
        # Last succeeds → decision/findings from it
        assert evidence["g4_decision"] == "ALLOW"

    def test_503_not_lost_as_no_plan(self):
        """503 from G4 must be recorded as an attempt with error_code, not dropped."""
        pd = _fake_plan_draft()
        reqs = [
            {
                "method": "POST",
                "url": "http://gateway:8200/plans/evaluate",
                "status": 503,
                "request": {"plan_draft": pd, "request_id": "req_01"},
                "json": {"error": {"code": "DEPENDENCY_UNAVAILABLE"}},
            },
        ]
        evidence = extract_g4_evidence(reqs)
        assert len(evidence["g4_attempts"]) == 1
        att = evidence["g4_attempts"][0]
        assert att["status"] == 503
        assert att["error_code"] == "DEPENDENCY_UNAVAILABLE"
        assert att["decision"] is None
        # No successful G4 → plan_draft still from the request for diagnostics
        assert evidence["plan_draft"] == pd
        assert evidence["g4_decision"] is None

    def test_no_evaluate_empty_evidence(self):
        """No /plans/evaluate calls → all evidence is empty/default."""
        evidence = extract_g4_evidence([])
        assert evidence["plan_draft"] is None
        assert evidence["g4_decision"] is None
        assert evidence["g4_findings"] is None
        assert evidence["g4_attempts"] == []

    def test_non_evaluate_requests_ignored(self):
        """Only /plans/evaluate requests contribute to evidence."""
        pd = _fake_plan_draft()
        reqs = [
            {"method": "GET", "url": "/plans/state", "status": 200, "request": None, "json": {}},
            *(_fake_transport_evaluate(plan_draft=pd, decision="ALLOW")),
        ]
        evidence = extract_g4_evidence(reqs)
        assert len(evidence["g4_attempts"]) == 1


# ── CaseRun backward compatibility ──────────────────────────────────────────


class TestCaseRunBackwardCompat:
    """Existing code constructing CaseRun without evidence fields must work."""

    def test_minimal_construction_no_evidence(self):
        """Old-style CaseRun should have empty evidence defaults."""
        r = _make_minimal_run()
        assert r.utterances == []
        assert r.plan_draft is None
        assert r.g4_attempts == []
        assert r.usage_known is False
        assert r.usage is None

    def test_evidence_fields_are_optional(self):
        """CaseRun() with only old fields should not raise."""
        r = CaseRun(
            case_id="tc-01",
            category="transfer",
            variant="v1",
            repeat_index=0,
            expected_outcome="ALLOW",
            expected_risk=None,
            expected_params={},
            actual_outcome="ALLOW",
            actual_risk=None,
            actual_params={},
        )
        assert r.utterances == []
        assert r.plan_draft is None

    def test_old_wire_fields_still_in_jsonl(self):
        """tokens, latency_ms, finding_codes etc. still appear."""
        r = _make_minimal_run(tokens=1234, finding_codes=["X1"])
        # Check old fields still accessible
        assert r.tokens == 1234
        assert r.finding_codes == ["X1"]


# ── write_outputs with evidence ─────────────────────────────────────────────


class TestWriteOutputsWithEvidence:
    """write_outputs includes evidence fields in cases.jsonl, stripped of secrets."""

    def _write_and_read(self, tmp_path: Path, runs: list[CaseRun]) -> list[dict]:
        metrics = compute_metrics(runs)
        meta = {
            "llm_mode": "fake",
            "model": "deepseek-v4-pro",
            "l3": "real",
            "seed_version": "l1_seed_v1",
            "corpus_version": "v1",
            "sample_count": str(len(runs)),
        }
        out = tmp_path / "report"
        write_outputs(out, runs, metrics, meta)
        lines = (out / "cases.jsonl").read_text(encoding="utf-8").strip().split("\n")
        return [json.loads(line) for line in lines]

    def test_evidence_fields_written(self, tmp_path: Path):
        """Evidence fields appear in JSONL."""
        runs = [
            _make_minimal_run(
                case_id="ev-01",
                utterances=["冻结我的卡"],
                plan_draft=_fake_plan_draft(),
                g4_attempts=[
                    {
                        "status": 200,
                        "decision": "ALLOW",
                        "risk": None,
                        "error_code": None,
                    }
                ],
            ),
        ]
        objs = self._write_and_read(tmp_path, runs)
        assert len(objs) == 1
        o = objs[0]
        assert o["utterances"] == ["冻结我的卡"]
        assert o["plan_draft"]["steps"][0]["tool"] == "freeze_card"
        assert len(o["g4_attempts"]) == 1
        assert o["g4_attempts"][0]["decision"] == "ALLOW"

    def test_multi_turn_utterances_preserved(self, tmp_path: Path):
        """Multi-turn case: all turn texts preserved in order."""
        runs = [
            _make_minimal_run(
                case_id="multi",
                utterances=["查询余额", "冻结我的卡"],
            ),
        ]
        objs = self._write_and_read(tmp_path, runs)
        assert objs[0]["utterances"] == ["查询余额", "冻结我的卡"]

    def test_secrets_stripped_in_jsonl(self, tmp_path: Path):
        """Secret-bearing fields in plan_draft/g4_attempts must be [REDACTED]."""
        pd = _fake_plan_draft()
        # Inject a secret key
        pd["assistant_token"] = "Bearer real-secret-token"
        pd["steps"][0]["args"]["api_key"] = "should-not-leak"
        runs = [
            _make_minimal_run(
                case_id="sec-01",
                plan_draft=pd,
                g4_attempts=[
                    {
                        "status": 200,
                        "decision": "ALLOW",
                        "risk": None,
                        "error_code": None,
                        "authorization": "Bearer abc.def.ghi",
                    }
                ],
            ),
        ]
        objs = self._write_and_read(tmp_path, runs)
        o = objs[0]
        assert o["plan_draft"]["assistant_token"] == "[REDACTED]"
        assert o["plan_draft"]["steps"][0]["args"]["api_key"] == "[REDACTED]"
        assert o["g4_attempts"][0]["authorization"] == "[REDACTED]"

    def test_old_fields_still_present(self, tmp_path: Path):
        """cases.jsonl still has case_id, actual_outcome, tokens, etc."""
        runs = [_make_minimal_run()]
        objs = self._write_and_read(tmp_path, runs)
        o = objs[0]
        assert o["case_id"] == "tc-01"
        assert o["actual_outcome"] == "ALLOW"
        assert o["tokens"] == 0
        assert o["latency_ms"] == 100

    def test_usage_known_false_has_null_usage(self, tmp_path: Path):
        """When usage_known=False, usage is None in JSONL."""
        runs = [_make_minimal_run(usage_known=False, usage=None)]
        objs = self._write_and_read(tmp_path, runs)
        assert objs[0]["usage_known"] is False
        assert objs[0]["usage"] is None

    def test_usage_known_true_preserves_usage(self, tmp_path: Path):
        """When usage_known=True, usage dict is written."""
        runs = [
            _make_minimal_run(
                usage_known=True,
                usage={"input": 1000, "output": 200, "cache": 300, "total": 1500},
            )
        ]
        objs = self._write_and_read(tmp_path, runs)
        assert objs[0]["usage_known"] is True
        assert objs[0]["usage"] == {"input": 1000, "output": 200, "cache": 300, "total": 1500}

    def test_meta_keys_in_cases_jsonl(self, tmp_path: Path):
        """The cases.jsonl should NOT include meta — meta goes to metrics.json."""
        runs = [_make_minimal_run()]
        self._write_and_read(tmp_path, runs)
        # meta is only in metrics.json, not in cases.jsonl
        metrics_raw = (tmp_path / "report" / "metrics.json").read_text(encoding="utf-8")
        metrics = json.loads(metrics_raw)
        assert metrics["totals"]["runs"] == 1

    def test_existing_17_metric_tests_stable(self):
        """Old test data (_make_runs) still produces the same metrics."""
        from tests.evaluation.runner.test_metrics import _make_runs

        m = compute_metrics(_make_runs())
        assert m["totals"]["runs"] == 10
        assert m["executable_rate"] == pytest.approx(5 / 7, rel=1e-9)
        assert m["attack_block_rate"] == pytest.approx(2 / 3, rel=1e-9)
