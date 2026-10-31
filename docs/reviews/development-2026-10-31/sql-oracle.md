# Core SQL oracle independent review

Final verdict: **APPROVE** for the scoped pure validator at SHA256 `6fcf6bd2f31dd8f22d1f713d55a8aaa9b4ef93e7fa18cce61aaaad9e3a8762f3`. R1–R4 and the confirmation-time consistency gap are closed. This is code-review approval, not actual SQL/six-scene/offline acceptance or freeze authorization. Historical findings and repair evidence follow.

Reviewed artifacts (unchanged during review):
- `/private/tmp/sbg-freeze-continuation/core_sql_oracles.py`, SHA256 `35c9aaf7bcc5e91198cd4fa7c0bcdff08b89fd996da828006d448c66a80165ba`.
- `/private/tmp/sbg-freeze-continuation/test_core_sql_oracles.py`, SHA256 `28948913e78dfc7a7bab37dde7d117a2d900babf1c5248dda8126d988d9f26e7`.

## Scope and positive checks

Read current AGENTS1–3/8 under the coordinator's task-scoped §8.7 ruling, relevant PRD5.1–5.6/6.7, the complete helper and tests, actual bank/gateway models, bankcore analytics/standing/banking/funds rules, PlanDraft/InteractiveEvidence contracts, gateway provenance and approval audit construction. No implementation, shared AGENTS/PRD, database, Docker, provider, secret file, git commit or deployment changed.

The query builders are fixed standalone SELECTs. The only interpolated session ID is whitelist validated and cannot contain quotes/separators. Selected columns exist in the actual SQLAlchemy metadata (independently exercised by the existing tests). Queries omit account numbers, password fields and OTP value/digest/ciphertext. Fields included from execution evidence contain authorization IDs and hashes, not a provider credential.

The common checks reject wrong DB generation/seed, mismatched metadata, unbalanced ledger, missing tables, duplicate row identities, changes to append-only history and unspecified changes in captured financial tables. Fixed cases independently enforce expected money/share movements and intended entity IDs. Bill facts are recomputed from raw transactions; idle classification correctly identifies only gym19900 from usage_expected and 60/120-day history. Unsupported43/clock scenarios are explicitly rejected and are not defects in this scoped component.

## R1 [P2] Actual authorization IDs and RED OTP send view are not bound to the current case

Locations: `core_sql_oracles.py:588–590`, `:623–631`; gateway plan projection at `:241` does not currently select the persisted plan's `utterance_ids` separately, though full `plan_draft` also normally contains them.

The bank execution evidence's `scope_ids` and `utterance_ids` are only tested for truthiness. On each author good fixture for01/34/36/23, replacing both with `['utt_other_session']` leaves all actual current-case gateway utterance rows unchanged, yet `verify_case` returns `verified=true`. The claimed execution-bound receipt therefore does not prove the original authorization references belong to this case/session or agree with the plan.

The RED sent-audit filter similarly matches plan/approval but ignores its already-selected `view_hash`. Changing the sole OTP_SENT audit to an unrelated view still yields a green case23 receipt. `APPROVAL_CONFIRMED.otp_verified=true` remains checked, but this does not establish that the selected send audit belongs to that view.

Narrow fix: bind evidence authorization IDs to actual current-session typed authorization row IDs and persisted plan authorization references (for these default cases, the exact canonical single typed request); reject nonexistent, extra, duplicate or other-session IDs. Match RED send audits to the actual confirmed/approved view. Keep the existing evaluation and confirmation checks. Do not add OTP contents to snapshots.

## R2 [P2] Case36 accepts incomplete derived-source evidence that the real gateway rejects

Location: `core_sql_oracles.py:949–963`.

The helper only requires each supplied leaf to refer to any cancellation step or any session read. It does not require both leaf types or all cancelled steps. The author's current `good('36')` fixture is step-only and contains no read leaf or read record, yet gets a green receipt. Replacing it with only an accounts-read leaf (and no cancellation step leaf) also gets a green receipt.

The real `services/gateway/app/pipeline/provenance.py:331–343` explicitly requires `leaf_steps == set(cancellations)`, nonempty leaf steps and nonempty trusted reads for `cancelled_monthly_fees`. Thus the helper's green fixture cannot demonstrate that actual required provenance shape. This is material to the claimed same-plan derived-saving evidence even when the independently checked final amount happens to be19900.

Narrow fix: require all actual cancellation steps and at least one actual resolvable trusted read leaf, with valid source shape/reference. Use a truthful good fixture containing actual subscription-read evidence plus every cancellation. Retain the independent raw-SQL idle-set/fee and own-saving target checks; do not substitute generic equality to arbitrary scope values. Avoid adding tighter pointer semantics than the approved gateway actually implements without a separate design decision.

## R3 [P2] Contradictory duplicate idle items are overwritten before comparison

Location: `core_sql_oracles.py:778–788`.

The dictionary comprehension silently collapses repeated instruction IDs. In the case27 good fixture, prepending another `is_idle=true` item for `si_zs_gym` with `typical_amount_cents=1` before the existing19900 item still issues a verified receipt. The actual read contains conflicting results but the last item hides the first.

Narrow fix: validate the relevant item identities/types and reject duplicate instruction IDs before constructing the comparison mapping; compare the exact idle set/amounts to independent SQL facts. Keep legitimate non-idle subscription rows permissible.

## R4 [P2] Operation rows can be misattributed while still passing exact-count checks

Location: `core_sql_oracles.py:684–690`.

`_operations` validates only action-kind/amount multiset and plan_id. On the case01 good fixture, replacing the new operation's `customer_id` with `cus_ls`, `step_id` with `s99`, and target with `another-payee` still yields a green receipt despite the persisted event being attributed to the wrong customer/action target. These columns are already captured and are used by workflow/audit state; same count and amount do not bind the event to the actual executed action.

Narrow fix: compare operation customer, step and deterministic target to the corresponding actual plan step/entity for each supported write case, in addition to kind/amount/count/plan. Construct truthful good fixtures using actual bankcore target conventions. This does not call for new financial rules or broader scenario coverage.

## Independent verification

1. Existing suite, fresh run:
   `PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -c pyproject.toml -q -p no:cacheprovider /private/tmp/sbg-freeze-continuation/test_core_sql_oracles.py`
   → **63 passed**, exit0,0.45s. Includes both bank/gateway metadata checks without a database connection.
2. Extra independent probes at `/private/tmp/sbg-freeze-continuation/test_oracle_independent_review.py`:
   - four stale authorization-ID variants;
   - wrong OTP send view;
   - step-only and read-only derived leaves;
   - contradictory duplicate idle item;
   - wrong operation attribution.
   Same pure pytest invocation → **9 failed**, exit1,0.07s, each because the expected `OracleError` was not raised. Original implementation and original tests were untouched. Log: `/private/tmp/sbg-freeze-continuation/core-oracles-independent-probes.log`.
3. Separate direct calls confirmed each counterexample actually returned `verified=true`; failures are not fixture/import/setup errors.
4. Artifact checksums above match the implementer's delivery report after all probes.

## Remaining coordinator gates

After these specific checks close, rerun original and independent pure tests, then execute the actual SELECTs against the migrated stack. Root remains responsible for real before-capture timing, checkout/image identity, actual session/trace binding, SQL adapter integration, two fresh six-scene rounds, detailed full43/clock coverage and actual blocked-network replay. This review does not convert pure payload tests into live SQL acceptance or authorize a freeze/tag.

## Re-review1 — original gaps closed; actual confirmation time still unbound

Reviewed module SHA256: `f2af7570507e06b5592ec5107451772bfe0a8f3a8588f258a5fea035581996fb`. Implementation and original tests remained untouched by this reviewer.

- Original R1 ID/view issues: **CLOSED**. Evidence, persisted plan/draft IDs, current typed row, session, epoch, consumed claim and RED OTP send view now agree. Exact canonical text is checked by SQL SHA256 without selecting raw text. The NULL agent-plan epoch exception is preserved, while contradictory non-NULL values fail. I separately verified all four write positives with `plans.authorization_epoch=None`.
- R2: **CLOSED**. The corrected good36 fixture contains both an actual cancel-step leaf and fresh trusted read leaf. I updated my independent negative test to explicitly retain only step leaves or only read leaves from that truthful fixture, preserving the remaining leaf metadata, rather than relying on the old malformed good fixture. Both cases now reject. The original probe was retained as `test_oracle_independent_review_v1.py`. The new checks resolve pointers, taint, sid, versions and time without inventing stricter tool semantics than the approved gateway.
- R3: **CLOSED**. Duplicate instruction IDs and invalid strict item amounts/types are rejected before mapping; legitimate non-idle rows are retained.
- R4: **CLOSED**. Operation rows now match the actual customer, step, deterministic target, action and amount multiset. New positives use the actual bankcore conventions, including null target for lost-card and persisted payee fingerprint for transfer.

Fresh pure test command over the author's suite plus the updated independent9: **119 passed**, exit0,0.72s. Log: `/private/tmp/sbg-freeze-continuation/core-oracles-independent-rereview.log`.

### Remaining R1-time [P2]

Locations: `_execute` still only checks `approval.confirmed_at_wall` truthiness (line601); `_authorization_binding` uses only `evidence.confirmed_at_wall` for the new typed-row validity check (line726). The actual selected approval timestamp is never compared with that evidence timestamp.

Concrete mutation of the otherwise-valid01 fixture:

```python
before, after = good('01')
after['gateway']['approvals'][0]['confirmed_at_wall'] = '2099-01-01T00:00:00+00:00'
verify_case('01', before, after, meta('01'))  # currently verified=true
```

The bank evidence's2026 timestamp remains inside the typed row lifetime, so the checker declares the authorization live at confirmation despite the actual captured approval confirmation being far after expiry. This is a narrow consistency failure in the newly claimed actual-confirmation binding.

Required narrow repair: parse and compare the actual approval confirmation instant with the bank evidence confirmation instant, then use that bound instant for the existing lifetime check. Equivalent timezone offsets must be accepted as the same instant. Do not require a non-NULL agent plan epoch or add OTP contents.

The independent test file now adds this explicit negative and an equivalent-timezone positive. Fresh run: **1 failed /10 passed**, exit1; the sole failure is the missing `OracleError` for the expired actual confirmation. Original nine refusals and timezone positive pass. Log: `/private/tmp/sbg-freeze-continuation/core-oracles-independent-rereview-time.log`.

No real SQL/DB/Docker/provider calls were made. Root still must run the updated SHA256/session/claims/version SELECTs against actual migrated PostgreSQL and run real six-case positives; pure payload verification does not certify those gates.

## Final re-review — APPROVE

Reviewed exact module SHA256: `6fcf6bd2f31dd8f22d1f713d55a8aaa9b4ef93e7fa18cce61aaaad9e3a8762f3`. The remaining R1-time gap is **CLOSED**. At lines661–666, `_execute` parses the actual selected approval confirmation and bank execution evidence confirmation with `_dt`, requires datetime-instant equality, and passes the bound actual instant into `_authorization_binding`. Lines730–734 use that same value for the typed authorization lifetime check. Equivalent timezone offsets remain valid. Agent-plan NULL epochs remain supported; no new query, authorization rule or OTP-content selection was introduced by this last repair.

Independent fresh verification over the complete current author suite and my unchanged current independent11:

```text
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -c pyproject.toml -q -p no:cacheprovider \
  /private/tmp/sbg-freeze-continuation/test_core_sql_oracles.py \
  /private/tmp/sbg-freeze-continuation/test_oracle_independent_review.py
130 passed in 0.62s
exit0
```

Log: `/private/tmp/sbg-freeze-continuation/core-oracles-independent-final-review.log`. The prior exact f2af757 module independently produced the specific time mismatch failure while the equivalent-timezone control passed; the current exact module passes both and all original R1–R4 probes. I did not change either the implementation or author tests. The sole independent probe update during re-review was the already-documented truthful step/read leaf removal and added time consistency probes.

No real SQL, DB, Docker, provider or secret access occurred. Root must still execute the updated SELECTs on actual migrated PostgreSQL, including SHA256/session/claims/version projections, run actual six-case positive captures, and complete runner integration plus exact-head CI/live/blocked-network gates. These unrun gates are not counted by the130 pure passes. Shared repository work and any unrelated output remain untouched.
