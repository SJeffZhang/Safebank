# Round 3 Review: S1 Stale-Claim Refactor

**Reviewer**: Claude (static code review, tests not run — Docker gated)
**Scope**: `ef8c843..cf827b2` — 3 new round-2 tests + `wall_now()` fix + advisory lock stale-claim implementation
**Files reviewed**: `scheduled.py`, `test_scheduled_authorize.py`, `conftest.py`, `0004_in_progress.py`, `tables.py`, `db.py`
**Date**: 2026-10-30

---

## Verdict: **APPROVE** — no P0, no P1

The stale-claim refactor correctly replaces the old "advisory lock on a pooled connection held across L3 HTTP calls" with a two-phase pattern: Phase A claims in a short locked transaction → Phase B evaluates with **no lock and no held connection** → Phase C finalizes in a second short transaction. All 7 review criteria pass.

---

## 7 Checks — All Pass

### 1. No DB lock held during L3 HTTP ✅

**`_claim_event`** (lines 152–209): acquires `pg_try_advisory_lock`, reads/inserts/updates `event_decisions`, commits, then **releases the lock in `finally`** (line 206).  The `async with db.connect() as conn:` returns the connection to the pool when the context exits.

**Phase B** uses separate, short-lived connections:
- `_load_grant`: `async with db.connect() as conn:` (line 429) — read-only, no lock
- `_re_evaluate`: `async with db.connect() as conn:` (line 282) — read-only snapshot, no lock
- `_backfill_grant`: `async with db.begin() as conn:` (line 327) — short write transaction, no lock

**`_release_claim`**: `async with db.begin() as conn:` (line 256) — short DELETE, no lock.

**`_finalize_claim`**: `async with db.begin() as conn:` (line 226) — short UPDATE+audit, no lock. (It intentionally does NOT re-acquire the advisory lock — the conditional `WHERE status = 'IN_PROGRESS'` is the safety net; see P2-3 below.)

**Test evidence**: `test_no_connection_held_during_l3_call` (lines 659–721) launches the handler asynchronously while `event_execute` is delayed 0.5s, then probes `event_decisions` from a **separate connection** — finds the `IN_PROGRESS` row without blocking. This proves no lock is held on the row across the L3 HTTP call.

### 2. Concurrent callbacks for same event_id ✅

`_claim_event` uses `pg_try_advisory_lock(hashtext('s1_event:{event_id}'))` (line 156). Only one callback acquires it. The loser immediately gets `RETRYING` / `EXECUTION_IN_PROGRESS` (line 162).

**Test evidence**: `test_concurrent_callbacks_exactly_one_execute` (lines 537–584) uses `asyncio.gather` with 0.3s `event_execute` delay → exactly 1 execute call, the other gets `RETRYING`.

### 3. L3 committed but gateway crash before finalize ✅

If the process crashes after `l3.event_execute` succeeds but before Phase C runs:
1. The `IN_PROGRESS` row persists (no one promotes it)
2. After `_CLAIM_LEASE` (5 min), the next callback's Phase A sees a stale row
3. Takes over (updates `decided_at_wall`, line 184–191)
4. Re-calls `l3.event_execute` — L3's **idempotency** returns the stored result
5. Phase C promotes to `SUCCESS`

**Test evidence**: `test_persist_failure_retry_records_original_result` (lines 587–653) manually deletes the row after first success, then retries — L3 returns the same `instruction_run_id` from idempotent storage. (Note: this test deletes the row rather than leaving a stale `IN_PROGRESS` — see P2-1.)

### 4. Stale claim takeover ✅

The takeover in `_claim_event` (lines 184–191) is a conditional UPDATE:
```sql
UPDATE event_decisions SET decided_at_wall = :now
WHERE event_id = :eid AND status = 'IN_PROGRESS'
```
- Only updates `decided_at_wall` (preserves `IN_PROGRESS` status)
- Advisory lock prevents two callbacks from simultaneously taking over
- After takeover, the second callback's Phase A would see age < `_CLAIM_LEASE` → `RETRYING`

**Test evidence**: `test_stale_claim_takeover` (lines 724–768) seeds a 6-min-old `IN_PROGRESS` row → handler returns `SUCCESS` (not `RETRYING`).

### 5. RETRYING releases claim, no deadlock ✅

`_release_claim` (lines 254–260): `DELETE FROM event_decisions WHERE event_id = :eid AND status = 'IN_PROGRESS'`. Only deletes `IN_PROGRESS` rows — terminal rows are never touched.

`_finalize_claim` (lines 226–237): `UPDATE event_decisions SET status = … WHERE event_id = :eid AND status = 'IN_PROGRESS'`. If the row was already released or finalized (`rowcount == 0`), it returns silently.

Race safety: only the CLAIMED handler calls `_release_claim` or `_finalize_claim`. Two callbacks cannot both get `CLAIMED` (advisory lock serializes Phase A). No deadlock because neither function acquires the advisory lock.

**Test evidence**: `test_retrying_releases_in_progress_row` (lines 771–818) sets `event_execute` to `unavailable_503` → handler returns `RETRYING` → asserts zero rows in `event_decisions`.

### 6. Migration backward compatible ✅

`0004_in_progress.py` (lines 19–35):
```python
ALTER TABLE event_decisions DROP CONSTRAINT IF EXISTS event_decisions_status_check
-- re-add with IN_PROGRESS included
```
- Only extends the CHECK constraint — all existing terminal statuses remain valid
- No data migration needed (IN_PROGRESS rows are created by new code only)
- Old code reading `event_decisions` sees same columns, same terminal statuses
- Downgrade raises `RuntimeError` — correct per project's forward-only convention

### 7. Tests trigger real scenarios ✅

All 3 new round-2 tests use:
- Real tmpfs PostgreSQL (via `gateway_db` fixture)
- Real ASGI handler (`build_app` → `build_internal_app`)
- Real advisory locks (`pg_try_advisory_lock` on real PG)
- Real `httpx.AsyncClient` → ASGI transport (not mocked HTTP layer)
- Stateful `FakeL3` double wrapped in real `L3Client` transport

| Test | Real scenario verified |
|------|----------------------|
| `test_no_connection_held_during_l3_call` | IN_PROGRESS row readable from separate connection during L3 call — proves lock released |
| `test_stale_claim_takeover` | 6-min-old row → takeover → SUCCESS, exactly 1 execute call |
| `test_retrying_releases_in_progress_row` | L3 unavailable → RETRYING → zero rows left behind |

No test uses mocks for the lock, DB, or HTTP layer. All exercise the real code path through `scheduled_authorize`.

---

## Findings

### P2-1: `test_persist_failure_retry_records_original_result` tests wrong crash scenario

**File**: `tests/gateway/s1/test_scheduled_authorize.py:620–622`

The test manually **deletes** the `event_decisions` row after a successful first call, then retries. This simulates "the row vanished" rather than the actual crash scenario: "Phase C (`_finalize_claim`) never ran, leaving a stale `IN_PROGRESS` row."

With the stale-claim pattern, the accurate simulation of "Phase C crash" would be: leave the `IN_PROGRESS` row untouched (or with an old `decided_at_wall` that exceeds the lease), then call `scheduled_authorize` again. The test as written exercises a different branch (no row → fresh insert in Phase A) than the real crash scenario (stale row → takeover in Phase A).

**Severity**: P2. The existing test still provides value (proves L3 idempotency on retry), and `test_stale_claim_takeover` covers the stale-row path. But together they don't prove the full crash→takeover→re-execute→L3-idempotent sequence in a single test. Not blocking.

### P2-2: Double L3 event_execute when slow original handler races with takeover

**File**: `services/gateway/app/internal/scheduled.py:184–191` (takeover) and `:511–523` (event_execute)

Scenario:
1. Handler A's Phase B takes >5 minutes (slow L3)
2. Handler B's Phase A: sees stale `IN_PROGRESS` → takes over → returns `CLAIMED`
3. Handler B's Phase B: calls `l3.event_execute`
4. Handler A's Phase B: also calls `l3.event_execute` (Handler A doesn't know it's been taken over)
5. Result: L3 receives `event_execute` twice for the same `event_id`

L3's idempotency (`/internal/events/{id}/execute` returns stored result for committed events) is the **sole defense**. Both handlers then call `_finalize_claim` — one succeeds (rowcount=1), the other silently returns (rowcount=0) without audit.

**Severity**: P2. This is inherent to the stale-claim pattern and documented in the module docstring ("the original handler is presumed dead"). The 5-minute lease makes the window wide enough that this is unlikely in practice. L3 idempotency is a hard requirement either way. Not blocking.

### P2-3: `_finalize_claim` doesn't re-acquire advisory lock → race with takeover

**File**: `services/gateway/app/internal/scheduled.py:226–237`

`_finalize_claim` uses only `WHERE status = 'IN_PROGRESS'` as its safety net — it does NOT re-acquire the advisory lock. This creates a theoretical race:

1. Original handler's Phase C: `_finalize_claim` begins, reads row (still `IN_PROGRESS`)
2. Takeover handler's Phase A: acquires advisory lock, sees age > `_CLAIM_LEASE`, takes over (updates `decided_at_wall`, keeps `IN_PROGRESS`)
3. Original handler's `_finalize_claim`: `UPDATE … WHERE status = 'IN_PROGRESS'` → succeeds (promotes to terminal)
4. Takeover handler proceeds to Phase B → calls `l3.event_execute` (double execute)
5. Takeover handler's `_finalize_claim`: rowcount=0 (already terminal) → returns silently

No data corruption (at most one finalize succeeds), but L3 may be called twice. Same root cause as P2-2 — L3 idempotency required.

**Severity**: P2. Safe in practice because the 5-minute lease means the original handler's `_finalize_claim` (Phase C) would need to be delayed >5 minutes after its own Phase A for this race to occur. Not blocking.

---

## Previous round findings — status

| Finding | Status |
|---------|--------|
| Round 1 P1-1 (TOCTOU race) | **FIXED** — advisory lock in `_claim_event` serializes Phase A |
| Round 1 P1-2 (persist failure after L3 commit) | **FIXED** — stale-claim pattern with IN_PROGRESS row, conditional finalize |
| Round 2 P1 (advisory lock held across HTTP) | **FIXED** — two-phase stale-claim pattern, lock released before any L3 HTTP |
| Round 2 P2-1 (pool connection exhaustion) | **FIXED** — connection returned to pool after Phase A, Phase B uses fresh connections |
| Round 2 P2-2 (IN_PROGRESS not in CHECK) | **FIXED** — migration 0004 extends CHECK |

All prior blocking findings are resolved. The stale-claim refactor successfully eliminates the core problem (advisory lock on a pooled connection during L3 HTTP calls).

---

## Test coverage summary

| Scenario | Covered by |
|----------|-----------|
| Terminal replay → stored result | `test_terminal_replay_returns_stored_result` |
| Terminal replay → no re-execution | `test_same_event_replay_never_executes_twice` |
| L3 unavailable → RETRYING, nothing written | `test_retrying_on_l3_unavailable_writes_nothing` |
| Wrong token → 401 | `test_wrong_token_returns_401` |
| REVOKED grant → BLOCKED | `test_revoked_grant_returns_blocked` |
| scope_hash mismatch → BLOCKED | `test_scope_hash_mismatch_returns_blocked` |
| Missing grant + COMMITTED backfill → SUCCESS | `test_missing_grant_backfill_and_success` |
| Risk increase → SKIPPED_REAPPROVAL | `test_risk_increase_returns_skipped_reapproval` |
| Insufficient funds → FAILED_INSUFFICIENT | `test_insufficient_funds_returns_failed_insufficient` |
| Non-COMMITTED backfill → BLOCKED | `test_non_committed_backfill_returns_blocked` |
| Happy path → transfers once + replay idempotent | `test_happy_path_success_executes_once` |
| Concurrent callbacks → exactly 1 execute | `test_concurrent_callbacks_exactly_one_execute` |
| Persist failure → retry + stored result | `test_persist_failure_retry_records_original_result` |
| No connection held during L3 HTTP | `test_no_connection_held_during_l3_call` |
| Stale claim takeover (6 min) | `test_stale_claim_takeover` |
| RETRYING → IN_PROGRESS row deleted | `test_retrying_releases_in_progress_row` |
| Stale IN_PROGRESS + takeover + L3 idempotent (combined) | Not tested (P2-1) |
| Slow handler + takeover race → double execute | Not tested (P2-2, P2-3) |
| DENY/REPLAN (non-insufficient) → BLOCKED | Not tested (existing gap) |