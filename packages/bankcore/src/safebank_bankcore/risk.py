"""Pure-function risk assessment scoring. No IO, no database, no randomness.

PRD §5.5 / §8.2: fixed rq_v1 with six questions; a/b/c/d → 1–4 points each;
total 6–24 maps to R1–R5; validity = assessed_at + 12 calendar months,
leap-day → end of month.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime

from safebank_contracts.risk_questionnaire import (
    QUESTIONNAIRE,
    SCORE_RANGES,
    VALIDITY_MONTHS,
)

from .reserve import BUSINESS_TIMEZONE


@dataclass(frozen=True, slots=True)
class RiskAssessmentResult:
    """Output of a successful risk assessment (no IO)."""

    score: int
    level: int
    valid_until: date


def _add_calendar_months(d: date, months: int) -> date:
    """Add *months* calendar months to *d*; if the target day doesn't exist
    (e.g. 31 → 30, or Feb-29 in a non-leap year), return the last day of the
    target month (PRD §8.2: 闰日取月末)."""
    y, m = d.year, d.month + months
    # carry years
    while m > 12:
        y += 1
        m -= 12
    while m < 1:
        y -= 1
        m += 12
    max_day = monthrange(y, m)[1]
    return date(y, m, min(d.day, max_day))


def apply_risk_assessment(
    questionnaire_id: str,
    answers: tuple[tuple[str, str], ...],  # ((question_id, option_id), ...)
    assessed_at: datetime,
) -> RiskAssessmentResult:
    """Score six mandatory answers against a known questionnaire.

    Raises ValueError for unknown questionnaire, duplicate/missing question,
    invalid option, or caller-supplied score/level (none accepted).
    """
    if questionnaire_id not in QUESTIONNAIRE:
        raise ValueError(f"Unknown questionnaire: {questionnaire_id!r}")

    qmap = QUESTIONNAIRE[questionnaire_id]
    if len(answers) != 6:
        raise ValueError("Exactly 6 answers required")

    seen: set[str] = set()
    total = 0
    for qid, oid in answers:
        if qid not in qmap:
            raise ValueError(f"Unknown question_id: {qid!r}")
        if qid in seen:
            raise ValueError(f"Duplicate question_id: {qid!r}")
        seen.add(qid)
        q = qmap[qid]
        opt = next((o for o in q.options if o.id == oid), None)
        if opt is None:
            raise ValueError(
                f"Invalid option {oid!r} for question {qid!r}; "
                f"expected one of {tuple(o.id for o in q.options)}"
            )
        total += opt.score

    # map score to level
    level = 0
    for lv, (lo, hi) in SCORE_RANGES.items():
        if lo <= total <= hi:
            level = lv
            break
    if level == 0:
        raise ValueError(f"Score {total} out of range (expected 6–24)")

    valid_until = _add_calendar_months(assessed_at.date(), VALIDITY_MONTHS)
    return RiskAssessmentResult(score=total, level=level, valid_until=valid_until)


def risk_valid_until_date(value: str) -> date:
    """Business date (Asia/Shanghai) through which a stored assessment is valid.

    ``risk_valid_until`` is either an ISO date or an ISO datetime (the demo seed
    stores aware datetimes); a naive datetime is read as business time. Raises
    ``ValueError`` for anything else.
    """
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.date()
    return parsed.astimezone(BUSINESS_TIMEZONE).date()
