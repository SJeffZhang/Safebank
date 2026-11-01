# November 1 clarification UI repairs

Base: aa7e5d26160ef3be1d7b9341a0398b2f0390d0b1. Branch: fix/nov1-clarification-ui.

## Scope and component plan

Unit 1: consume the current G21 parent chain for G19 candidate selection; register the resulting action through G3 without typed-only clarification_id; explicit SLOTS typed answers only; keep new messages separate; stop on missing parents, stale sessions, repeat clicks, or transport failure. Inputs are G21 wire responses and user choices; outputs remain the existing G19 then F3 requests. No L2/L1 authorization changes.

Unit 2: preserve each requested month in the formal case 14 comparison, keep structured source-month validation and inline report month in agreement, and retain case 16/35 single-month behavior. Use simulated Shanghai time, not the wall clock.

## Verification

Pending RED/GREEN evidence. Final full frontend tests/BFF/build will run once before handoff. No API, model, DB, Docker, browser, or demo-state operations are in scope.

## Limitations

Automated local UI tests do not prove live model or bank-state acceptance. Original case 38 remains failed until a separately authorized end-to-end rerun.

## Unit 1 — G21/G19/G3 clarification protocol

Changed only mobile Assistant, its Clarification wire type, and a new strict HTTP-boundary test. Candidate requests now use the unique current G21 parent and action-only G3 registration. Ordinary messages never inherit an old clarification. A separate SLOTS answer field explicitly marks typed slot answers. The candidate submission lock starts before G19; session changes discard pending responses, and missing/non-unique parents or failed G19 requests stop before G3. No form fallback, plan generation, approval, OTP, hash, or authorization rules were changed.

RED on the untouched production files: `npm --prefix frontend test -- src/mobile/AssistantClarification.test.tsx` gave **6 failed / 3 passed**, catching null parent, missing explicit typed-answer UI, implicit old-clarification inheritance, and duplicate G19 submissions. The strict fake rejects malformed G19 and G3 wire bodies rather than accepting every request. It exercises the production request/stream/SSE code; fetch alone is controlled. Evidence: `/private/tmp/sbg-nov1-repairs/clarification-red.log`.

GREEN: the nine new tests passed. Focused follow-up `npm --prefix frontend test -- src/mobile/AssistantClarification.test.tsx src/mobile/AssistantWire.test.tsx src/mobile/AssistantTopics.test.tsx src/mobile/PlanLifecycle.test.tsx` gave **4 files / 43 tests passed** (no warnings); `npm --prefix frontend run typecheck` initially found a table-test tuple typing error; the fixture was corrected to named parent-array cases and rerun before handoff. The first unpublished commit was made before that typecheck result had been inspected; it was corrected and replaced, and is not a validated release. Final typecheck and diff-check results are recorded below. Evidence: `/private/tmp/sbg-nov1-repairs/clarification-special.log`. An initial shell result-capture wrapper used zsh's readonly `status` name after the successful nine-test run; it was corrected to `sbg_test_exit` and the focused command reran successfully. No product or test failure was hidden by that wrapper.

The first commit has targeted verification only; the one full frontend/BFF/build run is reserved for the combined handoff after Unit 2. This is local protocol/UI evidence, not a live X3 purchase or SQL acceptance.

Corrected Unit 1 verification: the full 43-test focused command, `npm --prefix frontend run typecheck`, and `git diff --check` all completed with exit 0 after the named-case fixture change. The non-unique-parent case now supplies the actual two-element array instead of Vitest spreading it as callback arguments.

## Unit 2 — formal case 14 source-month comparison

The formal text “我上个月各类消费的占比，和前一个月比怎么样” now carries two display months computed from the simulated Shanghai calendar. Each source summary retains its own month and total; a source for any other month is still rejected. The inline workspace receives the primary requested month (September for the formal October simulation), and expanding it reads September boundaries. No totals are merged, no wall-clock date is used, and no banking write scope changes. Existing single-month case 16 / X1-style reads and wrong-month anomaly rejection remain covered by AssistantTopics.

RED: after completing the Summary fixture's required daily array, `npm --prefix frontend test -- src/mobile/AssistantComparison.test.tsx` produced **3 failed / 1 passed** on the first-unit HEAD. The failing tests covered the two valid source months being rejected, missing September inline reads, and the simulated Shanghai year rollover. The already-passing test proved that selecting a new inline month refreshes an expanded detail view; no extra Workspace/Bills change was needed. The first fixture attempt omitted daily and threw in Bills; this was a test-data error, corrected before accepting RED. Evidence: `/private/tmp/sbg-nov1-repairs/comparison-red.log`.

GREEN: `npm --prefix frontend test -- src/mobile/AssistantComparison.test.tsx src/mobile/AssistantTopics.test.tsx src/mobile/AssistantClarification.test.tsx` gave **3 files / 30 passed**. A mechanical `requestedMonthss` rename initially caused ReferenceError failures; it was corrected before the successful rerun. Evidence: `/private/tmp/sbg-nov1-repairs/comparison-green.log`.

## Combined final frontend verification

All commands were run locally in the assigned independent worktree without a browser or live services:

- `npm --prefix frontend test`: **49 files / 466 tests passed**, exit 0 (`/private/tmp/sbg-nov1-repairs/frontend-full.log`).
- `npm --prefix frontend run test:bff`: **16 passed / 0 failed**, exit 0 (`/private/tmp/sbg-nov1-repairs/frontend-bff.log`).
- `npm --prefix frontend run build`: TypeScript and Vite production build passed, exit 0 (`/private/tmp/sbg-nov1-repairs/frontend-build.log`).
- `git diff --check`: exit 0.

No API/model/DB/Docker/browser/demo-generation operations were performed. Repository-wide Python/PG, independent review, exact-head full CI, and live case 38 / 14 acceptance remain root-owned gates. The original saved formal results are unchanged. Generic FORM_REQUIRED emission and raw-name/error presentation outside these two units remain separate root-coordinated work.
