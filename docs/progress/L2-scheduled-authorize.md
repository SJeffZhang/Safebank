# L2-scheduled-authorize (S1 endpoint) — progress

**Branch**: `feat/l2-scheduled-authorize` (from main 0b9684e). Worktree GW-s1.

## Status (session r4 — final)
- Handler steps 1–5 fully implemented (`4b9d2c9` + `e25b826` backfill `created_at` fix).
- **9 tests all passing** (`e25b826`—`7239444`):
  - `test_terminal_replay_returns_stored_result` — stored event_decisions row returned, no L3 calls
  - `test_retrying_on_l3_unavailable_writes_nothing` — L3 503 → RETRYING, nothing persisted
  - `test_wrong_token_returns_401` — no header / bad token → 401
  - `test_same_event_replay_never_executes_twice` — seeded terminal row → 200 replay
  - `test_revoked_grant_returns_blocked` — REVOKED → BLOCKED + event_block + persisted
  - `test_missing_grant_backfill_and_success` — backfill from L3 COMMITTED → SUCCESS
  - `test_risk_increase_returns_skipped_reapproval` — GREEN→YELLOW → SKIPPED_REAPPROVAL
  - `test_insufficient_funds_returns_failed_insufficient` — DENY/INSUFFICIENT → FAILED_INSUFFICIENT
  - `test_happy_path_success_executes_once` — ACTIVE+YELLOW → SUCCESS, replay from persisted
- CI: `make test-s1` added to `.github/workflows/checks.yml` p1-postgres
- Final gates (all passing):
  - `make lint` — All checks passed
  - `make test` — 3445 passed, 932 skipped
  - `make test-gateway` — 2140 passed, 675 skipped
  - `make test-gateway-db` — 17 passed
- Pre-existing routing test updated: `test_scheduled_authorize_is_registered_but_stubbed` →
  `test_scheduled_authorize_rejects_wrong_token` (handler no longer stubbed; expects 401 for bad token).
- Pre-existing lint fix: unused `data` variable in `fake.py` `event_execute`.

## Remaining (not in scope of this component)
- Real L3 scheduler / event dispatch / clock advancement
- `scope_hash` mismatch → BLOCKED test (needs scope_hash != event.grant_scope_hash)
- Non-COMMITTED backfill → BLOCKED test
