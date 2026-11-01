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
