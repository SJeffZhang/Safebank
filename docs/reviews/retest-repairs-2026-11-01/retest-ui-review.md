# Frontend read-card / reply independent review

Target `5c86b0ce4e61ede51c6bcfc89ab21fa08c085b43`; base `8e2810e6764e3fd3e37406b31e694605751e0356`.

Review in progress. Exact frontend target archived into `/private/tmp/sbg-retest-ui-review`; existing dependencies linked from the coordinator frontend. Original author worktree and production source remain unchanged.

## Verdict: APPROVE

No actionable P0, P1 or P2 findings in the scoped frontend diff. R3/R4 are presentation/experience repairs, not newly established banking or PRD scenario pass rates. This approval does not replace a real-browser original-scene rerun.

## Source review

- `AssistantReadData.tsx:16–22` removes a single exact source-matched outer marker for legacy display labels. It does not globally delete fragments, parse HTML, mutate `card.data`/`source_ref`, create actions, or synthesize names from IDs. Embedded or differently sourced markers remain ordinary text. Current plain projected names display directly; missing names use explicit human placeholders.
- All display leaves in ReadData/Reply remain React text nodes. Neither file uses `dangerouslySetInnerHTML`, a URL/HTML parser, `href`, executable attributes, model-derived buttons, or form prefill. AssistantReply supports only paragraph/bold/structural pipe-table rendering; malformed or empty tables stay literal rather than inventing rows.
- Monetary values require safe integer number cents before formatting through the existing integer formatter. Missing/non-integer money is omitted or unknown, never promoted to zero. Units/NAV preserve validated decimal strings without `Number` rounding.
- `displayDate` distinguishes YYYY-MM-DD bank trade/NAV dates from instants; calendar validity is checked before parsing, and instants require a timezone and format with `Asia/Shanghai`. UTC crossing midnight changes the displayed instant's day while a date-only trade date remains unchanged. Invalid dates and timezone-less instants show an unavailable label.
- Read cards distinguish book/available/pending account funds; merchant charges/scheduled transfers/reserve totals; available/frozen holdings and pending orders. Product/order names are accepted from actual data fields; there is no prd_/si_/src_ lookup table or ID-to-name guess. Raw plan status UI remains outside this read-card scope.
- In `Assistant.tsx`, the behavioral change is reply/read-card renderer delegation plus a card group role and removing the visible source badge. The G21/G19/G3 candidate flow, G22a label request and HINT callback, OPEN_APPROVAL handlers, navigation allowlist and pending-plan behavior are unchanged by this diff.
- Existing tests changed source-ID selectors to visible card-group/month/content selectors because the source badge is deliberately hidden. Wrong-month/out-of-range assertions, clarification one-visible-history/dedup checks, candidate count, real action calls and approval assertions remain; no authorization test was deleted or relaxed. The direct ProductView test now asserts the explicit missing-name placeholder instead of requiring an internal ID to appear.

## Commands actually executed

The author worktree is clean at target `5c86b0ce4e61ede51c6bcfc89ab21fa08c085b43`. It has no installed node_modules, so the reviewer archived that exact commit's frontend into `/private/tmp/sbg-retest-ui-review/frontend` and linked existing coordinator dependencies without modifying either production checkout. Reviewer-only tests are in `src/mobile/IndependentReadReview.test.tsx` inside that temporary archive.

- `npm test -- --cache=false` in the temporary exact archive — **51 files / 499 tests passed**, exit 0, 17.37s. This total includes the author's 491 existing/final tests and 8 reviewer-created negative controls; the counts overlap and must not be added again.
- Independent controls cover a wrong-source wrapper containing script text; exactly one outer unwrap with payload immutability; Shanghai midnight versus date-only date; invalid dates and timezone-less timestamps; boolean/float/string/unsafe money rejection; exact large Decimal text; SVG/javascript pseudo-link/table-pipe payloads remaining inert; malformed/empty table fallback.
- `npm run typecheck` in that archive — passed, exit 0.
- `git diff --check 8e2810e6764e3fd3e37406b31e694605751e0356 5c86b0ce4e61ede51c6bcfc89ab21fa08c085b43` — passed, exit 0.
- `git rev-parse HEAD` / `git status --short` on the author worktree before and after review — exact target SHA, clean.

The suite emits existing React Router v7 future-flag warnings; there are no failed tests. An initial `npm test -- --help` attempt in the original worktree reported missing vitest because dependencies are deliberately absent there; that is a harness setup observation, not a product failure. A preliminary temporary-directory command also failed before execution because its requested cwd did not yet exist; the archive was then created from the existing author cwd and subsequent tests ran normally.

## Boundaries

These are jsdom/unit/mock-service tests, not live browser, network or bank-state acceptance. The reviewer did not run BFF tests or a production build anew; the author's saved BFF16/build evidence was read, while reviewer validation independently ran frontend tests and TypeScript. No real Gateway, PostgreSQL, provider, Docker, deployment, demo, screenshot, or original 43-case rerun occurred. No tracked source/test or author worktree was changed. Backend cards.py is owned by this reviewer and was deliberately not self-reviewed here.
