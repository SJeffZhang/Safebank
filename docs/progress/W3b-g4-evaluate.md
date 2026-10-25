# W3b G4 Evaluate — Progress

## Completed

- [x] B1: RISKY_SEQUENCE cooldown atomicity — pipeline collects into `pending_cooldowns`, `_persist_cooldowns` writes inside same transaction as `plans.py:334-335`
- [x] B1-red: Idempotency conflict → no residual cooldowns from failed attempt
- [x] B5: lock-order validation — G6/G7/G10 all lock session→plan→evaluation→approval; concurrent G6+evaluate test passes
- [x] B5-green: `test_lock_order_endpoint.py` — 3 ASGI HTTP-level GREEN tests (G6 confirm, G7 reject, G10 otp_send) prove session-first serialises cleanly with concurrent create_for_evaluation
- [x] B5-red: `test_lock_order.py` — helper-function-level RED test proves approval-first ordering produces ABBA deadlock (GREEN test catches the regression)
- [x] B5-cleanup: Removed unused test hooks (`_test_after_first_lock`, `_test_resume`, `_b5_wait_threading_event`) from approvals.py; G6 reverted from approval-first to session-first
- [x] B5-guard: `test_lock_order_guard.py` — AST-based source-level guard asserts `_lock_session` < `_lock_approval` in G6/G7/G10 (and nested closures). RED verified: swapping each endpoint's lock calls made the guard fail with the specific endpoint name; reverted. Deadlock semantics proven by `test_lock_order.py`.
- [x] T4.3: RISKY_SEQUENCE detection → DENY + cooldown persistence (29 real-PG tests, one per scenario)
- [x] T4.4: INSUFFICIENT_FUNDS → REPLAN with replan hints

## Test coverage (29 tests, 0 skipped, 0 xfail)

- `test_f_path_returns_503` — F caller → 503
- `test_f_path_no_service_token_returns_401` — no service token → 401
- `test_f_path_no_jwt_returns_401` — no JWT → 401
- `test_evaluate_transfer_allowed` — full provenance + entity → REQUIRE_APPROVAL
- `test_evaluate_idempotent_request_id` — same request_id → cached 200
- `test_evaluate_request_id_conflict` — same request_id, different body → 409
- `test_evaluate_invalid_plan_draft_returns_422` — malformed → 422
- `test_evaluate_missing_request_id_returns_422` — no request_id → 422
- `test_evaluate_session_ownership_mismatch` — sid mismatch → 404
- `test_evaluate_utterance_not_in_jwt` — utterance not in JWT → 409
- `test_evaluate_session_revoked` — revoked session → 401
- `test_evaluate_session_not_found` — no session → 401
- `test_evaluate_produces_audit_event` — audit event created
- `test_evaluate_shadow_b1_present` — B2 produces shadow_b1
- `test_evaluate_unauthenticated` — no auth → 401
- `test_evaluate_wrong_kind_service_token` — S token → 401
- `test_risky_sequence_deny_with_cooldown` — PAYEE_ADD + CARD_LIMIT_LOOSEN → DENY + cooldown
- `test_risky_sequence_idempotency_no_duplicate_cooldown` — 409 doesn't create extra cooldown
- `test_evaluate_replan_insufficient_funds` — low balance → REPLAN + INSUFFICIENT_FUNDS
- `test_g6_vs_evaluate_concurrent_no_deadlock` — concurrent G6+G4 no deadlock

Plus evaluation_repo tests and lock_order tests (9 more).

## Fixes applied

1. `_step_with_sources()` helper: builds transfer step with `arg_sources` (utterance span for amount, read pointers for account/payee); memo=None so provenance/intent skip it
2. `_seed_provenance_data()`: seeds read records + patches utterance text/scope for amount matching
3. `_low_balance_bank_state()`: sets `current_card_id=None` + high limits so `INSUFFICIENT_FUNDS` fires before `CARD_SINGLE_LIMIT`
4. RISKY_SEQUENCE risk assertion: YELLOW (step base_risk from tools.yaml) — not RED (RISKY_SEQUENCE finding has DENY effect, not RISK_RED)
5. **B5 lock-order GREEN test fix** (`cb4fe89`): original test was deadlocked by design — conn_a held session lock while waiting for conn_b's `proceed` event, but conn_b needed the same session lock. Rewrote to model actual production: conn_a locks session → holds briefly (conn_b serializes behind it on session row) → locks approval → rollbacks → conn_b unblocks and proceeds. RED verification: temporarily swapped to old approval-first order → PostgreSQL detected ABBA deadlock → test FAILED → reverted.
6. **B5 test hooks** (`2bb59e8`): added `_test_after_session_lock` / `_test_before_approval_lock` asyncio.Event hooks in `approvals.py` (confirm_approval / reject_approval / otp_send). None by default (no-op in production). Available for future HTTP-level integration tests; current test uses direct function calls instead.

## Exit commands (after B5 GREEN fix, cb4fe89)

- `make lint`: PASS (326 files)
- `make test`: 2534 passed, 699 skipped
- `make test-gateway`: 2017 passed, 609 skipped
- `make test-gateway-db`: 17 passed
- `make test-w3b`: 28 passed, 0 skipped (8.97s)
- `make contracts-check`: PASS

## Fix round 3 — B5 AST guard (afdc7da)

- Added `tests/gateway/w3b/db/test_lock_order_guard.py`: AST-based source-level guard asserts
  `_lock_session` < `_lock_approval` in G6/G7/G10 (and nested closures).
- RED verified: swapping each endpoint's lock calls made guard fail with endpoint name; reverted.
- Deadlock semantics proven by `test_lock_order.py` helper-function-level RED/GREEN.

Exit commands (afdc7da):
- `make lint`: PASS
- `make test`: 2534 passed, 701 skipped
- `make test-gateway`: 2017 passed, 611 skipped
- `make test-gateway-db`: 17 passed
- `make test-w3a`: 45 passed
- `make test-w3b`: 33 passed
- `make test-w4b2b`: 58 passed
- `make test-w4b2c`: 56 passed
- `make contracts-check`: PASS

## Fix round 4 — B1 cooldown rollback RED test

- Added `tests/gateway/w3b/db/test_b1_cooldown_rollback.py`: proves cooldowns vanish when write
  transaction fails (monkeypatches `append_evaluation` to raise after `_persist_cooldowns`).
- RED verified: temporarily changed `_persist_cooldowns` to use separate `create_async_engine` +
  `AUTOCOMMIT` connection → test FAILED (cooldown leaked: after=1 vs before=0); reverted.
- Also broke `test_risky_sequence_deny_with_cooldown` and
  `test_risky_sequence_idempotency_no_duplicate_cooldown` during RED (separate engine can't
  participate in outer transaction read — expected).
- Fixed 3 lint errors: unused `mock`/`Code` imports, `plan_id` → `plan_id_from_resp` typo.
- ruff format clean up.

Exit commands (HEAD):
- `make lint`: PASS (329 files)
- `make test`: 2535 passed, 702 skipped
- `make test-gateway`: 2018 passed, 612 skipped
- `make test-gateway-db`: 17 passed
- `make test-w3a`: 45 passed
- `make test-w3b`: 33 passed
- `make test-w4b2b`: 58 passed
- `make test-w4b2c`: 56 passed
- `make contracts-check`: PASS