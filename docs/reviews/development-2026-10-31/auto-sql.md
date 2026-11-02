# Task6 automatic SQL runner independent review

Current verdict: **APPROVE** for exact correction `296b444e7fd709c81f4f5b918021a408e3257c6e`; R1/R2 below are CLOSED after the independent re-review recorded at the end.

Original review of `6bdd8a3`: **REQUEST_CHANGES** for two P2 artifact/verification-state consistency defects below. The ordinary paths and original reviewed safety gates passed the scoped review; this is not a bank authorization-bypass claim.

Candidate: `6bdd8a3c30531d7737426ba77faef1303aa4307e`  
Increment base: `2548f49e5e1ba0f368da991e5738ef19734b157b`  
Worktree: `/Users/jet/.codex/worktrees/safebank-demo-runner/WeBank`

Scope: read-only adapter/security audit and pure Mock tests. Read the task report, scoped AGENTS/PRD under the coordinator §8.7 reading exception, prior runner and oracle final independent reports, the adapter/CLI diff, and affected fixtures. No real Docker, DB, provider/API/model, demo, credential-file or deployment operation. No implementation, original test, shared AGENTS/PRD, WIP or Git mutation. Only this independent report was written.

## R1 — P2: automatic VERIFIED manifest can label a different seed than actual SQL

Locations: `scripts/demo_runner.py:527–531` and `scripts/demo_runner.py:735–739`.

`metadata()` now copies the nonempty `current.initial.seed` label into `manifest.seed`. The automatic path proves its captured bank seed is the oracle's fixed `l1_seed_v1.0`, but never compares that actual SQL value to the selected manifest label. Consequently the runner can issue a fully VERIFIED run while its provenance says a different seed was used.

Independent pure reproduction used the existing `environment` + `auto_sql` fixtures without changing either fixture or implementation. Change only the returned current-generation metadata:

```python
old = env.demo.current
env.demo.current = lambda: {**old(), "initial": {"seed": "unexpected_seed_label"}}
result = run_core(auto)
```

The SQL payloads remained the original valid `l1_seed_v1.0` before/after values, and the actual output was:

```json
{"status":"VERIFIED","business_verified":true,"manifest_seed":"unexpected_seed_label","actual_seed":"l1_seed_v1.0"}
```

This does not mean a wrong banking state bypassed the fixed-case oracle; it means the supposedly exact provenance manifest is demonstrably false and its `same_current_generation_and_seed` receipt is not reconciled with that manifest. A damaged/stale initialization label is not detected.

Narrow fix: in automatic mode, compare the actual captured clock dataset revision with the selected generation/manifest seed before invoking the driver, and refuse the batch on mismatch (or establish the manifest field from the verified actual value with an explicit selected-label consistency gate). Keep the rule in the automatic adapter; no ad-hoc new SQL or change to the approved oracle is necessary. Preserve the explicit observe/external-proof compatibility and do not turn a seed label alone into SQL proof. Add a mismatched-label negative and the matching actual-seed positive, asserting no driver/receipt on refusal.

## R2 — P2: final persistence failure leaves FAILED run marked business_verified=true

Locations: `scripts/demo_runner.py:825–832`.

`verify_receipts()` returns a copy with `business_verified=true` (and per-case flags true). The final checkpoint then runs inside the same try block. If that write fails once, the exception path changes only `status` to FAILED, successfully writes the recovery checkpoint, and retains the global true verification flag.

Independent pure reproduction wrapped `private_write` only to raise a single synthetic `OSError` when writing `manifest.next.json` with status VERIFIED; all provider/SQL boundaries stayed Mock and all case SQL checks were genuinely valid. The normal exception propagated; reading the recovery manifest produced:

```json
{"status":"FAILED","business_verified":true,"cases_verified":[true]}
```

The case SQL facts themselves were valid in this probe. The defect is that the overall failed batch still advertises the overall success boolean, contrary to this runner's documented FAILED/false contract and to consumers that use that boolean. The same code is shared with explicit-proof mode.

Narrow fix: on every run failure, reset the overall `business_verified` to false before the recovery checkpoint and keep final status/flags consistent with their documented meaning. Preserving already-written evidence/receipts is appropriate; do not delete history or retry a driver. Add a one-shot final-checkpoint failure regression plus ordinary verified and earlier-failure controls. If individual-case facts stay true intentionally, distinguish those facts clearly from the overall run flag.

## Checks that passed

- Exact reviewed oracle is byte-identical: SHA256 `6fcf6bd2f31dd8f22d1f713d55a8aaa9b4ef93e7fa18cce61aaaad9e3a8762f3`. Original119 tests retain their semantics; only import/root location is adapted.
- Automatic cases require fixed supported canonical IDs/text/customer before runtime. Before SQL is captured immediately before the actual driver; after capture is bound to the saved current trace's UUID/config and the trusted, ordered G15 request/response/session_created triplet. Model/free-text session strings are not sources.
- G15 CLI trace adds only `body.session_id` on a successful response; tokens and the whole response body are excluded. No request, auth or terminal-state behavior changed in this five-line patch.
- SQL uses the fixed approved module, query stdin, validated generation/database names, `psql -X`, `ON_ERROR_STOP`, and explicit `PGOPTIONS=-c default_transaction_read_only=on` in the Postgres service. No admin credential is sent to L1/BFF or echoed.
- Raw sensitive SQL snapshots are refused rather than changed into different financial facts. Typed authorization_epoch int/NULL and otp_verified bool/NULL are preserved; credential-bearing fields/text fail before an evidence file or driver call. Before/after and receipts use exclusive private writes; missing/failed SQL and oracle mismatch stop the next case and retain partial evidence without issuing the failing receipt.
- Existing nonce/UUID trace identity, quota75/timeout124 immediate batch stop, offline-network preflight/probes, malformed-secret rejection, observation-only false status, explicit receipt verification and unsupported-case failure guards remain present and covered by the affected tests.

## Independent verification

Fresh command on the exact candidate:

```text
PYTHONDONTWRITEBYTECODE=1 GATEWAY_DB_TESTS=0 OBLIGATIONS_DB_TESTS=0 \
AGENT_DB_TESTS=0 GATEWAY_SECRET_TESTS=0 /Users/jet/Downloads/WeBank/.venv/bin/pytest \
-q -p no:cacheprovider tests/test_demo_runner.py tests/test_demo_sql.py \
tests/try_agent tests/demo_environment tests/test_demo_deploy_path.py
309 passed in 4.80s
exit0
```

Log: `/private/tmp/sbg-auto-sql-independent.log`.

The two additional inline probes used the unchanged existing fixtures and temporary synthetic directories under `/private/tmp`; both produced the exact inconsistent outputs above. No checkout/original test file was modified. Reproduction script setup loaded `tests/test_demo_runner.py` by importlib and called `runner.__wrapped__()`, `environment.__wrapped__(runner, tmp_path)`, and `auto_sql.__wrapped__(environment)`; it then invoked the existing `run_core(auto)` helper. These are actual behavior probes, not setup-error red counts.

## Remaining coordinator gates

After the two narrow fixes and independent incremental re-review, combined exact-head development CI still belongs to the coordinator. Actual runtime/schema/six-case/offline demonstration verification is outside the user's current development-only scope and is not claimed passing here. The previously completed real Bill SQL proof is not attributed to this reviewer or extrapolated to the remaining cases. No freeze/tag is certified.

## Final incremental re-review — APPROVE

Correction: `296b444e7fd709c81f4f5b918021a408e3257c6e`, increment base `6bdd8a3c30531d7737426ba77faef1303aa4307e`. Worktree is clean. Approved oracle byte SHA256 remains `6fcf6bd2f31dd8f22d1f713d55a8aaa9b4ef93e7fa18cce61aaaad9e3a8762f3`.

R1 is CLOSED: `require_declared_seed` compares the captured `clock.dataset_revision` with the actual selected manifest label. The before gate runs after retaining its safe raw snapshot but before any driver; the after gate runs before gateway-after SQL or a receipt. A matching actual seed is retained. The fixed SELECTs and oracle were not changed; observation/external-proof modes preserve their explicit compatibility.

R2 is CLOSED: every caught run failure now clears the global and all case verification flags before the recovery checkpoint. If the prior checkpoint left `manifest.next.json` (including partial bytes), recovery selects a new exclusive random temporary path, preserving the original fault artifact and already captured traces/snapshots/receipts. Normal final VERIFIED writes still retain true flags. No driver retry or history deletion was added.

Independent fresh verification:

- Exact corrected candidate: the same full affected command recorded above completed **317 passed in4.54s**, exit0. This includes matching-seed six positives, before/after seed mismatch stops, automatic and external-proof final-write failure with and without a partial file, successful final persistence, observation-only, quota/timeout, session binding, credential protection, lifecycle and original119 oracle tests. Log: `/private/tmp/sbg-auto-sql-independent-rereview.log`.
- In an isolated Python process, loaded only the original `scripts/demo_runner.py` from exact `6bdd8a3` via `git show` into its imported module namespace, without modifying checkout files, and ran the eight new targeted cases. Result: **6 failed /2 passed /99 deselected**, exit1; the original before/after seed and all four final-write variants fail, while the two normal persistence controls pass. Log: `/private/tmp/sbg-auto-sql-independent-old-red.log`.
- Scoped ruff check, format check, and correction diff whitespace check passed. The initial format check attempted a protected worktree cache and failed before checking; reran with `RUFF_CACHE_DIR=/private/tmp/sbg-auto-sql-review-ruff`, which passed2 files. This was an environment/cache setup error, not a test regression.
- Final HEAD, clean status and oracle hash were independently rechecked. No implementation or original test was changed by this reviewer.

No new blocking finding in the correction or reviewed adapter delta. This approves the development code only: no real SQL, Docker, API/model call, rehearsal/recording/offline demonstration or deployment was executed. The coordinator's combined exact-head automated CI and any separately authorized real acceptance gates remain outstanding; no freeze/tag is certified.
