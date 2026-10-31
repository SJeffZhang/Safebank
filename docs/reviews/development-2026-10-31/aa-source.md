# AA self-source independent review

Verdict: **APPROVE** for candidate code. No blocking correctness or security findings in the reviewed diff. Coordinator real PG/live/final exact-head CI gates remain required before merge; this review does not certify those unrun gates.

Candidate: `cb52eb6b818611608740dea5afd1208cb84fbc9b`  
Base: `ccf5d3330b44f4d5049fbbdb5bf5d78ac352ba1b`  
Worktree: `/Users/jet/.codex/worktrees/safebank-aa-self-source/WeBank`

## Scope and evidence

Read the coordinator common/brief/report, current AGENTS §1–3/§8 under the §8.7 task-reading exception, relevant PRD §5.1/§6.1–6.2/§7.4–7.6/§9.4, all seven changed files, and adjacent authorization loader, text matcher, G3 split parsing, and intent field matching. No application files or shared AGENTS/PRD edited. No actual API, Docker, DB, secret-file, push, merge or tag operation performed.

## Findings

No actionable blocking finding.

- `services/gateway/app/pipeline/provenance.py:86` restricts the new proof to `create_split_request`, `include_self`, strict Python `True`, a typed/SCOPED persisted row with exactly one `SPLIT_CREATE` action, an exact bool True scope field, and normalized source text exactly `我和`. It does not add global bool vocabulary or accept arbitrary equal scope fields. False, numeric/string substitutes, unrelated/broad spans, other actions/tools and context are not covered.
- `provenance.py:212` still obtains the row only from `authorization_evidence`. That unchanged loader verifies claimed root IDs, actual assistant credential claims, customer/session/origin, ACTIVE/SCOPED status, live session and epoch, persisted ref equality (including scope/text/times), and bounded live parent lineage before returning rows. The new helper cannot manufacture source rows or skip these checks.
- `services/gateway/app/pipeline/intent.py:183` retains independent type-and-value equality for sensitive fields. A valid self phrase cannot authorize a different AA total, participant set or account. Existing taint and memo handling are unchanged.
- `services/agent/app/llm/plan.py:787` now rejects participants when list_payees data is absent or empty, and still rejects invented IDs. `plan.py:815` requires exactly one include_self utterance source referencing an actual typed utterance; existing reference/span validation remains active. The local check declares evidence only, leaving text meaning and authorization to L2. The default regeneration bound is still one retry (`plan.py:1025`). Financial choices are not silently rewritten.
- `services/agent/app/llm/gather.py:287` supplements actual account/payee reads only for AA writes, preserving real read envelopes and skipping already read/attempted tools. Read-only AA status requests do not add these write prerequisites. No authorization or mocked read data is generated.
- Existing good-AA fixture changes add truthful sources; the wrong-participant negative remains. The new negative tests exercise missing/empty payees, invalid source types/ref/span/context, narrow scope semantics and exhausted bounded generation.

## Independent verification

All commands ran against the exact candidate with `PYTHONDONTWRITEBYTECODE=1`, pytest cache disabled, and real DB flags explicitly zero.

1. New AA pure tests plus neighboring planner/merge/redemption-gather tests: **41 passed / 11 skipped**, exit 0. The 11 are actual-PG cases and were not counted as passing.
2. New AA tests alone: **32 passed / 11 skipped**, exit 0.
3. Scoped ruff check and candidate diff whitespace check: passed.
4. Independently loaded the original three production modules from exact base via `git show` into a separate Python process, without modifying checkout files, then reran the new AA tests: **14 failed / 18 passed / 11 skipped**, exit 1. Thirteen failures cover L1 source/payee/prerequisite regressions; one covers the valid L2 self proof. The other negative controls continue to pass. Log: `/private/tmp/sbg-aa-independent-old-red.log`.
5. The first isolated old-module attempt failed before tests because its Python path omitted the contracts package. This harness setup error was corrected before collecting the red result above; it was not counted as a regression failure.

## Remaining coordinator gates

Run all eleven real G3/JWT/PG cases and relevant existing authorization/lost-card/W2 gates. Then actual fresh-generation live09 must show the correct three seeded payee IDs, valid include_self source, expected green decision/execution and SQL split allocation; finally run the full local CI on the final integrated full SHA. Rebase/integration must preserve both AA and bill edits to gather.py. Production recordings must be collected after those prompt changes settle.

## Targeted follow-up: scoped AA understanding classified read-only

Follow-up candidate: `e884e0c94c1b5558b372f3caed3f20e0072b9b69`  
Increment base: `992b5ddad6ac5be4f05d8692f49a60174d939a40`  
Rebased main includes redemption merge `a113787`.

**APPROVE** the targeted increment. No new blocking finding. This verdict covers the changed understanding adapter/new tests/acceptance notes and the coordinator's preceding test-only alternate-action fixture correction. Fresh live09 and final complete exact-head CI remain unaccepted by this review.

The sanitized actual failure at `/private/tmp/sbg-freeze-continuation/aa-992b5dd-readonly-failure.json` confirms authoritative G3 SCOPED status, sole SPLIT_CREATE with correct account/total/participants/self fields, no clarification, but L1 `split_bill/needs_write=false` and zero plans, splits or executions. The repair addresses this observed earlier routing failure rather than changing authorization.

- `services/agent/app/llm/understand.py:41` requires one and only one actual typed UtteranceRef, authoritative SCOPED parse status, both clarification fields absent, and an anchored full-sentence match of the observed narrowly supported AA command. Questions, quoted inputs, calculations/progress requests, mixed commands, omitted-self variants, other expense/time wording, context/hint/action/multiple inputs, and NO_WRITE/unknown/unclear/clarifying states are outside this guard. No normalized_scope value or arbitrary model field is treated as authorization.
- The state text/kind/parse status still originates from G20 ingestion (`graph.py:341` onward). UtteranceRef deliberately has no scope/action-kind data. The new guard only rejects a read-only proposal and asks the model to regenerate; it does not construct authorization, substitute needs_write=True, infer participants, change slots, or write plan arguments.
- `understand.py:69` uses the existing shared schema-validation retry loop, default one regeneration. Exhaustion raises `_AAWriteMismatch`/LlmToolError instead of returning a synthetic write result. Fixed feedback contains no echoed prior model fields or calculated financial values. Actual validated model output is returned unchanged.
- Provider invocation remains outside the local validation handler (`understand.py:70`), so 402/403/429 immediately propagate. No exception-triggered transport retry, fallback model or independent extra retry budget is added.
- In `992b5dd`, adding `card_id=crd_zs` to the persisted wrong-action test scope makes CARD_LOST complete enough for the real authorization loader to reach provenance. The test still has wrong action kind plus equal AA bool and still asserts UNKNOWN_SENSITIVE_ARG. This improves the specificity of that negative control; no production rule or assertion was weakened. The coordinator's 36 actual-PG result is recorded by the coordinator, not independently rerun here.

Independent checks on exact `e884e0c`:

1. New 35 understanding tests plus adjacent adapter/provider-stop/AA-source cases: **116 passed**, exit 0; all scripted transport, DB flags zero, no actual API/Docker/DB operations.
2. Scoped ruff check/format and increment whitespace check: passed; worktree remains clean.
3. Loaded exact base `992b5dd` understand.py into a separate Python process without modifying checkout files, then reran all 35 new tests: **9 failed / 26 passed**, exit 1. The six semantic/budget failures and three regeneration-quota-path failures disappear on the new implementation; existing boundary controls remain passing. Log: `/private/tmp/sbg-aa-understanding-independent-old-red.log`.

No implementation, shared AGENTS/PRD, deployment or credentials were changed by this follow-up review. Only this uniquely owned review report was appended. Final follow-up verdict: **APPROVE**, subject to the coordinator's remaining real live and complete CI gates.
