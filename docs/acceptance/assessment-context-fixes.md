# Assessment panel and current-plan authorization fixes

Date: 2026-10-31. Baseline: `22a87799c2b422a6c2186490a5972e11bc05dce4`.

## A. Authoritative assessment panel

Input: a persisted, validated app FORM assessment. Output: a yellow confirmation view displaying versioned questionnaire prompts and selected answer text, covered by the existing semantic hash. Missing authority or malformed answers must fail closed.

Red: `.venv/bin/pytest tests/gateway/w4b1/test_planview.py -q` on old production code: 2 failed, 28 passed; both legal-panel tests reached the deliberate metadata ValueError. Green: `.venv/bin/pytest tests/gateway/w4b1 tests/gateway/w3b --ignore=tests/gateway/w4b1/db --ignore=tests/gateway/w3b/db -q --tb=short -p no:cacheprovider`: 42 passed. Ruff check/format pass for changed Python files.

Selected-answer, prompt and option-text changes alter the existing semantic hash; unknown version, missing/duplicate/unknown question, bad option, agent origin and absent metadata remain rejected. Added real gateway PG G19→G4 panel-content and idempotency regression in `tests/gateway/w3b/db/test_assessment_panel.py`. It uses FakeL3 and does not prove real bank persistence. Coordinator command (not run by this subagent): `GATEWAY_DB_TESTS=1 .venv/bin/pytest tests/gateway/w3b/db/test_assessment_panel.py -q`, followed by real L3 confirmation/rejection/idempotency acceptance and full required CI.

A combined command containing the DB-directory suite was rejected by automatic approval review as violating coordinator-only PG verification. It did not execute; the subsequent verification explicitly excluded all DB directories. No real API, LLM, DB, Docker, browser, deployment, push or merge is performed by this subagent.

## B. Current-plan authorization roots

Input: current plan declaration and credential binding. Output: only declared roots, with the existing evidence verifier responsible for persisted lineage and permissions. Undeclared old roots and NO_WRITE context cannot authorize writes.

Verification pending. Coordinator owns the real PostgreSQL and integration gates.
