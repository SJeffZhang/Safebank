# W1 valid hint fixtures for replacement and deferred job gates

2026-11-01. Base `2b51bbe92a4b3692e1ab8557e6c2ec587b92ae5f`; branch `fix/nov1-w1-hint-gates`. Test-only change. No production code, policy, authorization source, grant, job state machine or JWT behavior changes.

## Observed baseline

Root's actual W1 CI (`p1-test-w1.log` under `/private/tmp/sbg-freeze-ci-20261031/.ds-logs/local-ci-2b51bbe/`) reported two legacy hint fixtures failing with AUTHORIZATION_CANCELLED: `test_hint_replacement_retains_the_newly_adopted_source` and `test_new_request_cannot_skip_unresolved_job_or_publish_replacement[hint]`. Their sources were manual unbound rows, including a no-op amount or a later CARD_FREEZE rewrite. The new source gate correctly rejects them. These are stale test inputs, not evidence of a production source regression. The root-run real G4/G22a/G3/next-G4/G9 positive chain is covered separately by the eight T5 PG cases.

## Fixture semantics

The shared test helper creates a complete saved REPLAN plan/evaluation for one live SCOPED 800-yuan transfer root and invokes the production `publish_replan_hints` then `validate_hint_source`. It produces a strict 300-yuan shrink with original target/source/scope fields unchanged, real private commitment and five-minute wall-clock expiry. It does not manually manufacture a digest or place binding fields in public scope. The synthetic financial evaluation is state setup for lifecycle tests; it is not a claim that the bank snapshot actually failed preflight, nor a substitute for real G4 acceptance.

The first test reuses its existing complete root, preserving source `used_by`/expiry and old job UNKNOWN assertions, and adds exact shrink, old-root supersession and retained claim checks.

The deferred test keeps its original CLARIFYING parent and unresolved orphan job. That parent cannot publish a hint. Only the hint variant receives a separately named, test-owned current SCOPED root. After the first deferred response, the next attempt receives another independently named valid root/source; neither old root is revived. Exact root-ID sets replace the former count-one assertion for this variant. Both attempts must still return the original deferred STATE_CHANGED response, preserve the complete old submission snapshot, expire unused sources without setting `used_by`, supersede the explicit roots, cancel the original clarification, publish no replacement utterance, and renew an empty assistant scope. Typed/action/slots setup and original assertions are preserved.

These extra roots deliberately supply valid inputs to the unresolved-job barrier under synthetic state; they do not claim G3 would grant new roots over an unresolved job in production. No old job/claim is deleted to make the test pass; the pre-existing test-owned orphan teardown remains unchanged.

## Worker verification and root gate

- Pure fixture/publisher controls: **37 passed** (1.24s; five new fixture controls plus 32 existing publisher controls). They prove valid existing/fresh roots, strict shrink, no existing-root mutation, and refusal of SUPERSEDED/CANCELLED/CLARIFYING roots.
- Exact affected PG selectors: **5 tests collected** (0.61s), including all four source variants. Collect only; actual execution belongs to root. The worker ran no database, API, model/provider, Docker or browser.
- Ruff check/format and `git diff --check`: passed.
- Root must run the two exact PG selectors (including the four source variants of the deferred test), then full W1 and final exact-head CI. No PG pass claim is made by this checkpoint.

## Same-source W2 integration fixture follow-up

Root's remaining-target diagnostic found one matching stale positive source in `tests/gateway/w2/db/test_real_w1_integration.py::test_adopted_hint_used_by_resolves_and_unadopted_is_rejected` (`/private/tmp/sbg-nov1-diagnostic-test-w2.log`). Only that positive 800→300 hint seed now uses the same production publisher helper and existing real typed root. The test still goes through real G3 adoption/JWT, `load_for_plan`, entity/provenance/intent stages, and now explicitly checks saved `used_by`/expiry. Its separate never-adopted hint remains a manual untrusted source and must have unknown trust/UNKNOWN_SENSITIVE_ARG; the separate wrong-used-by negative test is unchanged. This is not a generic trust exception.

Worker validation: 37 related pure checks passed; the positive W2 and separate wrong-used-by PG tests collected (2), not executed. Ruff/diff checks passed. Root owns actual W2 PG and final full-head gates.
