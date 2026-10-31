# September bill-analysis proof

Date: 2026-10-31. Base: `ccf5d3330b44f4d5049fbbdb5bf5d78ac352ba1b`.

This task checks one accurate September report path before changing code. It does not claim six-scenario freeze, live rerun, SQL execution, or final CI.

## Input, output and boundaries

- Input: `cus_zs`, `给我出一份9月的账单报告`.
- Business clock: supplied through the real `get_profile.sim_now`; September is `[2026-09-01T00:00:00+08:00, 2026-10-01T00:00:00+08:00)`.
- Monthly consumption oracle: `CARD_SPEND` + `MANDATE`, `OUT`, posted, owned accounts, within that half-open Shanghai range. Transfers are separate from consumption.
- Transaction totals are `totals_scope=filtered_all`; `len(items)` is a page count, never an all-range count. Monthly spending must use `get_spending_summary.total_cents` rather than sum the current transaction page.
- Real provider, Docker, SQL and live reruns are coordinator-only. This implementer used no credentials and made no provider/database calls.

## Baseline evidence supplied by coordinator

Trace: `/private/tmp/sbg-freeze-live-20261031/.ds-logs/full-e2e-aa-bill-baseline-ccf5d33-jet-01/16.trace.json`.

The verified user-visible report says September 2026, total 6448.00元, August 1331.90元, increase 384.12%. It lists 数码3499.00元/54.26%, 其他1470.00元/22.80%, 娱乐1280.00元/19.85%, 健身199.00元/3.09%. Trace records no plan/execution and four cards, but the driver returns `CLARIFY` (exit 2).

Coordinator supplied the fixed SQL oracle and actual checkpoint at `/private/tmp/sbg-freeze-continuation/bill-sql-oracle.json` and `bill-checkpoint-oracle.json` (generation `demo_20261031113502_21268cec`, commit `ccf5d33`). September 644800 cents and August 133190 cents match. Raw SQL has eleven category names; mapping labels outside PRD's eight-category whitelist into 其他 produces 娱乐128000、数码349900、健身19900、其他147000 cents, exactly matching the report. The tool's 38412bp month-on-month change and category shares 1985/5426/309/2280bp also match the visible report.

Actual checkpoint defects: `needs_write=false, unclear=true`; L2 `INTENT_UNCLEAR` plus a `FORM_REQUIRED` clarification with no ActionKind; the driver labels that CLARIFY. Reads were one September summary and two transaction pages with `to_at=2026-09-30T23:59:59+08:00`. There was **no** anomaly detection read. Accurate total alone therefore did not meet complete B4 coverage.

Coordinator then captured the formal F/app → L2 → L3 `detect_anomalies` read for the exact September half-open range, account_id=null, same generation: `/private/tmp/sbg-freeze-continuation/bill-anomalies-live-oracle.json`. It has three items:

- `txn_zs_014`: 128000 cents, 2026-09-07 02:13 Shanghai, NIGHT_LARGE plus NEW_MERCHANT_LARGE.
- `txn_zs_016`: 26800 cents, 2026-09-15 12:04 Shanghai, DUPLICATE, previous `txn_zs_015`, four-minute window.
- `txn_zs_017`: 349900 cents, 2026-09-22 15:30 Shanghai, NEW_MERCHANT_LARGE.

No database or provider requests were made by this implementer. These are coordinator-produced evidence files. Their SHA256 hashes are respectively `335aeed3d9819796582eb28fa0af288ca336e41c3f2ce694746fbd5e536a2b95`, `94da06281663de97b132d50bce7764f3632f89b8cb2feb09d83c7bcf6ce902dc`, and `01c77418ac5aac3198e4e5d75119ddaaf69fe7f721bce49c0d6713cb221d2265`.

## Offline verification performed here

Commands below use the existing locked environment without copying `.env` or `.secrets`.

1. Existing focused suites:

   `/Users/jet/Downloads/WeBank/.venv/bin/python -m pytest tests/agent/test_respond.py tests/agent/test_current_date.py tests/try_agent -q`

   Result: **112 passed**, exit 0, 3.20s. One pytest-cache write permission warning in the managed worktree; subsequent commands use `/private/tmp` for cache.

2. New driver regression, before production change:

   `/Users/jet/Downloads/WeBank/.venv/bin/python -m pytest tests/try_agent/test_bill_readonly.py -q -o cache_dir=/private/tmp/sbg-bill-pytest-cache`

   Result: **1 failed / 11 passed**, exit 1, 0.06s. `test_completed_bill_report_needs_no_write_decision` expected COMPLETED (1) but observed UNKNOWN (5). Eleven negative/terminal routing cases already pass. This establishes a driver gap independently of the pending SQL reconciliation.

## Component 1: read classification and driver terminal outcome

Commit `f98a9d7` adds a full-clause read grammar for monthly reports, preserving independently parsed comma-separated writes and rejecting unparsed suffixes. It does not match reports by substring. X-1, a report plus transfer, and a report plus card-limit update keep their write scopes. Unknown actions cannot hide behind the report text.

Driver COMPLETED is available for a structured L2 NO_WRITE result, explicit needs_write=false, reads present, final AI-message event received, no plan/decision/execution/route_error, and no actual clarification. It does not infer completion from reply prose, bypass an existing clarification, fabricate ALLOW, confirm, or execute anything. Model unclear=true alone cannot invent an L2 clarification on a known read-only request.

Red: parser + new driver tests **8 failed / 18 passed**. Green: **26 passed**. Existing W1 suite **1646 passed / 159 skipped**, try-agent **85 passed**. Ruff check/format and diff checks passed. DB skips were intentional; no live classification rerun is claimed here.

## Component 2: complete month and anomaly evidence

For one complete typed report request already classified NO_WRITE by L2 and needs_write=false by L1, production gather refreshes `get_profile.sim_now`, then reads `get_spending_summary` and `detect_anomalies` through the existing scoped executor. It retains actual source_ref, untrusted paths, versions, observed_at, and strips/wraps data through the existing helper. It does not use cached business-date text, sum transaction pages, introduce write tools, or alter transfer/AA/redemption prerequisites. No model inference is needed for those two mandatory B4 reads.

The fixed report formats the matching real summary and anomaly fields. It includes total, previous month, tool-provided percentage changes/shares, and each anomaly's Shanghai time, integer-cent amount and deterministic reason. It omits merchant/memo instructions from the fixed text. Missing/mismatched month, absent anomaly evidence, or an incomplete right boundary cannot produce a complete report or a false “无异常”. The existing output verifier and number masking still process the reply.

The new month tests cover the real September boundary, December/year rollover, leap February, stale prior-turn business date, mixed/unscoped requests staying outside the fixed path, and 402/403/429 propagation. Real anomaly fixtures and transaction-page distractors verify that 6448.00元 comes from the monthly aggregate, not one page or the 9048.00元 all-outflow total. Amount and percentage extraction matches source facts; OutputVerifier runs without repair inference.

Red before implementation: **7 failed / 4 passed**. Green after implementation and real anomaly fixtures: **15 passed**. Two additional clarification-guard tests failed before adding an explicit refusal to use the fixed path whenever clarification_id or clarification is present, even alongside NO_WRITE; final focused suite **17 passed**. An additional real-fixture check found that the existing amount regex treats timestamp minutes followed by a comma and amount as one number after NFKC normalization. The renderer now separates time with `；金额`; verifier rules were not weakened. A test assertion originally expected Chinese punctuation before existing NFKC masking; it was corrected to test time/amount/reason facts instead of punctuation.

Affected agent/driver run before the last four fixture checks: **731 passed / 39 skipped**, with localhost excluded from proxies. The initial sandbox run had two `PermissionError` failures binding loopback ports; the explicitly authorized elevated local run passed. Ruff initially reported import sorting/line-length issues; formatting fixed them, and targeted ruff check/format then passed.

Complete pure-unit run before the final clarification guard: **3617 passed / 1071 skipped**, exit 0, 33.44s. Final post-guard run: **3619 passed / 1071 skipped**, exit 0, 43.35s. Command:

```bash
env -u P1_TEST_DATABASE_URL NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost GATEWAY_DB_TESTS=0 AGENT_DB_TESTS=0 OBLIGATIONS_DB_TESTS=0 GATEWAY_SECRET_TESTS=0 /Users/jet/Downloads/WeBank/.venv/bin/python -m pytest -q --tb=short -o cache_dir=/private/tmp/sbg-bill-pytest-full-cache
```

Final targeted ruff check and format check both passed; `git diff --check` passed. Full PostgreSQL/network/local-CI and post-fix live verification remain coordinator gates.

## Remaining gates

- Coordinator live rerun on the integrated final head: G3 must be NO_WRITE with no FORM_REQUIRED clarification; report must include the exact three anomalies and source numbers; read range must end at Oct 1 midnight +08; no new L3 writes.
- Coordinator full CI, shared AGENTS updates and integration. Suggested PRD §6.1/6.7 delta: the anchored L2-classified NO_WRITE B4 path may use deterministic mandatory reads and formatting of actual aggregate/anomaly fields, retaining output verification. This is within the task's explicit permission to deterministically format trusted aggregates.
- This component covers a bounded whole-sentence report grammar, not every report paraphrase. Other non-core cases and global freeze remain unverified. No push/merge/tag here.

## Independent-review R1 follow-up

Reviewer found a driver-only acceptance defect: the fixed GENERIC_ERROR from an incomplete monthly report becomes `请求无法处理,请重新发起。未执行任何操作。` after the actual OutputVerifier's NFKC normalization. Its state has NO_WRITE, reads and a final message but no route_error; the new driver branch incorrectly called that COMPLETED. This affects reporting of failure, not L2 write authorization.

The regression now runs the real RespondAdapter failure path, real OutputVerifier, then the complete driver's MockTransport flow. Additional cases cover the canonical fixed text, NFKC punctuation and surrounding whitespace. Before the fix: **4 failed / 22 passed**, with the four failures observing COMPLETED (1) instead of UNKNOWN (5).

The production change only prevents a NO_WRITE completion when the **entire** final reply, after NFKC normalization and outer-whitespace stripping, equals the reserved GENERIC_ERROR/REINITIATE fixed text. It does not use substring or natural-language error inference, import L1's model package into the standalone CLI, modify graph interfaces, or change higher-priority Decision/clarification routing. Tests read the actual canonical template, so a future template change cannot silently evade this guard.

After the fix: **26 passed** in the driver bill file. A valid report containing an error fragment or quoting the entire fixed error text still completes. The same fixed text alongside DENY, REPLAN, ALLOW or a real clarification retains the structured outcome's priority. Existing route_error/missing-message/mixed-write guards remain covered. Affected try-agent + bill suite: **113 passed**, exit0, 2.94s. Final full default pure-unit run, with the same explicit DB/container opt-outs as above: **3630 passed / 1071 skipped**, exit0, 33.91s. Ruff check/format and whitespace diffcheck passed.

R1 exact commands use the same existing locked venv:

```bash
/Users/jet/Downloads/WeBank/.venv/bin/python -m pytest tests/try_agent/test_bill_readonly.py -q --tb=short -o cache_dir=/private/tmp/sbg-bill-r1-cache
/Users/jet/Downloads/WeBank/.venv/bin/python -m pytest tests/try_agent tests/agent/test_bill_accuracy.py -q --tb=short -o cache_dir=/private/tmp/sbg-bill-r1-focused-cache
env -u P1_TEST_DATABASE_URL NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost GATEWAY_DB_TESTS=0 AGENT_DB_TESTS=0 OBLIGATIONS_DB_TESTS=0 GATEWAY_SECRET_TESTS=0 /Users/jet/Downloads/WeBank/.venv/bin/python -m pytest -q --tb=short -o cache_dir=/private/tmp/sbg-bill-r1-full-cache
```

No provider, DB, Docker, deployment, push/merge/tag or shared AGENTS/PRD change occurred in this follow-up. Root's integrated live and full CI gates remain open.
