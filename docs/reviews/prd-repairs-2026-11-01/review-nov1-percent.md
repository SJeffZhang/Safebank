# Nov 1 percentage verification — independent review

## Latest review — APPROVE

Exact follow-up `c4b217a3eb7ca3be56f565890e7b6a276e53524d` archived at `/private/tmp/sbg-nov1-repairs/review-percent-c4b217a`. Root explicitly authorized the narrow direction comparison guard. The original two P2s are closed: compatible Unicode signs retain their original span; Unicode whitespace is bounded to 32 scalars; ambiguous/unsupported sign prefixes yield an unverified token instead of an unsigned positive tail. Percentage grounding now requires negative/zero/positive direction agreement before applying the existing same-direction 0.1 tolerance. No money parser, fact-source widening, absolute-value matching, auth or provider behavior changed.

Independent reruns on this exact archive:

- Original private 45 probes, unchanged from the historical red: **45 passed** (0.50s), `review-percent-c4-original45.log`.
- Author's five affected files including the new Unicode/direction tests: **177 passed** (0.81s), `review-percent-c4-author177.log`.
- Ten new private boundaries: exact 32-character ASCII/NBSP gaps, 33-character rejection, duplicate/mixed/plus-minus signs, same-direction rounding and distinct zero: **10 passed** (0.51s), `review-percent-c4-boundaries.log`.

The original 4a463db archive, red logs and 45 probes remain preserved. Initial punctuation-only probe corrections are documented below. This is a pure/static approval of the percentage unit; no actual PG/provider/API/service or full-suite claim is added by the reviewer.

---

## Historical review and preserved red evidence

**Historical REQUEST_CHANGES** — exact target `4a463db1fde506d9c87007991669c4c094f75c02`, base `294fc9bb00c2424e18fe786c1849b2d07322a8da`. The reported `-91.46%` correction is reproduced and its ordinary cases pass. Two direction controls remain open. Read-only audit: no product/worktree mutation, PG/API/model/browser/Docker or service execution.

## Scope and exact evidence

Archive: `/private/tmp/sbg-nov1-repairs/review-percent-4a463db`. Diff is limited to the percentage regex/extraction, one new test file and acceptance note. No amount parsing, fact collection, repair budget or absolute-value allowance changed.

- Author's affected verifier/bill/query tests, independently run in the exact archive: **138 passed** (0.90s). Log: `review-percent-author.log`.
- The final 21 author cases copied into an exact old-base archive: **15 failed / 6 passed** (0.47s), log `review-percent-old-red.log`. This proves the original negative-sign and ASCII gap failures independently of the new implementation.
- Private probes: `/private/tmp/sbg-nov1-repairs/test_review_percent.py`. **18 failed / 27 passed** (0.61s), log `review-percent-independent-final.log`. These cover ASCII/fullwidth/mathematical and small-form signs, ordinary/tab/nonbreaking/narrow-nonbreaking gaps, signed basis-point facts, zero, neighboring amounts and small nonzero directions.
- An initial probe used exact punctuation equality and produced two false positives because `mask_long_numbers` already normalizes fullwidth punctuation. The corrected probe compares the existing normalized output. Those two are not findings. Initial test/log retained separately; all counts below use the corrected controls.

## P2-1: a Unicode gap or small-form minus still lets a negative token borrow positive facts

Location: `services/agent/app/llm/verify.py:26` and percentage conversion at `:201` (target archive lines).

The sign gap only accepts ASCII space/tab, and U+FE63 SMALL HYPHEN-MINUS is not recognized. Examples `-\u00a091.46%`, `−\u202f91.46%`, and `﹣91.46%` are matched from the unsigned numeric tail as **+91.46**. With `mom_change_bp=9146`, the full `OutputVerifier` accepts a visibly negative reply; its final Unicode normalization may even display the ordinary `- 91.46%` form. With a negative fact the correct negative reply instead falls back. The private extraction cases and three full-verifier controls reproduce both sides.

Required outcome: preserve a supported explicit sign through horizontal Unicode gaps, or fail closed on such malformed/unsupported signed tokens. Never rematch the unsigned tail and ground it against the opposite direction. Keep amount parsing and fact sources unchanged.

## P2-2: existing percentage tolerance crosses zero and accepts equal-magnitude opposite directions

Location: `services/agent/app/llm/verify.py:406`–`:412` (`_pct_in_facts`; existing code, not introduced by this patch).

A source `mom_change_bp=1` is +0.01%, but `环比-0.01%` survives the full verifier unchanged. The reverse case and +5/-5 bp examples also pass, because `abs(value - fact) <= 0.1` treats the opposite directions as close enough. The extractor now preserves the sign, but grounding erases it again near zero. This is a pre-existing unclosed control within the root-requested “same magnitude, wrong direction must not pass” boundary; it is not evidence of a new regression or of the original formal report's unique cause.

Required outcome: nonzero opposite signs must not share a rounding tolerance. Preserve the intended same-direction rounding tolerance and zero handling; do not add absolute-value matching. A narrow comparison guard needs root authorization if it expands the author's percentage-extraction-only implementation scope.

## Commands and final results

Run from the exact target archive with the existing worktree venv:

```sh
PYTHONDONTWRITEBYTECODE=1 /Users/jet/.codex/worktrees/safebank-demo-runner/WeBank/.venv/bin/python -m pytest -q tests/agent/test_percentage_signs.py tests/agent/test_verify.py tests/agent/test_bill_accuracy.py tests/agent/test_query_gather.py -p no:cacheprovider
PYTHONDONTWRITEBYTECODE=1 /Users/jet/.codex/worktrees/safebank-demo-runner/WeBank/.venv/bin/python -m pytest -q -c pyproject.toml /private/tmp/sbg-nov1-repairs/test_review_percent.py -p no:cacheprovider --tb=short
```

Logs live in `/private/tmp/sbg-nov1-repairs/`. No full-suite, PG or live-service result is claimed by this review. No financial authorization boundary was changed. Re-review the same original private probes against the next exact SHA, preserving these red archives/logs.
