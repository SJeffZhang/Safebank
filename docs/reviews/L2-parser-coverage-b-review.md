# L2 Parser Coverage-B Review

Scope: `git diff feat/l2-parser-coverage..HEAD` (read-only, independent review).
Commits under review: `ba26be7` (red tests), `017e3ed` (implementation),
`0376d5f` (ruff format), `fef29ee` (progress doc).

## Verdict

**APPROVE** — no P0/P1. The monthly/weekly anchor and AA-split scopes are
parsed conservatively; every safety property in the review intent holds. Three
non-blocking P2 findings below (all fail-closed / fail-safe; none moves money
or widens authorization).

## Findings

### P0

None.

### P1

None.

### P2

1. **`_first_run_at` gives up instead of scanning forward for anchors 29/30/31.**
   `parser.py:524-535` only looks at the *immediately next* month. When that
   month lacks the anchor day it returns `None` → the whole request becomes
   INTENT_UNCLEAR, even though a later month has the day and the next
   occurrence is unambiguous. Verified: anchor 31 from `2026-04-15` (April has
   30 days) → `None`; anchor 31 from `2026-10-31` (today is the 31st, Nov has
   30 days) → `None`; anchor 29 from `2026-02-10` (Feb 2026 has 28 days) →
   `None`. So "每月31号给赵六转200元" is rejected roughly whenever the next
   month is short. Fail-closed (no wrong schedule, no money moved), so not
   merge-blocking, but the docstring's "first occurrence strictly after
   sim_now" is not honored. Recommend scanning forward to the first month whose
   `monthrange ≥ anchor_day`.

2. **"Today is the anchor" ignores time-of-day.** `_first_run_at` compares only
   `sim_now.day` (monthly) / `sim_now.weekday()` (weekly), so an anchor uttered
   *before* the 08:00 first-run time still advances a full period. Verified:
   anchor 20 from `2026-10-20T06:00+08:00` → `2026-11-20T08:00` (today 08:00 is
   strictly after sim_now but is skipped); weekly Monday from
   `2026-10-19T06:00+08:00` → `2026-10-26T08:00`. Fail-safe (delays the first
   run, never over-executes); informational.

3. **Progress doc overstates the test count.** `docs/progress/L2-parser-coverage-b.md`
   reports "make test-w1: 1782 passed", but the review-intent command
   `.venv/bin/pytest tests/gateway/w1 -q` yields **1623 passed, 159 skipped**
   (1782 total). Documentation only; not a code defect.

## Checks

1. **Monthly anchor → SCOPED schedule_transfer** — PASS.
   "每月20号给赵六转200元" yields `SCHEDULE_CREATE`, `fp_zl` 20000,
   `period=MONTHLY`, `first_run_at=2026-10-20T08:00:00+08:00`
   (`test_monthly_schedule_gei_zhao_liu`). `_clause` (parser.py:431,439-445)
   sets `tool="schedule_transfer"` + `{period, anchor_day, continuous}` and no
   longer marks it `future`, so `parse()` computes `first_run_at` instead of
   returning FORM_REQUIRED.

2. **Weekly anchor → SCOPED schedule_transfer** — PASS.
   "每周一给妈妈转100元" → `SCHEDULE_CREATE`, `fp_mom` 10000,
   `period=WEEKLY`, `first_run_at=2026-10-19T08:00:00+08:00`. `_WEEKDAYS`
   (parser.py:509) maps 一→0 … 日/天→6 matching Python `weekday()`
   (Monday=0). `_first_run_at` weekly branch (`days_ahead = (target−now)%7 or 7`)
   is correct for non-today and today-anchor cases.

3. **Month-end anchors** — PARTIAL (see P2-1). Anchor 31 from a 31-day month
   where the next month has 31 works (`2026-10-15` → `2026-10-31T08:00`); the
   forward-scan case fails closed. "每月31号给赵六转200元" at NOW
   (`2026-10-15`) → SCOPED with `first_run_at=2026-10-31T08:00`.

4. **Today-is-the-anchor** — PASS with caveat (P2-2). `sim_now.day == anchor_day`
   (or same weekday) always advances one full period; fail-safe, never
   schedules in the past.

5. **Schedule without a determinable first run still FORM_REQUIRED** — PASS.
   "明天给赵六转200元" → `INTENT_UNCLEAR` with
   `fields["missing_fields"] == ["first_run_at"]`
   (`test_bare_future_schedule_still_requires_form`). The bare-`future` branch
   (parser.py:450) still sets `future=True`; only concrete monthly/weekly
   anchors bypass it. "每周给妈妈转100元" (weekday omitted) does not match the
   weekly regex and falls through to a non-payee bare target → INTENT_UNCLEAR.

6. **Unknown / ambiguous names clarify (fail-closed, never guessed)** — PASS.
   Split participants must resolve to *exactly one exact* name/alias match
   (parser.py:658-665); `_payees` near-name (phonetic) matches are deliberately
   not accepted. "昨晚聚餐400元，我和小王AA" and "…我和王AA" → not SCOPED
   (parametrized in `test_split_attack_no_scope`). Duplicate participant
   ("我和小林、小林AA") → INTENT_UNCLEAR via the uniqueness check
   (parser.py:667).

7. **Amounts only from text, never invented** — PASS. Split total comes from the
   single bare-transfer clause's `amount_cents` (text-derived, parser.py:645,
   677); schedule amounts come from `_transfer_clause`. A total whose target
   resolves to an account or payee ("王五400元，我和小林、小周AA") is rejected
   (parser.py:654-655), so an amount can never be paired with a payee named in
   the total clause.

8. **Negation / quotes / questions produce no scope** — PASS. Verified by probe
   and the 3 parametrized attack cases: "不要AA" (no total → rejected),
   "昨晚聚餐400元吗，我和小林AA" (`吗` breaks `fullmatch` → None),
   "他说“我和小林、小周、小陈AA”" (QUOTES strips to "他说" → no command).
   "不要给赵六转200元" → bare target "不要给赵六转" matches no payee →
   INTENT_UNCLEAR.

9. **No existing test loosened** — PASS. `git diff --stat` shows the only test
   change is the *addition* of `tests/gateway/w1/test_parser_coverage_b.py`;
   no existing test file is modified. (The diff also lists
   `docs/reviews/L2-parser-coverage-a-review.md` as deleted; this is a
   branch-point artifact — HEAD is rooted at `013ad2b`, before `3533f0a` added
   that file to the base — and `git log --diff-filter=D` shows no commit in the
   range deletes it, so a merge will not remove it.)

10. **Patterns do not over-match plain transfers** — PASS. "给赵六转200元" still
    yields `TRANSFER_EXTERNAL` (probe). "昨晚聚餐400元" alone (bare transfer,
    no split clause) → INTENT_UNCLEAR (bare-transfer guard, parser.py:686-689).
    A non-bare transfer mixed with an AA clause ("给小林转100元，我和小林AA")
    is rejected because the total must be a *bare* transfer (parser.py:645).

11. **Test run** — PASS. `.venv/bin/pytest tests/gateway/w1 -q` →
    `1623 passed, 159 skipped in 3.88s` (clean, no failures). New file in
    isolation exercises the 3 positive + 6 attack cases from the intent.
