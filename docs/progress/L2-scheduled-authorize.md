# L2-scheduled-authorize (S1 endpoint) — progress

**Branch**: `feat/l2-scheduled-authorize` (from main 0b9684e). Worktree GW-s1.

## Status (session r5 — final two tests added)
- Handler steps 1–5 fully implemented.
- **11 tests all passing** (`471dfef`):
  - 9 original tests (terminal replay, retry, 401, same-event replay, REVOKED→BLOCKED, backfill+success, risk increase→SKIPPED_REAPPROVAL, insufficient funds→FAILED_INSUFFICIENT, happy path)
  - `test_scope_hash_mismatch_returns_blocked` — seeded grant scope_hash != event grant_scope_hash → BLOCKED + event_block
  - `test_non_committed_backfill_returns_blocked` — L3 execution non-COMMITTED → BLOCKED (no grant possible)
- CI: `make test-s1` in `.github/workflows/checks.yml` p1-postgres
- Final gates (all passing):
  - `make lint` — All checks passed, 447 files
  - `make test` — 3445 passed, 934 skipped
  - `make test-gateway` — 2140 passed, 677 skipped
  - `make test-gateway-db` — 17 passed
  - `make test-s1` — 11 passed

## Remaining (not in scope of this component)
- Real L3 scheduler / event dispatch / clock advancement
