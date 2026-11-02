# Assessment panel and current-plan authorization fixes

Date: 2026-10-31. Baseline: `22a87799c2b422a6c2186490a5972e11bc05dce4`.

## A. Authoritative assessment panel

Input: a persisted, validated app FORM assessment. Output: a yellow confirmation view displaying versioned questionnaire prompts and selected answer text, covered by the existing semantic hash. Missing authority or malformed answers must fail closed.

Red: `.venv/bin/pytest tests/gateway/w4b1/test_planview.py -q` on old production code: 2 failed, 28 passed; both legal-panel tests reached the deliberate metadata ValueError. Green: `.venv/bin/pytest tests/gateway/w4b1 tests/gateway/w3b --ignore=tests/gateway/w4b1/db --ignore=tests/gateway/w3b/db -q --tb=short -p no:cacheprovider`: 42 passed. Ruff check/format pass for changed Python files.

Selected-answer, prompt and option-text changes alter the existing semantic hash; unknown version, missing/duplicate/unknown question, bad option, agent origin and absent metadata remain rejected. Added real gateway PG G19→G4 panel-content and idempotency regression in `tests/gateway/w3b/db/test_assessment_panel.py`. It uses FakeL3 and does not prove real bank persistence. Coordinator command (not run by this subagent): `GATEWAY_DB_TESTS=1 .venv/bin/pytest tests/gateway/w3b/db/test_assessment_panel.py -q`, followed by real L3 confirmation/rejection/idempotency acceptance and full required CI.

A combined command containing the DB-directory suite was rejected by automatic approval review as violating coordinator-only PG verification. It did not execute; the subsequent verification explicitly excluded all DB directories. No real API, LLM, DB, Docker, browser, deployment, push or merge is performed by this subagent.

## B. Current-plan authorization roots

Input: current plan declaration and credential binding. Output: only declared roots, with the existing evidence verifier responsible for persisted lineage and permissions. Undeclared old roots and NO_WRITE context cannot authorize writes.

The G4 handler now passes exactly `plan_draft.utterance_ids` (already checked as a subset of JWT `utts`) to the existing loader. The loader only queries those declared ACTIVE/unexpired IDs; execution replay already supplies the persisted plan IDs. Without declared IDs it loads no roots. Necessary parents remain separate read-only evidence reached by the unchanged verifier, which retains exact root/scope/draft equality, SCOPED status, same customer/sid, JWT binding, liveness, revocation, epoch and parent-chain checks. No scope comparison or NO_WRITE filtering grants authority.

Finalized in-memory regressions on old production code: 4 failed, 10 passed. Restored repair: 14 passed. Initial fixture omitted canonical scope defaults; positive evidence checks were corrected to use `NormalizedScope.model_dump`, then the complete red/green check was repeated against old and repaired code.

Broader pure verification: `.venv/bin/pytest tests/gateway/w1/test_*.py tests/gateway/w2/test_*.py tests/gateway/w3b/test_*.py tests/gateway/w4b1/test_*.py -q --tb=short -p no:cacheprovider`: 1901 passed. Changed Python files pass Ruff check/format. `git diff --check` passes.

Added `tests/gateway/w3b/db/test_current_authorization.py`: real gateway PG G4 X2 using the parser's canonical multi-action scope, derived subscription-fee amount, read sources and actual calendar spans, with preceding ACTIVE NO_WRITE or SCOPED context. Only the X2 root is declared and JWT-bound; a yellow approval and exact persisted root set are required. L3 is explicitly a contract snapshot/read fake, and no execution job is permitted. Both PG regressions and the assessment PG test collect successfully (3 tests); they were not executed.

Coordinator must run `GATEWAY_DB_TESTS=1 .venv/bin/pytest tests/gateway/w3b/db/test_assessment_panel.py tests/gateway/w3b/db/test_current_authorization.py -q`, then `GATEWAY_DB_TESTS=1 .venv/bin/pytest tests/gateway/w3b tests/gateway/w2/db/test_source_lineage.py tests/gateway/w2/db/test_schedule_provenance.py -q` and the full required local CI. Real L3 business acceptance, browser tests, recording and replay remain unverified by this work. Shared AGENTS/PRD are intentionally left to the coordinator.
