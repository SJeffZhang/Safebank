# W1 correction authority and target/amount boundary

Date: 2026-11-01. Base: `2b51bbe92a4b3692e1ab8557e6c2ec587b92ae5f`.
Worktree: `/Users/jet/.codex/worktrees/safebank-nov1-w1-correction-gates/WeBank`.
Branch: `fix/nov1-w1-correction-gates`.
Scope: `services/gateway/app/utterance/parser.py`, `tests/gateway/w1/db/test_review_r1.py`, new `tests/gateway/w1/test_transfer_target_amount_boundary.py`, this record. Root maintains shared AGENTS/PRD. No L3 write, approval, execution, grant, amount-limit or credential changes.

## Two distinct causes

The original CI has 15 healthy-L3 R7 failures: `CLARIFYING != INTENT_UNCLEAR`. Its input is `不对，改成给赵六九百`, not the literal formal case `王`. Exact base parsing splits this into target `赵`, amount **90600 cents**; the new registered-name-prefix logic offers the single `赵六` candidate. It has no transfer yet, but selecting that candidate could promote the wrong amount. The prefix behavior alone is not an authorization bypass; the target/Chinese-money split is a separate sensitive production defect. Original log: `/private/tmp/sbg-freeze-ci-20261031/.ds-logs/local-ci-2b51bbe/p1-test-w1.log`, first R7 mismatch around line 660.

The old R7 test is intended to exercise a genuinely unclear replacement. Its fixture now says `给钱七900`: unknown target, explicit 90000-cent amount, no prefix ambiguity. Its original 30 healthy/outage parameterizations keep all exact assertions: INTENT_UNCLEAR, empty action kinds, FORM_REQUIRED, old authority unreadable, old token exposes no utterances, SUPERSEDED old utterance/confirmed plan/approval, preserved EXECUTING and UNKNOWN plans, retained CLAIMED ownership. No status set was widened.

## Production behavior

Transfer grammar now tries complete current-customer registered name/alias/saved phone and current-customer own-account target labels against the entire clause. It considers every legal target/amount split. Exactly one split proceeds through existing entity resolution; two different splits fail closed with empty scope. Entity ordering and name length do not select a winner. Duplicate identities with the same full phone/name still follow the existing candidate-selection rules.

A generic unresolved near/prefix name cannot provide an adjacent Chinese money token; an explicit separator such as `转`, whitespace, or the existing money-before-target grammar is required for those candidates. Even a registered short name cannot accept malformed concatenated leading Chinese digits such as `六九百`: otherwise an unknown/foreign longer name could still become `赵` + 90600 cents. This conservative check is local to transfer parsing; global `money_cents`, card/fund money parsing and all integer-cents limits remain unchanged.

`给赵六九百` with only the real complete saved target becomes `赵六` / 90000 cents. Multiple registered splits, including alias splits, remain empty scope. `给王转九百`, `给王 九百`, and `把九百转给王` still offer the actual two payees at 90000 cents and no transfer. Names ending in Chinese numerals, aliases, phones, explicit source accounts, monthly/weekly schedules and incomplete future dates share the same target parser. Quoted, mixed unknown, question, conditional and foreign-customer inputs keep their non-authorizing boundary. Bare target-only text still cannot authorize a transfer; the existing explicit-transfer/multiple-step shorthand rule is preserved.

## Reproduction and actual validation

Interpreter used: `/Users/jet/.codex/worktrees/safebank-bill-analysis/WeBank/.venv/bin/python`; every command ran in the worktree above unless otherwise stated. `GATEWAY_DB_TESTS=0`, `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider` prevent PG provisioning and incidental cache writes.

- Pure fixture guard before changing the old input: `python -m pytest -q tests/gateway/w1/db/test_review_r1.py -k fixture_has_no_candidate_authority`: **15 failed**, exact CLARIFYING/INTENT_UNCLEAR mismatch. Log `/private/tmp/sbg-nov1-repairs/w1-correction-gates-red.log`. With genuinely unknown fixture and explicit candidate-selection controls: **30 passed**, log `w1-correction-gates-green.log`.
- Final amount-boundary tests copied into a fresh exact `git archive 2b51bbe92a4b3692e1ab8557e6c2ec587b92ae5f`, run from `/private/tmp/sbg-nov1-repairs/w1-correction-base`: `python -m pytest -q tests/gateway/w1/test_transfer_target_amount_boundary.py`: **63 failed, 72 passed**, log `/private/tmp/sbg-nov1-repairs/w1-target-amount-final-base-red.log`. This final red excludes an earlier invalid single-bare-transfer expectation, which was corrected to preserve the existing grammar.
- Additional short-name/unknown-or-foreign-suffix probe against the first candidate implementation: **2 failed** (incorrect SCOPED 90600 cents), log `w1-target-short-name-red.log`; it is fixed in the final local transfer guard and included in the final test file.
- Complete W1 suite: `python -m pytest -q tests/gateway/w1`: **1870 passed, 164 skipped**, 3.67s. Log `/private/tmp/sbg-nov1-repairs/w1-correction-gates-suite.log`.
- Exact modified R7 file: `python -m pytest -q --collect-only tests/gateway/w1/db/test_review_r1.py`: **74 collected**, log `w1-correction-gates-collect.log`. This includes 30 pure tests and 44 PostgreSQL tests. New PG case exercises G3 correction → rejected empty/noncandidate G19 → accepted explicit G19 → G3 adoption, direct loader INTENT_UNCLEAR before selection, old root permanently consumed, correct fingerprint/90000 cents after adoption, and zero plan/job creation.
- `ruff check`, `ruff format`, `git diff --check`: passed for changed Python files.
- Full repository pure suite: `GATEWAY_DB_TESTS=0 AGENT_DB_TESTS=0 BANK_DB_TESTS=0 PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider`: **4913 passed, 1165 skipped**, 48.24s; log `/private/tmp/sbg-nov1-repairs/w1-correction-gates-all-pure.log`. No skipped PG test is counted as passed.

## Unrun and ownership

No real PostgreSQL, provider, API, Docker, browser, model, deployment or demonstration was run. Root must run the 44 PG cases serially, especially the original 30 R7 cases and new explicit-selection loader regression. Pure/compiled tests are not live financial acceptance. Independent safety review is required for the production parser change; B has accepted that review assignment and is waiting for a stable complete SHA.

## Independent review correction

B found the new PG-only zero-job assertion referenced nonexistent `execution_jobs.sid`. An isolated column-access repro raises `AttributeError: sid`; this was a new test expression defect, not a production parser finding. It now filters `execution_jobs.plan_id` through the current session's `plans.id` subquery, retaining both zero-plan and zero-job assertions. The corrected SQL expression compiles, the affected test module collects (74), and all W1 pure tests remain green. Real PG remains unrun by this agent; root must execute it. No production file changed in this follow-up.

## Root actual-PG authentication fixture correction

Root's full W1 actual PostgreSQL run reported **2038 passed / 1 failed** (`/private/tmp/sbg-nov1-w1-final-pg.log`). The sole failure was this new test's manually constructed F caller without `jti`/credentials: the real loader correctly returned UNAUTHENTICATED before the test expected INTENT_UNCLEAR. Both preceding old-root and new-unselected-root `cannot_load` checks had passed; the later G19→G3 assertions had not yet executed because of this mismatch.

This test-only follow-up replaces that manual caller with `caller_for(parent, public)`, obtained by verifying the actual G3-returned assistant token, and explicitly checks A kind, nonempty `jti`, assistant scope and inclusion of the exact parent ID before asserting loader INTENT_UNCLEAR/409. The source is authenticated but still incomplete. No loader/parser/production code changes; the prior cannot-load checks and every subsequent G19→G3, amount, fingerprint and zero-plan/job assertion remain. W1 pure verification/collection is repeated locally with PG disabled. Root must rerun the new single PG test, then the full W1 gate; no new actual-PG pass is claimed here.
