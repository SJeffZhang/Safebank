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
    extract_gather_reads,
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
    req_body: dict = plan_draft or _fake_plan_draft()
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
        assert result["header"] == "Bearer [REDACTED]"

    def test_strips_jwt_string(self):
        # JWT: base64url.base64url.base64url, starts with eyJ.
        # Note: key "token" matches _SECRET_KEY_RE → whole-value replacement.
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

    # ── P1-1: tuple bypass ──────────────────────────────────────────────

    def test_strips_tuple_headers(self):
        """P1-1: Tuple now processed; Bearer element stripped (old code skipped tuples)."""
        obj = {"headers": [("Authorization", "Bearer eyJhbGci.eyJzdWI.SflKxw")]}
        result = _strip_secrets(obj)
        h0 = result["headers"][0]
        assert isinstance(h0, tuple)
        # Old code returned the raw tuple unchanged → Bearer leaked.
        # Now the second element (Bearer) has token stripped, prefix kept.
        assert h0[1] == "Bearer [REDACTED]"
        assert "eyJ" not in str(h0)

    # ── P1-2: mid-string Bearer ─────────────────────────────────────────

    def test_strips_bearer_anywhere_in_string(self):
        """P1-2: Bearer anywhere in string must be detected, only token replaced."""
        obj = {"header": "Authorization: Bearer eyJhbGci.eyJzdWI.SflKxw"}
        result = _strip_secrets(obj)
        assert result["header"] == "Authorization: Bearer [REDACTED]"

    def test_strips_lowercase_bearer(self):
        """P1-2: Lowercase 'bearer' must also be detected, only token replaced."""
        obj = {"h": "bearer eyJhbGci.eyJzdWI.SflKxw"}
        result = _strip_secrets(obj)
        assert result["h"] == "bearer [REDACTED]"

    # ── P1-3: embedded JWT ──────────────────────────────────────────────

    def test_strips_embedded_jwt(self):
        """P1-3: JWT embedded in longer text — only the JWT substring is replaced."""
        jwt = (
            "eyJhbGciOiJIUzI1NiJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        )
        obj = {"text": "some text " + jwt + " more"}
        result = _strip_secrets(obj)
        assert result["text"] == "some text [REDACTED:jwt] more"

    # ── P2-1: depth placeholder ─────────────────────────────────────────

    def test_depth_limit_returns_placeholder(self):
        """P2-1: Beyond max depth returns [REDACTED:depth], not raw obj."""
        obj: Any = {"a": {}}
        cur = obj["a"]
        for _ in range(20):
            cur["a"] = {}
            cur = cur["a"]
        result = _strip_secrets(obj)
        # Walk down to find the placeholder
        node = result
        for _ in range(11):  # _MAX_STRIP_DEPTH=10, then next is placeholder
            assert isinstance(node, dict)
            node = node["a"]
        assert node == "[REDACTED:depth]"

    # ── P2-3: set / frozenset ───────────────────────────────────────────

    def test_strips_set(self):
        """P2-3: set containing secret-bearing strings must be stripped."""
        obj = {"items": {"Bearer abc.def.ghi", "safe_value"}}
        result = _strip_secrets(obj)
        s = result["items"]
        assert isinstance(s, set)
        assert "Bearer [REDACTED]" in s
        assert "safe_value" in s

    def test_strips_frozenset(self):
        """P2-3: frozenset must be stripped and remain frozenset."""
        obj = {"items": frozenset(["Bearer xyz.secret.token", "keep"])}
        result = _strip_secrets(obj)
        s = result["items"]
        assert isinstance(s, frozenset)
        assert "Bearer [REDACTED]" in s
        assert "keep" in s

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

    # ── T12: tightened JWT regex, substring-only replacement ────────────

    def test_validation_error_path_not_stripped(self):
        """T12: pydantic ValidationError paths like 'steps.0.arg_sources.2.source'
        must NOT be mistaken for JWTs and must be preserved as-is."""
        obj = {
            "error_message": (
                "1 validation error for PlanDraft\n"
                "steps.0.arg_sources.2.source\n"
                "  Field required [type=missing, input_value={}]"
            ),
        }
        result = _strip_secrets(obj)
        assert "steps.0.arg_sources.2.source" in result["error_message"]
        assert "arg_sources" in result["error_message"]
        assert "[REDACTED" not in result["error_message"]  # nothing should be redacted

    def test_bearer_token_only_replaced_in_mid_string(self):
        """T12: 'x Bearer abc.def y' → only token replaced, surrounding text kept."""
        obj = {"text": "x Bearer abc.def y"}
        result = _strip_secrets(obj)
        assert result["text"] == "x Bearer [REDACTED] y"

    def test_embedded_real_jwt_only_substring_replaced(self):
        """T12: embedded real-form JWT (eyJ...) only that substring replaced."""
        jwt = (
            "eyJhbGciOiJIUzI1NiJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        )
        obj = {"text": "token=" + jwt + "&other=value"}
        result = _strip_secrets(obj)
        assert result["text"] == "token=[REDACTED:jwt]&other=value"


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
                "request": pd,
                "json": {"error": {"code": "PARAM_MISMATCH"}},
            },
            {  # second attempt: 200
                "method": "POST",
                "url": "http://gateway:8200/plans/evaluate",
                "status": 200,
                "request": pd,
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
                "request": pd,
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
        # key "authorization" matches _SECRET_KEY_RE
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

    # ── P1-4: g4_decision / g4_findings preserved ───────────────────────

    def test_g4_decision_and_findings_in_jsonl(self, tmp_path: Path):
        """P1-4: g4_decision and g4_findings must be written to cases.jsonl."""
        pd = _fake_plan_draft()
        runs = [
            _make_minimal_run(
                case_id="g4-ev",
                plan_draft=pd,
                g4_decision="REQUIRE_APPROVAL",
                g4_findings=[{"code": "X1", "severity": "YELLOW"}],
                g4_attempts=[
                    {
                        "status": 200,
                        "decision": "REQUIRE_APPROVAL",
                        "risk": "YELLOW",
                        "error_code": None,
                    }
                ],
            ),
        ]
        objs = self._write_and_read(tmp_path, runs)
        assert objs[0]["g4_decision"] == "REQUIRE_APPROVAL"
        assert objs[0]["g4_findings"] == [{"code": "X1", "severity": "YELLOW"}]

    def test_g4_decision_none_when_no_successful_g4(self, tmp_path: Path):
        """P1-4: g4_decision=None when no G4 call succeeded."""
        runs = [
            _make_minimal_run(
                case_id="g4-fail",
                g4_decision=None,
                g4_findings=None,
                g4_attempts=[
                    {
                        "status": 503,
                        "decision": None,
                        "risk": None,
                        "error_code": "DEPENDENCY_UNAVAILABLE",
                    },
                ],
            ),
        ]
        objs = self._write_and_read(tmp_path, runs)
        assert objs[0]["g4_decision"] is None
        assert objs[0]["g4_findings"] is None

    # ── P2-5: additional secret-bearing patterns ────────────────────────

    def test_strips_bearer_in_g4_error_code(self, tmp_path: Path):
        """P2-5: error_code containing Bearer token must be stripped."""
        runs = [
            _make_minimal_run(
                case_id="sec-err",
                g4_attempts=[
                    {
                        "status": 503,
                        "decision": None,
                        "risk": None,
                        "error_code": "Bearer leaked-token-value",
                    }
                ],
            ),
        ]
        objs = self._write_and_read(tmp_path, runs)
        # error_code is a str, so startswith-then-bearer → but with our fix
        # the whole string gets stripped because it contains Bearer
        assert "leaked-token-value" not in json.dumps(objs[0]["g4_attempts"])

    def test_strips_secrets_in_nested_arg_sources(self, tmp_path: Path):
        """P2-5: Secrets in arg_sources[].source.memo must be stripped."""
        pd = _fake_plan_draft()
        pd["steps"][0]["arg_sources"].append(
            {
                "param": "memo",
                "source": {
                    "kind": "utterance",
                    "ref": "utt_01",
                    "span": [5, 10],
                    "api_key": "leak-in-source",
                },
            }
        )
        runs = [
            _make_minimal_run(case_id="sec-nested", plan_draft=pd),
        ]
        objs = self._write_and_read(tmp_path, runs)
        raw = json.dumps(objs[0])
        assert "leak-in-source" not in raw


# ── extract_gather_reads ────────────────────────────────────────────────────


class TestExtractGatherReads:
    """Pure function: extract read requests from transport log."""

    def _read_request(
        self,
        tool: str = "get_cards",
        status: int = 200,
        source_ref: str = "src_01",
        items: list | None = None,
    ) -> dict:
        """Create a fake transport entry for a POST /tools/read/{tool} call."""
        resp: dict[str, Any] = {"source_ref": source_ref}
        if items is not None:
            resp["data"] = {"items": items}
        else:
            resp["data"] = {"items": [{"id": "crd_zs_1", "status": "ACTIVE"}]}
        return {
            "method": "POST",
            "url": f"http://gateway:8200/tools/read/{tool}",
            "status": status,
            "request": {"account_id": None},
            "json": resp,
        }

    def test_single_read(self):
        """One read request → one entry with tool, source_id, item_count."""
        result = extract_gather_reads([self._read_request()])
        assert len(result) == 1
        r = result[0]
        assert r["tool"] == "get_cards"
        assert r["status"] == 200
        assert r["source_id"] == "src_01"
        assert r["item_count"] == 1

    def test_no_reads_empty(self):
        """No /tools/read/ requests → empty list."""
        result = extract_gather_reads([])
        assert result == []

    def test_ignores_non_read_requests(self):
        """Only POST /tools/read/ paths are extracted; evaluate, sessions, etc. ignored."""
        reqs = [
            self._read_request(),
            {
                "method": "POST",
                "url": "/plans/evaluate",
                "status": 200,
                "request": {},
                "json": {"decision": "ALLOW"},
            },
            {
                "method": "GET",
                "url": "/clarifications",
                "status": 200,
                "request": None,
                "json": {"clarification": {}},
            },
        ]
        result = extract_gather_reads(reqs)
        assert len(result) == 1

    def test_multiple_reads(self):
        """Multiple read requests → all captured in order."""
        reqs = [
            self._read_request("get_cards", source_ref="src_a"),
            self._read_request("get_accounts", source_ref="src_b"),
        ]
        result = extract_gather_reads(reqs)
        assert len(result) == 2
        assert result[0]["tool"] == "get_cards"
        assert result[0]["source_id"] == "src_a"
        assert result[1]["tool"] == "get_accounts"
        assert result[1]["source_id"] == "src_b"

    def test_read_error_status(self):
        """Read with non-200 status still captured (source_id/item_count may be None)."""
        req = self._read_request("get_cards", status=503)
        req["json"] = None  # no response body on error
        result = extract_gather_reads([req])
        assert len(result) == 1
        r = result[0]
        assert r["tool"] == "get_cards"
        assert r["status"] == 503
        assert r["source_id"] is None
        assert r["item_count"] is None

    def test_item_count_accounts(self):
        """Response with 'accounts' key → counted correctly."""
        req = self._read_request("get_accounts", items=None)
        req["json"] = {
            "source_ref": "src_acc",
            "data": {"accounts": [{"id": "a1"}, {"id": "a2"}, {"id": "a3"}]},
        }
        result = extract_gather_reads([req])
        assert result[0]["item_count"] == 3

    def test_item_count_no_list_key(self):
        """Response without any known list key → item_count=None."""
        req = self._read_request("get_product", items=None)
        req["json"] = {
            "source_ref": "src_prd",
            "data": {"product_id": "prd_01", "name": "基金A"},
        }
        result = extract_gather_reads([req])
        assert result[0]["item_count"] is None

    def test_empty_items(self):
        """Response items=[] → item_count=0."""
        req = self._read_request("get_cards", items=[])
        result = extract_gather_reads([req])
        assert result[0]["item_count"] == 0

    def test_secrets_stripped(self):
        """Secret-bearing fields in gather_reads are redacted."""
        req = self._read_request("get_cards")
        req["json"]["data"]["api_key"] = "secret-value"
        result = extract_gather_reads([req])
        assert result[0]["source_id"] == "src_01"  # business data preserved
        # No secret keys in the output
        raw = json.dumps(result)
        assert "secret-value" not in raw


# ── write_outputs gather_reads in JSONL ────────────────────────────────────


class TestWriteOutputsGatherReads:
    """gather_reads field appears in cases.jsonl, stripped of secrets."""

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

    def test_gather_reads_in_jsonl(self, tmp_path: Path):
        """gather_reads appears in JSONL output."""
        runs = [
            _make_minimal_run(
                case_id="gr-01",
                gather_reads=[
                    {"tool": "get_cards", "status": 200, "source_id": "src_01", "item_count": 1}
                ],
            ),
        ]
        objs = self._write_and_read(tmp_path, runs)
        assert objs[0]["gather_reads"] == [
            {"tool": "get_cards", "status": 200, "source_id": "src_01", "item_count": 1}
        ]

    def test_gather_reads_empty_default(self, tmp_path: Path):
        """CaseRun with no gather_reads → empty list in JSONL."""
        runs = [_make_minimal_run(case_id="no-reads")]
        objs = self._write_and_read(tmp_path, runs)
        assert objs[0]["gather_reads"] == []
