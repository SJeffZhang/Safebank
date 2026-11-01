# Independent review: W1/W2 bound-hint test repairs

**APPROVE — test-only scope, no actionable finding.** 2026-11-01.

Base: `2b51bbe92a4b3692e1ab8557e6c2ec587b92ae5f`.
Reviewed exact commits: `4a490c88de86af3bfe9e0a17af2a75324a4960b3`, then `af1f096d3935ac517dd6671bef294c8e9006e54a`.
Exact `git archive` checkout: `/private/tmp/sbg-nov1-repairs/review-bound-hints-af1`.
No production file changed: the six-path cumulative diff contains five tests/fixtures and one acceptance record. No author worktree or WIP was modified.

## Source binding and financial boundary

The shared fixture (`tests/gateway/w1/db/bound_hint_fixture.py`) calls the real `_persist_plan`, `append_evaluation`, `publish_replan_hints`, then `validate_hint_source`. It uses one complete current 800-yuan transfer root and changes only its amount to 300 yuan. It does not insert a hint binding/digest directly, monkeypatch source verification, or change production authorizers. The real publisher requires a current ACTIVE/SCOPED, unexpired, same-sid/customer/epoch root with adopted provenance, saved REPLAN plan/evaluation, and strict narrowing; it computes the private commitment and normalized label itself. The helper returns an unused source and verifies that commitment before the caller exercises G3 adoption.

The fixture deliberately seeds saved synthetic REPLAN state: its 800-yuan insufficient-funds finding is not a claim that real G4/preflight produced that result from the fixture's bank snapshot. The fresh-root variant inserts an explicitly named test-owned typed root to supply valid source input to a lower-level unresolved-job barrier. It does not assert that production G3 can create such a root over an unresolved job. This is suitable isolation for the three repaired lifecycle/provenance tests, and that limitation is explicitly documented. It is neither a new public source nor creation consent for a financial operation.

## Preserved regression contracts

- W1 live replacement retains its existing genuine typed 800-yuan root. It now adopts a strict 300-yuan hint via G3. Original `used_by == adopted utterance`, unchanged expiry, and old job UNKNOWN assertions remain. Additional checks require old root SUPERSEDED and original CLAIMED ownership retained. The fixture only adds a separate saved REPLAN parent and evaluation; it does not change the seeded old job, evidence or claim.
- W1 deferred test retains the original CLARIFYING parent, unresolved orphan job, and each typed/hint/action/slots variant. Only the hint variant gets an independent complete test root. The old count-one assertion becomes an exact set containing the original root plus the known extra roots, with every root SUPERSEDED after each rejected request. Both attempts still require the exact deferred STATE_CHANGED/error details, canceled original clarification, unused/expired UI sources, and an empty renewed assistant scope. The added complete `submission` equality covers the old plan, job, claims and evaluations after both hint attempts. No old root is made ACTIVE, no orphan job/claim is removed by the new helper, and no replacement utterance is allowed.
- W2 positive adopts the bound hint through real G3/JWT, then `load_for_plan` and entity/provenance/intent stages. It keeps exact 30000-cent output, user trust and intent match, adding stored `used_by`/expiry assertions. The separate unadopted manual hint remains unknown trust with UNKNOWN_SENSITIVE_ARG. The entire wrong-used-by negative function is AST-identical to base; cross-sid/customer and correction/logout tests are outside the diff.

## Independent verification actually run

All commands used the absolute shared Python environment, cwd the exact archive, `GATEWAY_DB_TESTS=0`, `AGENT_DB_TESTS=0`, `BANK_DB_TESTS=0`, `PYTHONDONTWRITEBYTECODE=1`, and `-p no:cacheprovider` where pytest was invoked.

1. Author's targeted pure suite, rerun independently: `python -m pytest -q tests/gateway/w1/test_bound_hint_fixture.py tests/gateway/test_hint_publication_unit.py` — **37 passed**, 1.23s. Log: `/private/tmp/sbg-nov1-repairs/review-bound-hints-pure.log`.
2. Private independent tests: `python -m pytest -q tests/test_review_bound_hints_independent.py` — **16 passed**, 1.08s. Seven pre-publication root negatives cover stale epoch, expired/future root, untrusted echo, foreign sid, and unsupported hint/action sources. Eight post-publication controls reject used-by mismatch, increased amount, changed target, epoch, digest, parent, evaluation and expiry. A SELECT-filtering in-memory SQL boundary separately retains an old SUPERSEDED/CLARIFYING root, EXECUTING job, CLAIMED ownership and old UI rows alongside two fresh roots: the real fixture/publisher completes without UPDATE/DELETE, and every old row remains byte-for-byte unchanged. This improves on the author's deliberately single-row stand-in without treating either stand-in as PostgreSQL.
   Probe: `/private/tmp/sbg-nov1-repairs/review-bound-hints-af1/tests/test_review_bound_hints_independent.py`.
   Log: `/private/tmp/sbg-nov1-repairs/review-bound-hints-independent.log`.
3. Four affected selectors (live hint replacement, all four deferred source variants, W2 positive, W2 wrong-used-by negative): **7 tests collected**, 0.61s. Collection only. Log: `/private/tmp/sbg-nov1-repairs/review-bound-hints-collect.log`.
4. Ruff check on all changed Python files: passed. Exact git-diff/AST inspection confirmed the test-only boundary and unchanged wrong-used-by negative.

## Limits

No PostgreSQL, actual service/API, provider, model, Docker, browser or deployment was run. Root must execute the affected PG tests and full exact-head CI serially. These pure source/lifecycle checks do not establish a real financial preflight, real G4→G9 execution or a new live demonstration result. Approval applies to these two exact test-only commits and preserves those required gates.
