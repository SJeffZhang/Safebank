# L2-scheduled-authorize (S1 endpoint) — progress

**Branch**: `feat/l2-scheduled-authorize` (from main 0b9684e). Worktree GW-s1.

## Status (session r6 — P1 security fixes)
- Handler steps 1–5 fully implemented.
- **P1-1 (TOCTOU race)** fixed (`189b61c`): `pg_try_advisory_lock(hashtext('s1_event', event_id))` serializes concurrent callbacks for the same event_id. Second callback returns `RETRYING` with `EXECUTION_IN_PROGRESS` without calling L3. Lock released in `finally` block.
- **P1-2 (persist failure retry)** already handled correctly by existing idempotent L3 `event_execute`; test added to validate retry records original result and same `instruction_run_id`.
- **13 tests all passing** (`189b61c`):
  - 11 original tests (terminal replay, retry, 401, same-event replay, REVOKED→BLOCKED, scope_hash mismatch→BLOCKED, non-COMMITTED backfill→BLOCKED, backfill+success, risk increase→SKIPPED_REAPPROVAL, insufficient funds→FAILED_INSUFFICIENT, happy path)
  - `test_concurrent_callbacks_exactly_one_execute` — two `asyncio.gather` callbacks with 0.3s event_execute delay → exactly 1 L3 execute, 1 RETRYING
  - `test_persist_failure_retry_records_original_result` — delete event_decisions row → retry → L3 idempotent result, same instruction_run_id
- CI: `make test-s1` in `.github/workflows/checks.yml` p1-postgres
- FakeL3: added `set_event_execute_delay(seconds)` for concurrent race testing
- Final gates (all passing):
  - `make lint` — All checks passed, 447 files
  - `make test` — 3445 passed, 936 skipped
  - `make test-gateway` — 2140 passed, 679 skipped
  - `make test-gateway-db` — 17 passed
  - `make test-s1` — 13 passed

## Remaining (not in scope of this component)
- Real L3 scheduler / event dispatch / clock advancement
