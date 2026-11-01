# Nullable Agent entry independent review

Target `e0ea17d5a285a43a3304b600c1942af40cdda3a6`; base `8e2810e6764e3fd3e37406b31e694605751e0356`; integrated source observed at `2fe862ab3d9dab417f081f19bc28f93b77430f7f`.

Review in progress. Only read-only source review and pure in-process tests; no production edits, DB, Docker, browser, model, deployment, or original scene rerun.

## Verdict: APPROVE

No actionable P0, P1 or P2 findings in this scoped change. This approves the code and automated boundary coverage; it does not change original 02/38 scene outcomes or establish browser/real-L2 acceptance.

## Verified code behavior

- `services/agent/app/gateway/adapters.py:145` centralizes the HTTP/G20 local conversion. Only an actual `None` together with `kind in (hint, action)` maps to `text=""`; original kind is passed unchanged to the existing enum validation. There is no fallback to typed, no fabricated user instruction, and no amount/target derivation.
- `services/agent/app/main.py:500` calls that same adapter before graph entry. The graph still fetches registered utterance IDs from G20 and replaces local refs with authoritative G20 kind/text. Scope and parent IDs originate from G20, not caller fields or the empty-string representation.
- No L2 schema/policy/auth/expiry/source-chain/execution code is changed. Existing JWT and session binding still run before the nullable conversion. G20 rejection and G4 scope denial stay terminal; tested legal action/hint flows stop at approval and do not execute G8.
- Authoritative typed null now rejects at the shared conversion instead of the old broad `utt.text or ""` normalization. The intended accepted null cases remain hint/action only; invalid kinds are not relabeled.

## Commands actually executed

Working directory: `/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank`. Integration HEAD was `2fe862ab3d9dab417f081f19bc28f93b77430f7f` at initial inspection and advanced under coordinator work to `c0830b26f36578196eaa9b3d9d54cbb6973d9598` at final inspection. The exact reviewed production and test files were unchanged from target e0ea17d throughout, and the final diff check below returned exit 0.

1. `PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/agent/test_retest_nullable_entry.py tests/agent/gateway/test_client_write_paths.py tests/agent/gateway/test_wire.py tests/agent/routing/test_graph_compiled.py -q -p no:cacheprovider --tb=short` — **79 passed**, 1.33s.
2. `PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -c pyproject.toml /private/tmp/test_sbg_retest_entry_review.py -q -p no:cacheprovider --tb=short` — **13 passed**, 0.96s. These reviewer-created controls exercise direct null/invalid-field conversion, authoritative typed-null rejection, HTTP hint-vs-G20-action mismatch, caller text injection replacement, malformed G20 kind/null preventing provider/approval calls.
3. `git diff --exit-code e0ea17d5a285a43a3304b600c1942af40cdda3a6 -- services/agent/app/main.py services/agent/app/gateway/adapters.py tests/agent/test_retest_nullable_entry.py` — exit 0; reviewed files match the exact target commit despite testing in the integration checkout.

Initial reviewer test invocation without `-c pyproject.toml` failed collection (`ModuleNotFoundError: services`) because the temporary test path selected a different pytest root. Supplying the repository config fixed the harness; that collection error is not counted as a product negative control.

## Boundaries and exclusions

- Actual HTTP/SSE route, JWT verification, compiled LangGraph, GatewayClient and Understand/Gather/Plan adapters execute. PostgreSQL checkpointer/lock are in-memory/test replacements; Gateway/provider HTTP is scripted in process. No real API/provider or database is contacted.
- G20 expiry/scope refusals and G4 approval decisions are scripted responses. Source/TTL/parent binding enforcement in real Gateway/PostgreSQL is unchanged by diff and was not re-exercised as a real service here.
- No browser, BFF, G19/G3 registration, bank-state oracle, production DeepSeek, Docker, deployment, demo or full CI was run. Required real-browser 02/38 follow-up remains for the coordinating release/scene workflow.
- No tracked source/test file was modified by the reviewer. Initial unrelated coordinator WIP in `tests/gateway/w3b/db/test_nov1_hint_publication.py`, and later coordinator documentation WIP, were observed and left untouched.
