# Read-card display repair — 2026-11-01

Base: `8e2810e6764e3fd3e37406b31e694605751e0356` (`origin/main`). Isolated worktree branch: `fix/retest-read-card-ui`; the previous `fix/nov1-w1-query-isolation` branch is preserved.

Input/output: existing UICard source_ref/data and assistant reply strings become React-only customer text, typed facts and semantic tables. Source references and IDs remain in payloads and existing request/action paths. The renderer never derives permission, writes values, pre-fills forms or interprets HTML/URLs.

Evidence read only: `/Users/jet/Downloads/WeBank/output/prd-retest-2026-11-01/REPORT.md`, `residual-ui-findings.md`, and existing `screens/12-result.txt` / `14-result.txt`. Actual contract shapes were checked against `packages/contracts/src/safebank_contracts/views.py`; no live provider, browser, bank state, Docker or deployment was used.

## Red/green

- `npm test -- src/mobile/AssistantReadPresentation.test.tsx` against the unchanged frontend: **10 failed / 2 passed**, exit 1. Saved in `red.txt`. Initial fixture-only normalization and product enum issues were corrected before this recorded red run.
- Same command after the initial repair: **12 passed**, exit 0.
- Additional invalid-timestamp boundary: **1 failed / 14 passed** before strict calendar validation (`date-boundary-red.txt`), then **15 passed**, exit 0.
- First full frontend run: **1 failed / 490 passed** because an existing clarification deduplication test still selected visible source IDs. Preserved in `first-full-source-selector-failure.txt`; replaced those selectors with exact visible card-group counts and retained the G21/action assertions.
- Final `npm test`: **491 passed / 50 files**, exit 0 (`green.txt`). The suites overlap with the focused counts; do not add them together.
- Final `npm run test:bff`: **16 passed**, exit 0 (`bff.txt`).
- Final `npm run build`: TypeScript check and Vite production build **passed**, exit 0 (`build.txt`).
- `git diff --check`: exit 0 before staging. Staged check then found only terminal-log extra blank EOFs; normalized those endings without changing recorded results, and the staged check passed. No dependency/package-lock changes.

Production-shape cases cover exact matching source wrappers, plain React labels including HTML-like text, preservation of embedded user markup, book/available/pending account amounts, missing merchant names, last versus next subscription debit times, scheduled versus merchant reserves, nested anomaly Shanghai timestamps, missing product names and NAV calendar dates, available/frozen holdings versus pending orders, and Markdown table rendering. Exact Decimal precision, invalid date and absent/non-integer money negative controls are also included. Existing source-ID based selectors were replaced with customer-visible card groups/content while retaining wrong-month/out-of-range rejection assertions.

## Boundaries

- Exact-source legacy wrapper removal affects only known display labels. It uses a matching prefix/suffix and slices once, with no regex deletion across user content, HTML parsing, entity trust promotion or source payload mutation.
- Missing names show an explicit human placeholder; IDs are never mapped to synthetic names. Agent-side source-scoped name projection and selection of relevant query reads are a separate component owned by the cards.py agent.
- Monetary values must be safe integer cents; missing values do not become zero. Decimal unit/NAV strings are not rounded through Number. Timestamp rendering explicitly uses Asia/Shanghai, while trade/NAV calendar dates preserve their original day.
- Tables support a narrow paragraph/bold/pipe-table subset; HTML, links, images, scripts and action buttons from assistant prose remain inert text. This is not a full Markdown engine.
- Approval actions, G21 parent chains, G19/G3 flow and G22a HINT labels are untouched. Current raw plan-ID status UI is outside the read-card scope.
- No live end-to-end acceptance, production LLM, new screenshots, original 43-case rerun, DB, Docker, deployment or full repository CI occurred here. Root performs integration and CI.
