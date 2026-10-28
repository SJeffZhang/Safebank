"""Tests for risk assessment pure functions (no PG required)."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from safebank_bankcore.risk import _add_calendar_months, apply_risk_assessment
from safebank_contracts.risk_questionnaire import QUESTION_IDS, QUESTIONNAIRE, SCORE_RANGES

CST = ZoneInfo("Asia/Shanghai")

# ── helpers ─────────────────────────────────────────────────────────────


def _ans(*pairs: tuple[str, str]) -> tuple[tuple[str, str], ...]:
    """Shortcut: _ans(('q01','a'), ('q02','b'), ...)"""
    return pairs


def _answers_for_score(target: int) -> tuple[tuple[str, str], ...]:
    """Build a set of answers whose scores sum to *target*.

    Start from six 'a' (6 pts), then bump earlier questions toward 'd'
    (adding at most 3 pts each) until the sum reaches *target*.
    """
    assert 6 <= target <= 24
    scores: list[int] = [1] * 6
    remaining = target - 6
    for i in range(6):
        add = min(3, remaining)
        scores[i] += add
        remaining -= add
        if remaining == 0:
            break
    opt_map = {1: "a", 2: "b", 3: "c", 4: "d"}
    return tuple((qid, opt_map[s]) for qid, s in zip(QUESTION_IDS, scores, strict=False))


def _now() -> datetime:
    return datetime(2026, 10, 15, 14, 30, 0, tzinfo=CST)


# ── _add_calendar_months ───────────────────────────────────────────────


class TestAddCalendarMonths:
    def test_normal_month(self):
        assert _add_calendar_months(date(2026, 10, 15), 12) == date(2027, 10, 15)

    def test_year_boundary(self):
        assert _add_calendar_months(date(2026, 1, 10), 12) == date(2027, 1, 10)

    def test_31_to_30(self):
        # Oct 31 → Nov 30 (Nov has 30 days)
        assert _add_calendar_months(date(2026, 10, 31), 1) == date(2026, 11, 30)

    def test_jan31_to_feb28(self):
        assert _add_calendar_months(date(2026, 1, 31), 1) == date(2026, 2, 28)

    def test_leap_day_to_non_leap(self):
        # 2024-02-29 + 12m → 2025-02-28
        assert _add_calendar_months(date(2024, 2, 29), 12) == date(2025, 2, 28)

    def test_leap_day_to_leap(self):
        # 2024-02-29 + 48m → 2028-02-29 (2028 is leap)
        assert _add_calendar_months(date(2024, 2, 29), 48) == date(2028, 2, 29)

    def test_dec31_to_dec31(self):
        assert _add_calendar_months(date(2026, 12, 31), 12) == date(2027, 12, 31)


# ── apply_risk_assessment ───────────────────────────────────────────────


class TestApplyRiskAssessment:
    # ── score → level boundaries ───────────────────────────────────
    @pytest.mark.parametrize(
        "score,expected_level",
        [
            (6, 1),
            (9, 1),
            (10, 2),
            (13, 2),
            (14, 3),
            (17, 3),
            (18, 4),
            (21, 4),
            (22, 5),
            (24, 5),
        ],
    )
    def test_score_level_boundaries(self, score, expected_level):
        answers = _answers_for_score(score)
        result = apply_risk_assessment("rq_v1", answers, _now())
        assert result.score == score
        assert result.level == expected_level

    # ── valid_until ────────────────────────────────────────────────
    def test_valid_until_same_day_next_year(self):
        result = apply_risk_assessment(
            "rq_v1", _answers_for_score(10), datetime(2026, 3, 15, 9, 0, tzinfo=CST)
        )
        assert result.valid_until == date(2027, 3, 15)

    def test_leap_day_assessment(self):
        """Assessed on Feb 29 → valid_until = Feb 28 of next (non-leap) year."""
        result = apply_risk_assessment(
            "rq_v1", _answers_for_score(12), datetime(2024, 2, 29, 10, 0, tzinfo=CST)
        )
        assert result.valid_until == date(2025, 2, 28)

    def test_is_valid_true(self):
        result = apply_risk_assessment("rq_v1", _answers_for_score(15), _now())
        assert result.valid_until > _now().date()

    def test_exactly_expired(self):
        """Assessed on 2025-10-15 → valid_until = 2026-10-15.
        At 2026-10-16 it's expired."""
        result = apply_risk_assessment(
            "rq_v1",
            _answers_for_score(15),
            datetime(2025, 10, 15, 12, 0, tzinfo=CST),
        )
        assert result.valid_until == date(2026, 10, 15)

    # ── validation errors ──────────────────────────────────────────
    def test_unknown_questionnaire(self):
        with pytest.raises(ValueError, match="Unknown questionnaire"):
            apply_risk_assessment("rq_v2", _answers_for_score(10), _now())

    def test_unknown_question_id(self):
        bad = _ans(
            ("q99", "a"),
            ("q02", "b"),
            ("q03", "c"),
            ("q04", "d"),
            ("q05", "a"),
            ("q06", "b"),
        )
        with pytest.raises(ValueError, match="Unknown question_id"):
            apply_risk_assessment("rq_v1", bad, _now())

    def test_invalid_option_id(self):
        bad = _ans(
            ("q01", "x"),
            ("q02", "b"),
            ("q03", "c"),
            ("q04", "d"),
            ("q05", "a"),
            ("q06", "b"),
        )
        with pytest.raises(ValueError, match="Invalid option"):
            apply_risk_assessment("rq_v1", bad, _now())

    def test_duplicate_question_id(self):
        bad = _ans(
            ("q01", "a"),
            ("q01", "b"),
            ("q03", "c"),
            ("q04", "d"),
            ("q05", "a"),
            ("q06", "b"),
        )
        with pytest.raises(ValueError, match="Duplicate question_id"):
            apply_risk_assessment("rq_v1", bad, _now())

    def test_too_few_answers(self):
        with pytest.raises(ValueError, match="Exactly 6"):
            apply_risk_assessment(
                "rq_v1",
                _ans(("q01", "a"), ("q02", "b"), ("q03", "c"), ("q04", "d"), ("q05", "a")),
                _now(),
            )

    def test_too_many_answers(self):
        with pytest.raises(ValueError, match="Exactly 6"):
            apply_risk_assessment(
                "rq_v1",
                _ans(
                    ("q01", "a"),
                    ("q02", "b"),
                    ("q03", "c"),
                    ("q04", "d"),
                    ("q05", "a"),
                    ("q06", "b"),
                    ("q01", "b"),
                ),
                _now(),
            )

    # ── question text integrity ────────────────────────────────────
    def test_all_six_questions_have_four_options(self):
        qmap = QUESTIONNAIRE["rq_v1"]
        assert len(qmap) == 6
        for qid in QUESTION_IDS:
            q = qmap[qid]
            assert len(q.options) == 4
            ids = [o.id for o in q.options]
            assert ids == ["a", "b", "c", "d"]
            scores = [o.score for o in q.options]
            assert scores == [1, 2, 3, 4]

    def test_score_ranges_cover_6_to_24(self):
        covered: set[int] = set()
        for lo, hi in SCORE_RANGES.values():
            for v in range(lo, hi + 1):
                covered.add(v)
        assert covered == set(range(6, 25))
