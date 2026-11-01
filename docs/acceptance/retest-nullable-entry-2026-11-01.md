# Agent nullable entry regression (2026-11-01)

Baseline: `8e2810e6764e3fd3e37406b31e694605751e0356`.

Scope: legal G3 action/hint `text=null` through the Agent HTTP/SSE route and real compiled gateway graph, preserving L2-authoritative ingest and typed rejection. No Docker, database, production provider, browser, demo, or deployment.

## Root cause

HTTP red reproduction confirmed. The route constructs a local `UtteranceRef` with `u.get("text", "")` before G20. Explicit null does not use that default, while the local field requires a string. Existing G20 adapter already converts nullable text.

## Red / green evidence

Baseline red command: existing project Python environment, `python -m pytest -q tests/agent/test_retest_nullable_entry.py --tb=short` against unchanged production code at the baseline above. Result: **6 failed, 11 passed**. Transfer action, product action, and hint all failed with the safe `MODEL_OUTPUT_INVALID` SSE payload before authoritative ingest; ingest-rejection and scope-denial tests were blocked by the same early conversion. Existing typed and identity rejection checks passed.

Implementation uses one shared `utterance_ref_from_fields` conversion at the HTTP and G20 boundaries. It maps only `kind=hint/action,text=None` to an empty local prompt string; typed null/non-string values still fail validation. G20 remains the sole source of scope and authoritative text.

Final baseline negative control: `git archive 8e2810e6764e3fd3e37406b31e694605751e0356` into a temporary isolated directory, copy only the final regression test, then `python -m pytest -q -p no:cacheprovider tests/agent/test_retest_nullable_entry.py --tb=short`. Result: **6 failed, 11 passed**, exit 1 as expected. The working tree and previous branch were never reset or overwritten.

Final implementation verification:

- `python -m pytest -q tests/agent/test_retest_nullable_entry.py --tb=short`: **17 passed**.
- `AGENT_DB_TESTS=0 python -m pytest -q tests/agent --tb=short`: **1089 passed, 41 skipped**. PostgreSQL tests remain unrun. A sandboxed attempt first returned 1087 passed / 41 skipped / 2 failures because localhost socket bind was prohibited in `test_default_client_real_localhost_roundtrip` and `test_default_client_real_cross_host_redirect_not_followed`; the full suite passed after the tool approved localhost test execution outside that restriction.
- `ruff check` and `ruff format --check` for main.py, gateway/adapters.py, and the new test: passed. Initial test formatting issues were corrected before the final checks.
- `git diff --check`: passed.

## Coverage and limits

The new tests run the real FastAPI route and JWT verification, real compiled gateway graph, G20/G21/G2/G4 client, and real Understand/Gather/Plan adapters. PostgreSQL storage/lock are replaced with InMemorySaver/a test lock, and external gateway/provider HTTP uses scripted transports. The product action includes the actual X3 gather prerequisites (profile, accounts, upcoming charges, risk profile, selected product); all legal paths stop at the scripted L2 approval interrupt with no execution call.

Checks retain typed string handling and authoritative G20 replacement of caller text, reject typed null/non-string text and invalid kinds, preserve action/hint kind/scope/parent chain, reject expired identity and mismatched session, and propagate G20 source-expiry/scope failures plus G4 scope refusal without exposing the private error canary or token in public SSE.

Limitations: gateway expiry/scope verdicts and approval outcomes are scripted, so this is not a real L2 database authorization test or bank-state acceptance. G19/G3 registration, BFF/browser rendering, production DeepSeek, Docker, database, demo, deployment, full CI, and saved 02/38 field-result rechecks were not run. No claim changes the original scene results. Root coordinator maintains AGENTS.md and PRD.md and runs shared release gates.
