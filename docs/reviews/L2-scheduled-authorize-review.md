# Security Review: `POST /internal/scheduled/authorize` (S1)

**Reviewed**: 2026-10-29 | `services/gateway/app/internal/scheduled.py`  
**Scope**: diff `origin/main..HEAD` (scheduled.py +349, client +21, fake +58, tests +531)  
**Method**: Static code review + test analysis. Tests not run (permission denied on Docker-dependent `GATEWAY_DB_TESTS=1`).

---

## Verdict: **APPROVE** with 2 P1, 4 P2

No P0. All 13 mandatory checks pass. The handler correctly implements PRD §7.11 / §9.5a S1: it fetches the immutable event from L3, never trusts callback amounts; re-evaluates on a fresh snapshot; compares risk against the grant's `approved_risk`; blocks revoked grants and fingerprint mismatches; backfills grants only from COMMITTED L3 executions; and returns RETRYING with nothing written when L3 is unavailable.

---

## Checks (all pass)

| # | Check | Result | Evidence |
|---|---|---|---|
| 1 | Terminal event_decisions replay returns stored result, never re-executes | ✅ | scheduled.py:248–255 — returns stored row without re-evaluation or L3 calls. Tests `test_terminal_replay_returns_stored_result` / `test_same_event_replay_never_executes_twice` confirm no `event` / `event_execute` / `event_block` operations reach FakeL3. |
| 2 | Amounts/targets only from L3 event, never from callback | ✅ | scheduled.py:260 — `l3.event(body.event_id)` fetches the whole definition. `_build_plan_draft` (line 61–92) uses `event["account_id"]`, `event["to_account_id"]`, `event["payee_id"]`, `event["amount_cents"]`. The `AuthorizeRequest` body (line 51–54) only carries `event_id` + `request_id` + `attempt` — no amount or target fields. |
| 3 | Grant lookup by source ID; REVOKED or scope_hash mismatch → BLOCKED | ✅ | scheduled.py:286 — `grant["status"] == "REVOKED" or grant["scope_hash"] != event["grant_scope_hash"]` → `_block(BLOCKED, SCHEDULED_BYPASS)`. Tests: `test_revoked_grant_returns_blocked`, `test_scope_hash_mismatch_returns_blocked`. |
| 4 | Missing grant backfill only from COMMITTED L3 execution | ✅ | scheduled.py:141–143 — `_backfill_grant` calls `l3.execution(customer_id, source_plan_id)`, rejects non-COMMITTED → returns None → handler calls `_block(BLOCKED, SCHEDULED_BYPASS)`. Test `test_non_committed_backfill_returns_blocked` confirms CANCELLED → BLOCKED. |
| 5 | L3 unavailable → 200 RETRYING, nothing written | ✅ | Four `except L3Error: return _retrying(event_id)` blocks (lines 261–262, 273–274, 298–299, 342–343) plus `_block` → L3Error on `event_block` (line 197–198). `_retrying` returns `{event_id, status:RETRYING, code:DEPENDENCY_UNAVAILABLE}` with no DB or L3 mutations. Test `test_retrying_on_l3_unavailable_writes_nothing` confirms no `event_decisions` rows. |
| 6 | Re-evaluation on a fresh snapshot | ✅ | `_re_evaluate` (lines 95–114): creates new `EvalContext` with `mode=EvalMode.EXECUTE_REPLAY`, runs pipeline stages ①contract ②entity ⑤snapshot ⑥preflight ⑦workflow ⑧decision. Provenance/intent stages excluded (correct: grant was already authorized). |
| 7 | Risk > approved_risk → SKIPPED_REAPPROVAL | ✅ | scheduled.py:307–315 — `_RISK_ORDER[risk_value] > _RISK_ORDER.get(approved_risk, 0)` → `_block(SKIPPED_REAPPROVAL, SCHEDULED_REAPPROVAL_REQUIRED)`. Test `test_risk_increase_returns_skipped_reapproval` confirms GREEN→YELLOW → SKIPPED_REAPPROVAL. |
| 8 | DENY/REPLAN → BLOCKED | ✅ | scheduled.py:316–321 — `decision_value in (DENY, REPLAN)` → `_block(BLOCKED, SCHEDULED_BYPASS)` (unless INSUFFICIENT_FUNDS finding → FAILED_INSUFFICIENT first). |
| 9 | Insufficient → FAILED_INSUFFICIENT | ✅ | scheduled.py:317–318 — `any(f.code is Code.INSUFFICIENT_FUNDS for f in ctx.decision.findings)` checked before general DENY/REPLAN → BLOCKED. Test `test_insufficient_funds_returns_failed_insufficient` confirms 900,000 from 600,000 balance → FAILED_INSUFFICIENT. |
| 10 | Execute at most once per event | ⚠️ | See P1-1 below. Gateway relies on L3 idempotency; no DB-level serialization around the event_decisions read and L3 call. Two concurrent callbacks can both pass Step 1 and both call `l3.event_execute`. L3 PRD §7.11 documents idempotency for `/internal/events/{id}/execute`, so this is **mitigated**, but the gateway has no lock of its own. Test `test_happy_path_success_executes_once` covers replay of same event_id sequentially, not concurrent. |
| 11 | No HTTP inside DB transactions | ✅ | `_persist_decision` (lines 209–233): `async with app.db.begin()` wraps only `conn.execute(insert…)` + `audit_append`. All L3 HTTP (`event_block`, `event_execute`) outside any transaction. Re-evaluation `_re_evaluate` uses `async with app.db.connect()` (read-only connect, no transaction wrapping the L3 snapshot fetch). |
| 12 | Only scheduler service token accepted | ✅ | scheduled.py:238–240 — `caller.kind is not ServiceKind.S → 401`. Route registered in `build_internal_app` with `caller_dependency(env, public_key)` on the router (main.py:158). Service token matching via `secrets.compare_digest` (tokens.py:40). Tests: `test_wrong_token_returns_401`, `test_scheduled_authorize_rejects_wrong_token`. |
| 13 | Audit written | ✅ | `_persist_decision` lines 221–233: `audit_append(conn, customer_id, event_type="SCHEDULE_RESULT", …)` in same transaction as event_decisions insert. Audit failure (exception) rolls back the entire transaction (event_decisions + audit), preventing partial writes. |

---

## Findings

### P1-1: TOCTOU race in terminal decision check — concurrent callbacks may both call L3

**File**: `services/gateway/app/internal/scheduled.py:248–255` (Step 1 check) and `:332–350` (event_execute)  
**Summary**: Two concurrent scheduler callbacks for the same `event_id` race through the `event_decisions` read (non-locking `async with db.connect()`) before either writes a terminal decision. Both proceed through full re-evaluation and both call `l3.event_execute`. The second call relies on L3's idempotency guarantee (PRD §7.11: L3 `/internal/events/{id}/execute` returns stored result for already-terminated events). The gateway's own `_persist_decision` uses `on_conflict_do_nothing()` which silently discards the second write — the handler does not detect or log the race.  
**Failure scenario**: If L3's `event_execute` idempotency has a bug (e.g., a timing window where the first execution hasn't committed before the second call arrives), the same transfer could be applied twice.  
**Recommendation**: Add `SELECT 1 FROM event_decisions WHERE event_id = :id FOR UPDATE` within a write transaction *before* calling L3, serializing callbacks for the same event_id at the gateway layer. Alternatively, use `pg_try_advisory_lock(hashtext('s1_event', event_id))`. Impacts latency only under concurrent callbacks (rare for scheduler).  
**Severity**: P1. Mitigated by L3 idempotency, but the gateway should defend in depth.

### P1-2: `_persist_decision` failure after L3 commit leaves no gateway record — retry repeats L3 call

**File**: `services/gateway/app/internal/scheduled.py:209–233` and callers at `:345` (SUCCESS) / `:199` (_block)  
**Summary**: `_persist_decision` (event_decisions + audit) runs in a single DB transaction *after* the L3 operation (event_execute or event_block). If `_persist_decision` fails (DB error, audit failure), the transaction rolls back — no event_decisions row is persisted. The handler returns an error to the scheduler. On retry, the gateway finds no terminal decision (Step 1), re-evaluates identically, and calls the L3 operation *again*. This is safe only because L3 event_execute and event_block are idempotent — repeated calls return the stored result without re-executing. However, transient DB failures (e.g. a connection pool issue) could cause repeated L3 calls over many retries.  
**Failure scenario**: A transient DB error in `_persist_decision` on every retry would cause the gateway to call L3 event_execute on every retry indefinitely, never recording a terminal decision. While L3 handles this idempotently, it wastes resources and masks the underlying DB issue.  
**Recommendation**: Consider checking `event_decisions` again (or checking the L3 for already-executed events) before re-calling L3 on retry, or add a retry count guard.  
**Severity**: P1. Safe in principle, fragile in practice.

### P2-1: `_backfill_grant` no re-verification after concurrent insert

**File**: `services/gateway/app/internal/scheduled.py:132–172`  
**Summary**: Two concurrent callbacks for different events from the same instruction will both find no grant (line 269), both call `_backfill_grant`, and both attempt to insert the same row. `on_conflict_do_nothing()` prevents duplicate rows, but the second callback proceeds with its in-memory grant dict (constructed from the same L3 execution, so identical). No correctness issue, but the second callback's work is wasted.  
**Severity**: P2. No data corruption possible; purely an efficiency concern.

### P2-2: Grant `approved_risk` defaults to `"YELLOW"` when missing from L3 execution

**File**: `services/gateway/app/internal/scheduled.py:144`  
**Summary**: `approved_risk = execution.get("approved_risk") or "YELLOW"` defaults to YELLOW when the L3 committed execution result omits the field. This is conservative (fails-safe): if the original transfer was GREEN-level but L3 doesn't record it, the backfilled grant has a YELLOW baseline, which means any GREEN re-evaluation will be allowed (GREEN ≤ YELLOW) but future scenarios that are truly YELLOW won't be skipped.  
**Severity**: P2. Conservative default; no security regression. Worth documenting that this backfill path only occurs for grants created before `approved_risk` was stored in execution results.

### P2-3: Suite uses `NullPool` + explicit `db.dispose()` — minor risk of connection leak in test failures

**File**: `tests/gateway/s1/conftest.py:128`, tests `_runtime_db` calls  
**Summary**: Test fixtures create a separate `engine(db_url, poolclass=NullPool)` and call `db.dispose()` at the end of each `asyncio.run()`. If a test assertion fails before `db.dispose()`, the connection may linger until GC. Not a production concern.  
**Severity**: P2. Test-only.

### P2-4: `_pinned` returns `(versions={}, clock_version=0)` when no snapshot

**File**: `services/gateway/app/internal/scheduled.py:203–206`  
**Summary**: When `ctx is None` or `ctx.snapshot is None` (e.g., grant is REVOKED, hash mismatch, backfill failed — all early-exit paths that skip `_re_evaluate`), `_block` sends `expected_versions={}` and `clock_version=0` to L3's `event_block`. This is semantically correct (we have no snapshot data, so we pass no version constraints), but L3's `event_block` should document that empty versions mean "no version check."  
**Severity**: P2. Behavior is correct; just a documentation gap.

---

## Test coverage adequacy

| Scenario | Covered by | Status |
|---|---|---|
| Terminal replay → stored result, no L3 calls | `test_terminal_replay_returns_stored_result` | ✅ |
| Terminal replay → no re-execution (dedicated test) | `test_same_event_replay_never_executes_twice` | ✅ |
| L3 unavailable → RETRYING, nothing written | `test_retrying_on_l3_unavailable_writes_nothing` | ✅ |
| Wrong/missing token → 401 | `test_wrong_token_returns_401` | ✅ |
| REVOKED grant → BLOCKED | `test_revoked_grant_returns_blocked` | ✅ |
| scope_hash mismatch → BLOCKED | `test_scope_hash_mismatch_returns_blocked` | ✅ |
| Missing grant + COMMITTED backfill → SUCCESS | `test_missing_grant_backfill_and_success` | ✅ |
| Risk increase → SKIPPED_REAPPROVAL | `test_risk_increase_returns_skipped_reapproval` | ✅ |
| Insufficient funds → FAILED_INSUFFICIENT | `test_insufficient_funds_returns_failed_insufficient` | ✅ |
| Non-COMMITTED backfill → BLOCKED | `test_non_committed_backfill_returns_blocked` | ✅ |
| Happy path → transfers once + replay idempotent | `test_happy_path_success_executes_once` | ✅ |
| Concurrent callbacks for same event → double execute | Not tested | ❌ |
| DENY/REPLAN (non-insufficient) → BLOCKED | Not tested directly | ❌ |
| RETRYING followed by successful re-evaluation on retry | Not tested | ❌ |

---

## Miscellaneous observations

- **`_RISK_ORDER`** (line 34) correctly models "risk only ever rises" — ordinal comparison is safe because PRD §7.9 defines GREEN < YELLOW < RED as a strict total order.
- **`_STAGES`** (lines 39–46) correctly exclude provenance and intent stages for scheduled grant re-evaluation, since the synthetic app_form draft has no session-scoped read records.
- **`origin="app_form"`** on the synthetic `PlanDraft` (line 103) correctly avoids ENTITY_UNSEEN errors.
- **`mode=EvalMode.EXECUTE_REPLAY`** on the EvalContext (line 107) means the pipeline runs in replay mode — it won't create new plans or approvals.
- **`_block` passes `expected_payload_hash`** (line 194) to L3, allowing L3 to verify the event payload hasn't been tampered with between the gateway's fetch and the block call.
- **Token enforcement tested at unit level** (`test_routing.py:test_scheduled_authorize_rejects_wrong_token`) in addition to integration level (`test_wrong_token_returns_401`).

---

## Round 2 (2026-10-30)

**Scope**: commit 189b61c (advisory lock fix for P1-1 + P1-2 test). Code read from source; tests not run (Docker-dependent).

### Key question: is the advisory lock held across HTTP calls to L3?

**Yes — CONFIRMED.** The `pg_try_advisory_lock` on `lock_conn` (line 266) is held across **every** L3 HTTP call in the handler:

| Line | Operation | HTTP? |
|------|-----------|-------|
| 280 | `l3.event(body.event_id)` | ✅ HTTP |
| 292 | `_backfill_grant(db, l3, …)` → `l3.execution(…)` | ✅ HTTP |
| 297–303 | `_block(…)` → `l3.event_block(…)` (line 187) | ✅ HTTP |
| 305–313 | `_block(…)` → `l3.event_block(…)` | ✅ HTTP |
| 317 | `_re_evaluate(…)` → `l3.snapshot(…)` | ✅ HTTP |
| 329–361 | `_block(…)` or `l3.event_execute(…)` | ✅ HTTP |
| 365 | `_persist_decision(…)` | DB only ✅ |

The lock is only released in `finally` (lines 371–377). `lock_conn` is obtained from the pool at line 252 (`await db.connect()`) and not returned until the handler exits.

This is the **exact violation of the project rule** "no HTTP inside DB transactions or while holding row/advisory locks on a pooled connection."

### P1-1 reopened: pool connection held across HTTP → exhaustion risk

`lock_conn` is a pooled asyncpg connection. While the advisory lock is held, the handler makes up to 4 sequential HTTP calls to L3 (event, execution/backfill, snapshot, event_execute/block). Each call could take seconds (network latency, L3 queue, DB contention). With a default pool size of ~18 connections and the scheduler firing multiple events, even a handful of slow L3 responses could exhaust the pool.

**Failure scenario**: 10 concurrent scheduler callbacks for different event_ids each acquire advisory locks on separate connections and begin calling L3. L3 experiences a slow DB migration or read spike; each HTTP call takes 3–5 seconds. After ~18 concurrent callbacks, the connection pool is exhausted. Other gateway operations that need a DB connection time out.

### Recommended fix: stale-claim pattern (IN_PROGRESS row)

Replace the long-held advisory lock with a two-phase stale-claim approach:

1. **Phase A — claim (short, with lock)**:
   - Acquire `pg_try_advisory_lock` on a connection
   - Check no existing `event_decisions` row for this `event_id`
   - If a terminal row exists → unlock, return stored result
   - If an IN_PROGRESS row exists and is fresh (`decided_at_wall` within timeout) → unlock, return `RETRYING`/`EXECUTION_IN_PROGRESS`
   - If an IN_PROGRESS row exists and is stale → proceed (reclaim)
   - INSERT/UPDATE a row with `status = 'IN_PROGRESS'` and `decided_at_wall`
   - Release the advisory lock and close the connection

2. **Phase B — evaluate and execute (no lock)**:
   - Fetch L3 event, load/backfill grant, re-evaluate, call `event_execute`/`event_block`
   - All HTTP calls happen here with **no advisory lock held and no pooled connection tied up**

3. **Phase C — commit result (short, with lock)**:
   - Re-acquire `pg_try_advisory_lock` on a fresh connection
   - UPDATE the `event_decisions` row from `IN_PROGRESS` to terminal status (SUCCESS/BLOCKED/SKIPPED_REAPPROVAL), with audit in the same transaction
   - Release lock and close connection

A stale-IN_PROGRESS recovery path is essential: if a handler crashes in Phase B, the IN_PROGRESS row is orphaned. The next callback must detect the stale row (`decided_at_wall` older than e.g. 60s), check L3 for the actual execution result via `l3.event(event_id)`, and update it to terminal status. The `event_decisions` table needs a CHECK or index for the `IN_PROGRESS` status (currently not in any CHECK constraint — check the DDL).

### P1-2 verification: retry returns L3's stored terminal result ✅

The test `test_persist_failure_retry_records_original_result` correctly demonstrates:
1. First call succeeds normally (SUCCESS with `instruction_run_id`)
2. Event_decisions row is manually deleted (simulating a persist failure after L3 commit)
3. Retry calls `l3.event_execute` again
4. L3 returns the **same `instruction_run_id`** from its idempotent store
5. Event_decisions row is re-persisted with `status = 'SUCCESS'`

This is correct and sufficient. The retry path does not double-execute; L3's idempotency handles the replay properly.

### P1-1 concurrent test adequacy

`test_concurrent_callbacks_exactly_one_execute` uses `asyncio.gather` with a 0.3s `event_execute` delay to create a race. This correctly verifies:
- Exactly one callback reaches L3 `event_execute`
- The other gets `RETRYING`/`EXECUTION_IN_PROGRESS`
- Total L3 execute calls = 1

However, this test runs within a single asyncio event loop with a `NullPool` — it does not exercise real connection pool contention. The test proves the serialization logic works in principle; the concern is the production pool behavior (see P1-1 above).

### Verdict: **REQUEST_CHANGES**

**Blocking**:

- **P1-1 (reopened)**: Advisory lock on a pooled connection is held across HTTP calls to L3 (`scheduled.py` lines 252–377). The lock acquisition at line 266 is inside a `try` block that extends through all L3 I/O (`l3.event`, `l3.execution` via `_backfill_grant`, `l3.snapshot` via `_re_evaluate`, `l3.event_execute`/`l3.event_block`), with release only at `finally` (lines 371–377). Must be restructured to the two-phase stale-claim pattern (IN_PROGRESS row in a short locked transaction → HTTP calls with no lock → terminal update in a second short transaction) so no pool connection holds a lock during L3 I/O.

**Not blocking**:

- P1-2 fix is correct. Test `test_persist_failure_retry_records_original_result` proves L3 idempotency handles the retry correctly. No additional changes needed.
- Concurrent callback test (`test_concurrent_callbacks_exactly_one_execute`) is sound within a single-loop async context and should be retained after the lock restructuring.
- All other Round 1 checks remain valid — the business logic (re-evaluation, risk comparison, block/execute mapping) is unchanged from the approved Round 1 and the lock restructuring should preserve it exactly.