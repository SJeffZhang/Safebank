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

## Unit 3a — unmounted candidate submission safety stop

Added a deferred-G19 test that unmounts Assistant before the response arrives. RED on Unit 2 HEAD: **1 failed / 9 passed**, because the old component emitted a second, action-kind message to G3 after unmount. Cleanup now invalidates the pending choice marker before aborting any stream, so the delayed response cannot enter G3. GREEN: **10/10 clarification tests passed**; TypeScript and diff-check exit 0. Logs: `/private/tmp/sbg-nov1-repairs/unmount-{red,green}.log`. This test proves no stale G3 attempt occurs; it does not rely on backend ownership rejection. No full-suite repeat was run for this isolated safety stop.

## Unit 3b — fixed current-G21 form guidance and clarification-ID deduplication

The existing G21 `prompt_template_id` selects three frontend-owned fixed messages:

- `risk_assessment_form`: only the customer's own six-question assessment computes the level; direct R5 assignment is unavailable. The fixed link opens existing `#wealth`, with an explicit instruction to enter Risk Assessment there (the current native Wealth route does not support directly opening its assessment tab).
- `register_payee_form`: the unregistered target is not automatically added or paid; the fixed `#transfer` link leads to manual payee entry and review without URL parameters or prefilled values.
- `transfer_consultation`: consultation is not authorization; no transfer has been initiated. Existing `#overview` / `#cards` links allow balance and limit inspection, without claiming feasibility has passed.

Unknown template IDs use the safe generic business-page/new-request text. The lookup accepts only its own fixed whitelist keys, including safe fallback for inherited JavaScript keys such as `__proto__` and `constructor`. Empty required_fields no longer produce an empty “您可以在下方补充” line. SLOTS candidate and explicit typed-answer behavior stays intact. Custom clarification cards with the current G21 ID are displayed only in the active G21 area; repeated historical clarification IDs display once. Other historical replies and business cards are retained in state and in view.

RED: the new guidance/dedup tests gave **5 failed / 10 passed** before implementation (`guidance-red.log`). Additional unknown-key probes caught two initial object-lookup failures before the whitelist-own-key guard (`guidance-unknown-red.log`), then passed. GREEN: the focused AssistantClarification/Comparison/Topics/Wire/PlanLifecycle command gave **5 files / 55 passed**, with TypeScript and diff-check exit 0 (`guidance-green.log`). Tests use the production request/stream/SSE code with complete current-G21 HTTP fixtures. Link rendering and zero action/proposal side effects are asserted; no actual navigation/browser or live G21 emission was exercised.

After all added units were implemented, one final combined frontend verification was run: **49 files / 474 frontend tests**, **16 BFF tests**, TypeScript and Vite production build all passed with exit 0. Logs: `/private/tmp/sbg-nov1-repairs/frontend-final-{full,bff,build}.log`. These replace the earlier 466-test count for this worktree's final handoff. Root owns the server-side selection of these three template IDs, the 15 read-only NO_WRITE cases, secure Agent SSE errors, integration, independent review and exact-head CI. No backend/auth/contract wire fields, form prefill, OTP/approval, demo state, original reports, AGENTS or PRD were changed here.
