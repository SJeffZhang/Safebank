# W4b2b Approval Endpoints — Fix Round 2

**Date**: 2026-10-24
**Worktree**: `../Fintech2026-wt/W4b2b` (branch `codex/w4b2b-approval-endpoints`)

## Task

Rewrite D0 concurrency test to genuinely prove instance-scoped HMAC keys: two
apps with **different** keys, self-confirm + cross-confirm rejection, plus a
red-test that temporarily breaks the adapter to confirm the test catches
global-state regression.

## Changes

### `tests/gateway/w4b2b/db/conftest.py`

- `build_app` now accepts `approval_hmac_key` and `otp_hmac_key` keyword-only
  overrides. When provided, these bytes override the conftest `HMAC_KEY`/`OTP_KEY`
  constants in Settings and GatewayRuntime.
- `_compute_binding` accepts `hmac_key: bytes = HMAC_KEY`.
- `_seed_approval` accepts `hmac_key: bytes = HMAC_KEY`, passes to `_compute_binding`.
- `_seed_full` accepts `hmac_key: bytes = HMAC_KEY`, passes to `_seed_approval`.

All existing callers use default values — zero test parameter changes needed.

### `tests/gateway/w4b2b/test_adapter.py`

Replaced `test_d0_concurrency_two_apps_different_keys` (which used same key,
only GET, MACs differed due to different approval_id — did not prove instance
isolation) with `test_d0_two_apps_different_keys_self_confirm_cross_reject`:

1. Builds two apps concurrently with `asyncio.gather`, each with a distinct
   32-byte approval HMAC key and OTP key.
2. Seeds a YELLOW `PENDING` approval chain in each app, `binding_mac` computed
   with that app's key.
3. **Self-confirm** — app A POSTs G6 to `/approvals/apv_a/confirm` with correct
   `view_hash` → 200, DB → CONFIRMED. Same for app B.
4. **Cross-confirm** — app A POSTs G6 to `/approvals/apv_b/confirm` → 409
   `APPROVAL_BINDING_MISMATCH`. App B → `/approvals/apv_a/confirm` → 409.
5. DB assertions: both approvals CONFIRMED (self-confirms worked), plans
   transitioned to CONFIRMED.

### Red-test (not committed)

Temporarily changed `services/gateway/app/approval/adapter.py:approval_hmac_key`
to return a hardcoded `b"GLOBAL-FIXED-KEY--at-least-32-by"` (ignoring
`request.app.state`). Ran `make test-w4b2b` — **10 failed, 41 passed**:

```
FAILED test_adapter.py::test_approval_hmac_key_returns_bytes
FAILED test_adapter.py::test_two_requests_different_keys_return_different_values
FAILED test_adapter.py::test_d0_two_apps_different_keys_self_confirm_cross_reject
FAILED test_confirm.py::test_confirm_yellow_succeeds
FAILED test_confirm.py::test_confirm_idempotent
FAILED test_confirm.py::test_confirm_different_request_id_after_confirm_fails
FAILED test_confirm.py::test_confirm_risk_ack_false_when_not_required_succeeds
FAILED test_confirm.py::test_confirm_red_returns_503
FAILED test_reject.py::test_reject_succeeds
FAILED test_reject.py::test_reject_idempotent
```

The D0 test failed on the very first self-confirm assertion — `ra.status_code == 409`
(`APPROVAL_BINDING_MISMATCH`) instead of 200, because the globally fixed key
did not match either app's seeded key.

Reverted via `git checkout` — all 51 tests pass again.

## Exit Command Results (all exit 0)

| Command | Result |
|---|---|
| `make lint` | All checks passed, 301 files formatted |
| `make test` | 2428 passed, 607 skipped |
| `make test-gateway` | 1987 passed, 547 skipped |
| `make test-gateway-db` | 17 passed |
| `make test-shared-runtime` | 79 passed |
| `make test-w1` | 1688 passed |
| `make test-w4b1` | 57 passed |
| `make test-w4b2b` | **51 passed**, 0 skipped, 0 error |
| `make contracts-check` | PASS |

## Files Changed (Round 2)

| File | Change |
|---|---|
| `tests/gateway/w4b2b/db/conftest.py` | `build_app` accepts key overrides; `_compute_binding`/`_seed_approval`/`_seed_full` accept `hmac_key` |
| `tests/gateway/w4b2b/test_adapter.py` | Rewrote D0 test to use different keys + self/cross confirm + DB assertions |
| `docs/progress/W4b2b-approval-endpoints.md` | This file (updated) |

---

# W4b2b Approval Endpoints — Fix Round 4

**Date**: 2026-10-24
**Worktree**: `../Fintech2026-wt/W4b2b` (branch `codex/w4b2b-approval-endpoints`)

## Tasks

### 1. Fix `test_list_empty` regression

**Root cause**: `gateway_db` fixture is session-scoped. Adding `test_race.py`
to the `db/` directory shifted test ordering so `test_list_empty` (which
used `truncate=False`) ran after a data-seeding test, seeing leftover rows.

**Fix**: Changed `test_list_empty` from `truncate=False` to default
`truncate=True`. The test still exercises the empty-list path (truncate
clears all tables, `_seed_auth` re-seeds only the auth row, approvals
table is empty).

### 2. Rewrite `test_race.py` — single green test + red-verification

**Problem with round 3**: `test_session_revoke_race_without_for_update_would_bypass`
asserted the *buggy* behavior (that a stale read exists), which is forbidden —
tests must only assert correct behavior.

**Fix**: Single test `test_concurrent_session_revoke_blocked_by_for_update`:

- conn1 locks approval, locks session **WITH FOR UPDATE**
- conn2 concurrently revokes session → **blocked** by FOR UPDATE
- Assertion: `assert not revoke_done.is_set()` — conn2 is blocked (correct)
- conn1 confirms approval → commits
- conn2 unblocks → revocation commits
- DB state: approval CONFIRMED (confirmation won the race)

### 3. Red-verification

Temporarily removed `.with_for_update()` from the test's session SELECT.
Ran `make test-w4b2b`:

```
FAILED tests/gateway/w4b2b/db/test_race.py::test_concurrent_session_revoke_blocked_by_for_update
1 failed, 56 passed in 7.98s

E  AssertionError: revoke should be blocked by FOR UPDATE
E  assert not True
E   +  where True = is_set()
```

Without FOR UPDATE, conn2's revocation was NOT blocked — it committed
while conn1 still held a stale snapshot. The assertion caught the race
window. Reverted the temporary edit (not committed).

Green: restored `.with_for_update()` → 57 passed, 0 failed (confirmed
twice, stable).

## Files Changed (Round 4)

| File | Change |
|---|---|
| `tests/gateway/w4b2b/db/test_list.py` | `test_list_empty`: `truncate=False` → default `truncate=True` |
| `tests/gateway/w4b2b/db/test_race.py` | Rewritten: single green test + red-verification (comment only) |
| `docs/progress/W4b2b-approval-endpoints.md` | This file (updated) |

## Exit Command Results (all exit 0)

| Command | Result |
|---|---|
| `make lint` | All checks passed, 302 files formatted |
| `make test` | 2428 passed, 613 skipped |
| `make test-gateway` | 1987 passed, 553 skipped |
| `make test-gateway-db` | 17 passed |
| `make test-shared-runtime` | 79 passed |
| `make test-w1` | 1688 passed |
| `make test-w4b1` | 57 passed |
| `make test-w4b2b` | 57 passed, 0 failed (confirmed ×2) |
| `make contracts-check` | PASS |