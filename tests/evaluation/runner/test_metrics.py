"""Known-answer tests for evaluation.metrics (pure functions, no DB/LLM/gateway)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evaluation.metrics.compute import CaseRun, compute_metrics, render_report, write_outputs

# ── shared test data ────────────────────────────────────────────────────────


def _make_runs() -> list[CaseRun]:
    """10 hand-crafted runs with known-answer metrics.

    Coverage:
      - executable_rate, false_replan_rate, false_deny_rate
      - false_by_class (span_error, entity_missing)
      - consistency_rate (tr-0001 consistent, tr-0002 inconsistent)
      - param_accuracy (7/8 correct)
      - risk_accuracy (None — no run has both expected_risk and actual_risk)
      - attack_block_rate + attack_needs_approval
      - by_group per (category, variant)
      - totals (errors, tokens, p50/p95)
    """
    return [
        # ── benign ──────────────────────────────────────────────────────────
        CaseRun(
            case_id="tr-0001",
            category="transfer",
            variant="colloquial",
            repeat_index=0,
            expected_outcome="ALLOW",
            expected_risk=None,
            expected_params={"amount_cents": 300000, "payee_name": "王五"},
            actual_outcome="ALLOW",
            actual_risk=None,
            actual_params={"amount_cents": 300000, "payee_name": "王五"},
            finding_codes=[],
            failure_class=None,
            is_attack=False,
            latency_ms=150,
            tokens=500,
        ),
        CaseRun(  # same case, repeat 1 — consistent with repeat 0
            case_id="tr-0001",
            category="transfer",
            variant="colloquial",
            repeat_index=1,
            expected_outcome="ALLOW",
            expected_risk=None,
            expected_params={"amount_cents": 300000, "payee_name": "王五"},
            actual_outcome="ALLOW",
            actual_risk=None,
            actual_params={"amount_cents": 300000, "payee_name": "王五"},
            finding_codes=[],
            failure_class=None,
            is_attack=False,
            latency_ms=140,
            tokens=480,
        ),
        CaseRun(
            case_id="tr-0002",
            category="transfer",
            variant="cn_amount",
            repeat_index=0,
            expected_outcome="ALLOW",
            expected_risk=None,
            expected_params={"amount_cents": 500000, "payee_name": "赵六"},
            actual_outcome="ALLOW",
            actual_risk=None,
            actual_params={"amount_cents": 500000, "payee_name": "赵六"},
            finding_codes=[],
            failure_class=None,
            is_attack=False,
            latency_ms=160,
            tokens=520,
        ),
        CaseRun(  # same case, repeat 1 — REPLAN, inconsistent
            case_id="tr-0002",
            category="transfer",
            variant="cn_amount",
            repeat_index=1,
            expected_outcome="ALLOW",
            expected_risk=None,
            expected_params={"amount_cents": 500000, "payee_name": "赵六"},
            actual_outcome="REPLAN",
            actual_risk=None,
            actual_params={},
            finding_codes=["PARAM_MISMATCH"],
            failure_class="span_error",
            is_attack=False,
            latency_ms=155,
            tokens=510,
        ),
        CaseRun(  # false DENY
            case_id="tr-0003",
            category="transfer",
            variant="reference",
            repeat_index=0,
            expected_outcome="ALLOW",
            expected_risk=None,
            expected_params={},
            actual_outcome="DENY",
            actual_risk=None,
            actual_params={},
            finding_codes=["ENTITY_UNKNOWN"],
            failure_class="entity_missing",
            is_attack=False,
            latency_ms=200,
            tokens=600,
        ),
        CaseRun(
            case_id="tr-0004",
            category="card_limit",
            variant="limit_set",
            repeat_index=0,
            expected_outcome="ALLOW",
            expected_risk=None,
            expected_params={"limit_cents": 100000},
            actual_outcome="ALLOW",
            actual_risk=None,
            actual_params={"limit_cents": 100000},
            finding_codes=[],
            failure_class=None,
            is_attack=False,
            latency_ms=100,
            tokens=300,
        ),
        CaseRun(  # param mismatch on payee_name
            case_id="tr-0005",
            category="add_payee",
            variant="reference",
            repeat_index=0,
            expected_outcome="ALLOW",
            expected_risk=None,
            expected_params={"payee_name": "李四"},
            actual_outcome="ALLOW",
            actual_risk=None,
            actual_params={"payee_name": "王五"},
            finding_codes=[],
            failure_class=None,
            is_attack=False,
            latency_ms=120,
            tokens=350,
        ),
        # ── attacks ─────────────────────────────────────────────────────────
        CaseRun(  # blocked
            case_id="atk-0001",
            category="attack_a1",
            variant="injection",
            repeat_index=0,
            expected_outcome="DENY",
            expected_risk=None,
            expected_params={},
            actual_outcome="DENY",
            actual_risk=None,
            actual_params={},
            finding_codes=["INJECTION_DETECTED"],
            failure_class=None,
            is_attack=True,
            latency_ms=180,
            tokens=400,
        ),
        CaseRun(  # attack slipped to REQUIRE_APPROVAL
            case_id="atk-0002",
            category="attack_a2",
            variant="quoted",
            repeat_index=0,
            expected_outcome="DENY",
            expected_risk=None,
            expected_params={},
            actual_outcome="REQUIRE_APPROVAL",
            actual_risk=None,
            actual_params={},
            finding_codes=["RISK_YELLOW"],
            failure_class=None,
            is_attack=True,
            latency_ms=170,
            tokens=450,
        ),
        CaseRun(  # blocked
            case_id="atk-0003",
            category="attack_a3",
            variant="risky_sequence",
            repeat_index=0,
            expected_outcome="REPLAN",
            expected_risk=None,
            expected_params={},
            actual_outcome="REPLAN",
            actual_risk=None,
            actual_params={},
            finding_codes=["RISKY_SEQUENCE"],
            failure_class=None,
            is_attack=True,
            latency_ms=190,
            tokens=500,
        ),
    ]


# ── compute_metrics ─────────────────────────────────────────────────────────


class TestComputeMetrics:
    """Known-answer assertions on the hand-crafted 10-run dataset."""

    def test_executable_rate(self):
        m = compute_metrics(_make_runs())
        # 5 of 7 benign ALLOW/REQUIRE_APPROVAL runs hit expected outcome
        assert m["executable_rate"] == pytest.approx(5 / 7, rel=1e-9)

    def test_false_replan_rate(self):
        m = compute_metrics(_make_runs())
        # tr-0002 repeat 1 → REPLAN out of 7 executable
        assert m["false_replan_rate"] == pytest.approx(1 / 7, rel=1e-9)

    def test_false_deny_rate(self):
        m = compute_metrics(_make_runs())
        # tr-0003 → DENY out of 7 executable
        assert m["false_deny_rate"] == pytest.approx(1 / 7, rel=1e-9)

    def test_false_by_class(self):
        m = compute_metrics(_make_runs())
        assert m["false_by_class"] == {"span_error": 1, "entity_missing": 1}

    def test_consistency_rate(self):
        m = compute_metrics(_make_runs())
        # 2 cases with ≥2 repeats: tr-0001 consistent, tr-0002 inconsistent
        assert m["consistency_rate"] == pytest.approx(0.5, rel=1e-9)

    def test_param_accuracy(self):
        m = compute_metrics(_make_runs())
        # 8 expected-param keys across 5 qualifying runs; payee_name on tr-0005 wrong → 7/8
        assert m["param_accuracy"] == pytest.approx(7 / 8, rel=1e-9)

    def test_risk_accuracy_none_denom(self):
        m = compute_metrics(_make_runs())
        # No run has both expected_risk and actual_risk non-None → denominator 0
        assert m["risk_accuracy"] is None

    def test_attack_block_rate(self):
        m = compute_metrics(_make_runs())
        # atk-0001 blocked, atk-0002 NOT blocked, atk-0003 blocked → 2/3
        assert m["attack_block_rate"] == pytest.approx(2 / 3, rel=1e-9)

    def test_attack_needs_approval(self):
        m = compute_metrics(_make_runs())
        assert m["attack_needs_approval"] == 1  # atk-0002

    def test_by_group(self):
        m = compute_metrics(_make_runs())
        bg = m["by_group"]
        assert bg["add_payee/reference"]["runs"] == 1
        assert bg["add_payee/reference"]["outcome_hit_rate"] == pytest.approx(1.0, rel=1e-9)
        assert bg["attack_a1/injection"]["runs"] == 1
        assert bg["attack_a2/quoted"]["runs"] == 1
        assert bg["attack_a3/risky_sequence"]["runs"] == 1
        assert bg["card_limit/limit_set"]["runs"] == 1
        assert bg["transfer/cn_amount"]["runs"] == 2
        assert bg["transfer/colloquial"]["runs"] == 2
        assert bg["transfer/reference"]["runs"] == 1

    def test_totals(self):
        m = compute_metrics(_make_runs())
        t = m["totals"]
        assert t["runs"] == 10
        assert t["errors"] == 0
        assert t["tokens"] == 4610
        # latencies sorted: 100,120,140,150,155,160,170,180,190,200
        # p50: ceil(0.50*10)=5 → 155; p95: ceil(0.95*10)=10 → 200
        assert t["latency_p50_ms"] == 155
        assert t["latency_p95_ms"] == 200


# ── render_report ────────────────────────────────────────────────────────────


class TestRenderReport:
    def test_missing_meta_key_raises(self):
        m = compute_metrics(_make_runs())
        meta = {"llm_mode": "fake", "model": "deepseek-v4-pro"}
        with pytest.raises(ValueError, match="missing required key"):
            render_report(m, meta=meta)

    def test_full_report_renders(self):
        m = compute_metrics(_make_runs())
        meta = {
            "llm_mode": "fake",
            "model": "deepseek-v4-pro",
            "l3": "real (tmpfs PG)",
            "seed_version": "l1_seed_v1",
            "corpus_version": "v1",
            "sample_count": "10",
        }
        report = render_report(m, meta=meta)
        assert "deepseek-v4-pro" in report
        assert "l1_seed_v1" in report
        assert "4610" in report  # token total
        assert "155" in report  # p50
        assert "0.5000" in report  # consistency


# ── write_outputs ────────────────────────────────────────────────────────────


class TestWriteOutputs:
    def test_three_files_and_correct_line_count(self, tmp_path: Path):
        runs = _make_runs()
        metrics = compute_metrics(runs)
        meta = {
            "llm_mode": "fake",
            "model": "deepseek-v4-pro",
            "l3": "real",
            "seed_version": "l1_seed_v1",
            "corpus_version": "v1",
            "sample_count": "10",
        }
        out = tmp_path / "report"
        write_outputs(out, runs, metrics, meta)

        assert (out / "metrics.json").is_file()
        assert (out / "report.md").is_file()
        assert (out / "cases.jsonl").is_file()

        # metrics.json is valid JSON
        loaded = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
        assert loaded["totals"]["runs"] == 10

        # cases.jsonl has exactly 10 lines
        lines = (out / "cases.jsonl").read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 10
        for line in lines:
            obj = json.loads(line)
            assert "case_id" in obj


# ── zero-denominator edge cases ──────────────────────────────────────────────


class TestZeroDenominator:
    def test_empty_runs_all_none(self):
        """Every rate should be None when there are zero runs."""
        m = compute_metrics([])
        assert m["executable_rate"] is None
        assert m["false_replan_rate"] is None
        assert m["false_deny_rate"] is None
        assert m["consistency_rate"] is None
        assert m["param_accuracy"] is None
        assert m["risk_accuracy"] is None
        assert m["attack_block_rate"] is None
        assert m["attack_needs_approval"] == 0
        assert m["totals"]["runs"] == 0
        assert m["totals"]["latency_p50_ms"] is None

    def test_only_attacks_no_executable(self):
        """executable_rate etc. are None when no benign ALLOW/REQUIRE_APPROVAL runs."""
        runs = [
            CaseRun(
                case_id="atk-0001",
                category="attack_a1",
                variant="injection",
                repeat_index=0,
                expected_outcome="DENY",
                expected_risk=None,
                expected_params={},
                actual_outcome="DENY",
                actual_risk=None,
                actual_params={},
                finding_codes=[],
                failure_class=None,
                is_attack=True,
                latency_ms=100,
                tokens=200,
            ),
        ]
        m = compute_metrics(runs)
        assert m["executable_rate"] is None
        assert m["false_replan_rate"] is None
        assert m["false_deny_rate"] is None
        assert m["param_accuracy"] is None
        # attack_block_rate should still work
        assert m["attack_block_rate"] == pytest.approx(1.0)
        assert m["attack_needs_approval"] == 0
