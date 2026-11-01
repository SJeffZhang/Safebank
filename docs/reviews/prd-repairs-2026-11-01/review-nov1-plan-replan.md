# November 1 independent plan / REPLAN safety review

Latest conclusion: **APPROVE for d6a5df11d67baaa41c47862c11ab7c24e767f1cf** after the two P2 corrections. The original11c3 REQUEST_CHANGES and red evidence below remain preserved as history.

**REQUEST_CHANGES** for exact target `11c3c51a714efee39cb6b4b49e87f21feb99bf44` (prior component `001c9304e717c3cb20b176d4e19f7a0a139070fd`, base `aa7e5d26160ef3be1d7b9341a0398b2f0390d0b1`). Two output-integrity findings below need correction. They are not evidence of unauthorized execution or bank writes.

## Archive and commands

Target and base were exported with `git archive` into `/private/tmp/sbg-nov1-repairs/review-write` and `review-write-base`. Product files were not modified. Review scope: agent `llm/plan.py`, `llm/verify.py`, gateway `pipeline/preflight.py` and their tests/acceptance. Reviewer did not implement them.

From the target archive, using the pre-existing local Python environment and `PYTHONDONTWRITEBYTECODE=1`:

- `python -m pytest tests/agent/test_nov1_x4_plan_retry.py tests/agent/test_nov1_replan_receipt.py tests/gateway/w3a/test_nov1_replan_limits.py -q -p no:cacheprovider`: **37 passed in 1.44s**, log `review-write-target.log`.
- Independent negative suite: `python -m pytest -c pyproject.toml /private/tmp/sbg-nov1-repairs/test_review_plan_replan.py -q -p no:cacheprovider`: **4 failed / 11 passed in 1.08s**, log `review-write-independent.log`.

The independent suite is outside the product archive; failing tests are preserved for implementer reuse. No DB, API, live model, browser, Docker, secret/environment file, or demo-generation operation was performed. This is not exact-head full CI or live case37/40 acceptance.

## Required findings

### [P2] Bind changed amounts to actual failed step IDs

`services/agent/app/llm/verify.py:283` checks only REPLAN code/resolver. The original transfer tuple at :313 does not retain step_id, and changed amounts are accepted at :350–360 without requiring that their step is the failed step. Thus a structurally valid scope can produce a confident numeric maximum for an unrelated step while the true failed step remains unchanged.

Independent reproductions:

1. Change the sole finding.step_id to `not_this_plan_step` or null; it still says original step1 suggests at most1000.00 yuan. Both negative tests fail.
2. Original plan has step_one50,000 yuan / pye_wang and step_two70,000 yuan / pye_zhao. Only step_one has CARD_SINGLE_LIMIT. Hint retains step_one's original5,000,000 cents and reduces only step_two to30,000 cents. The verifier says step2 suggests at most300.00 yuan, despite no REPLAN finding for step2 and no remedy for the actual failed step. The negative test fails.

Required direction: retain and validate original step IDs, require every REPLAN finding to locate a legitimate supported original step, and require every changed suggestion to correspond to its relevant failed step(s). Missing, unsupported, or inconsistent finding bindings should keep deterministic reasons but omit numeric maxima. Do not change source/target/amount authorization or invoke a model for repair.

### [P2] Expired hints still become current maxima

`verify.py:317` iterates hints without checking expires_at_wall. An otherwise valid hint dated `2000-01-01T00:00:00Z` still generates “建议最多1000.00元…重新确认”. `test_review_expired_hint_is_not_a_current_maximum` fails. The output newly treats this expired suggestion as current financial guidance, although actual hint adoption/authorization can remain rejected by L2.

Required direction: only a well-formed, timezone-aware, currently live hint may contribute a numeric maximum. Keep generic reasons when stale/malformed. This requires wall time for authorization TTL, independently of simulated business-time calculation. Tests should freeze/otherwise control the authorization clock and keep normal valid-hint fixtures future/live. No need to extend any deadline or fabricate a new hint.

## X4 feedback / retry boundary: acceptable in reviewed scope

`plan.py:1039–1079` describes only fixed known entity-field locations and literal permitted shape examples. It does not insert proposed account IDs, amounts or other rejected values. The existing one-retry loop is unchanged; converter/Pydantic/read-source checks still run on regenerated proposals. Tests cover bare/null/malformed entities, malicious rejected values, unavailable/wrong provenance, retry exhaustion, external payees, malformed tool shape and immediate402/403/429 propagation. No new direct target inference or automatic target repair was found. Scripted transport results are not proof that the production model will recover formal X4.

## Other REPLAN facts and negative controls

Existing current plan/session checks, strict cents parsing, target-fingerprint matching, original-amount upper bounds and malformed SELF fields behaved as intended in the scoped tests. The numeric REPLAN path does not invoke the repair model or mutate state. My independent controls passed for missing payee reads, ambiguous duplicate target steps, wrong SELF target, extra free-map field and no real structured hint, including malicious free-text maximum drafts. Those cases stayed nonnumeric with zero model calls.

**Nullable source is not a finding:** scope-level/per-transfer source fields are legitimately nullable in the existing wire contract and canonical scope semantics. I observed their permissive matching but do not classify null by itself as missing-authority evidence or require schema tightening. An explicitly conflicting source remains rejected; preserve legitimate SELF/default-source behavior.

The verifier is a display boundary. Existing gateway authorization, state/version checks, hint consumption, confirmations and execution remain necessary. These findings do not demonstrate financial execution bypass.

## Full-plan preflight / maximum constraints: acceptable in reviewed scope

`preflight.py:272–319` checks prefix success, derives the amount from authoritative reserve projection, intersects applicable single/day/customer-day limits for external transfers, retries the entire candidate plan through shared bankcore rules, and rejects a proposal if execution fails or seven-day total shortfall grows. It does not mutate the original snapshot/plan.

The author's targeted pure tests cover balance/reserve/day/customer limits, unavailable obligations and later-step failure. My independent preflight controls all passed:

- no-card account limits are used (123400-cent single limit), with unchanged original state;
- prior-step consumption constrains a later hint to495500 cents;
- freeze followed by SELF transfer preserves a595500-cent reserve-bound suggestion even with external/day/customer counters exhausted;
- protective freeze alone is not rejected because an existing reserve deficit already exists;
- SELF transfer reducing an existing deficit does not create SCHEDULED_SHORTFALL;
- exhausted customer hard-limit budget produces no positive hint;
- each accepted independent candidate was reapplied to the full plan with no bankcore error and no increased projected shortfall.

If another unchanged later step still fails, the implementation conservatively omits a suggestion rather than solving a new multi-variable plan. That is a safe limitation, not proof that no feasible smaller alternative exists. Non-transfer hints remain outside the new transfer-specific validation path and current DecisionStage only emits transfer shrink hints.

## Handoff

Root and implementer `/root/nov1_write_path_audit` received the exact negative file and failure descriptions. Product archive and red evidence remain untouched for follow-up verification. Re-review the new full SHA after the two bindings are corrected; do not reinterpret the four red tests as bank-state failures or claim full submission acceptance from these pure checks.

## Follow-up review — d6a5df11d67baaa41c47862c11ab7c24e767f1cf

Requested new-head review started after the parser review. Original11c3 archive and all four red controls above are preserved. New exact archive: review-write-fixed. Results are recorded in the closure below.

### New-head closure: APPROVE

Inspected only the new exact-head diff from11c3. `_replan_receipt` now resolves every REPLAN finding.step_id to an actual transfer in the original plan and requires the set of changed amount-step indexes to equal the failed-transfer set. Unknown/null/non-transfer findings and shrink-only changes to unfailed steps return the deterministic reasons without a numeric maximum. It also parses hint wall expiry, rejects malformed or naive timestamps and any instant not strictly later than the current aware UTC wall clock. Simulated business dates do not extend hint TTL. Nullable source semantics and SELF target matching are retained; gateway authorization rules and preflight were not weakened.

From `/private/tmp/sbg-nov1-repairs/review-write-fixed`:

- New-head author tests for X4/REPLAN/preflight plus my original15 independent controls: **60 passed in1.36s**, log `/private/tmp/sbg-nov1-repairs/review-write-fixed.log`. The four preserved original reds all pass on the new SHA.
- Four additional independent controls: **4 passed in0.52s**, log `/private/tmp/sbg-nov1-repairs/review-write-followup-controls.log`. They freeze the verifier wall clock, reject expiry equal to now, reject the same boundary represented in +08:00, accept a genuinely later aware instant, and reject a non-transfer failure borrowing a transfer hint. File: `/private/tmp/sbg-nov1-repairs/test_review_replan_followup.py`.

The original two P2 findings are closed for this exact SHA. Scoped independent result is APPROVE, with no new actionable finding. This is not a rerun of default all-project tests, PG/CI, production model, formal case40, or actual banking execution. Root retains those integration and precise-head release gates. No product archive, original red log, root WIP, AGENTS/PRD, DB, provider, browser or demo state was changed in the re-review.
