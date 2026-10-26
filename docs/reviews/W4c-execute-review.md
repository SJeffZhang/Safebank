# W4c Execute — Independent Code Review

**Reviewer**: Claude (DeepSeek V4 Pro), 2026-10-25
**Scope**: `services/gateway/app/api/execution.py`, `services/gateway/app/state.py` (relevant CAS), `services/gateway/app/execution/replay.py`, migration 0003, all 13 test files in `tests/gateway/w4c/db/`
**Task spec**: `docs/plans/briefs/W4c-execute.md` (340 lines)
**Progress log**: `docs/progress/W4c-execute.md` (267 lines)
**Verdict**: **REQUEST_CHANGES** — 2 P1, 3 P2; no P0

---

## P1 (must fix before merge)

### P1-1: Resume path misses STATE_CHANGED barrier — swallows as UNKNOWN

- **File**: `services/gateway/app/api/execution.py:864-869`
- **Summary**: `_handle_resume` catches all `L3BusinessError` and routes to `_unknown()`, while the normal G8 path correctly checks `exc.code == "STATE_CHANGED"` and enters `_handle_state_changed`.

```python
# execution.py:864-869 — resume path
except L3BusinessError as exc:
    return await _unknown(f"execute(resume): L3BusinessError {exc.code}")
```

Compare with the normal path:

```python
# execution.py:1614-1648 — normal path
except L3BusinessError as exc:
    if exc.code == "STATE_CHANGED":
        # ADR-008 fence...
        return await _handle_state_changed(...)
    return await _unknown(...)
```

**Failure scenario**: A resumed execution (attempt ≥ 2) hits STATE_CHANGED from L3. The prepare already succeeded (L3 has a PREPARED intent for this attempt, `_handle_resume` called `l3.prepare` at line 849). Instead of fencing, the code sends the plan to UNKNOWN → reconcile → CANCELLED, losing the opportunity to re-evaluate. The barrier works for attempt 1 but not for attempt 2+.

**Fix**: Replace the generic `except L3BusinessError` at line 864-869 with `exc.code == "STATE_CHANGED"` → `_handle_state_changed(...)` and other codes → `_unknown(...)`. The resume path must pass `exec_identity` (which correctly has the resumed attempt number at line 815-821) and the other barrier parameters to `_handle_state_changed`. See P1-2 for the related hardcode issue.

### P1-2: `_handle_state_changed` hardcodes `attempt=1` in fence prepare

- **File**: `services/gateway/app/api/execution.py:415`
- **Summary**: The fence `Prepare` body uses literal `attempt=1`.

```python
# execution.py:412-421
fence_prepare_body = Prepare(
    customer_id=caller_customer_id,
    plan_id=plan_id,
    attempt=1,  # <-- hardcoded
    ...
)
```

**Failure scenario**: Once P1-1 is fixed and `_handle_state_changed` is called from the resume path (attempt ≥ 2), the fence prepare would tell L3 `attempt=1` even though the existing PREPARED intent is at attempt ≥ 2. L3 would either reject this as a mismatched attempt or create a second intent.

**Fix**: Replace `attempt=1` with `attempt=exec_identity.attempt`. This is currently functionally correct (the only caller passes `exec_identity` with `attempt=1` at line 1547-1551), but becomes wrong once P1-1 is fixed.

---

## P2 (should fix before merge; non-blocking)

### P2-1: PAUSED+DENIED/REPLAN plans silently enter resume path

- **File**: `services/gateway/app/api/execution.py:1185-1195`
- **Summary**: The PAUSED branch matches all PAUSED jobs:

```python
# execution.py:1185
elif job_status == JobStatus.PAUSED.value:
    return await _handle_resume(...)
```

When the barrier produces a DENIED or REPLAN decision (via `publish_paused_decision`), the plan status is updated to DENIED or REPLAN but the job stays PAUSED. A subsequent G8 call hits this branch, enters `_handle_resume`, which replays the decision → DENIED/REPLAN → `_check_decision` raises `STATE_CHANGED` 409. This is a safe loop: no L3 is contacted, no data is corrupted, and the caller gets a 409 with the correct decision. But the error path is confusing — a DENIED plan should get a clear 403/409 "already decided" rather than silently entering a resume-pipeline that loops back to the same result.

**Fix**: Add an early check before line 1185: if `plan["status"] in {PlanStatus.DENIED.value, PlanStatus.REPLAN.value}` and job is PAUSED, return 409 with the existing evaluation decision directly (no replay, no L3 contact).

### P2-2: Orphan PAUSED jobs for DENIED/REPLAN — no path to archival

- **File**: `services/gateway/app/state.py:547-629` (`resume_paused_execution`)
- **Summary**: `resume_paused_execution` validates that the plan is in EVALUATED status (line 578-584). A DENIED or REPLAN plan with a PAUSED job cannot be resumed, and there is no code path to call `archive_execution_terminal` for these jobs. They remain PAUSED forever with CLAIMED claims holding those authorization resources.

**Note**: This is a known issue — the task spec mentions W5 for orphan cleanup. No data corruption or double-spending risk (claims are CLAIMED, not released or reusable). The `guard_paused_execution` function at line 404-421 explicitly allows DENIED/REPLAN statuses through for publishing new decisions, but there is no symmetric code to archive the PAUSED job when the plan decision is already final.

### P2-3: Reconciliation doesn't upgrade request_dedup non-terminal markers

- **Files**: `services/gateway/app/api/execution.py:946` (`_reconcile_job`) and `execution.py:76-107` (`_write_request_dedup_row`)
- **Summary**: When G8's first call gets UNKNOWN (request_dedup gets a non-terminal marker), and a second G8 triggers reconciliation → terminal, the reconciler archives the job but never calls `_write_request_dedup_row` for the terminal response. The non-terminal request_dedup marker persists forever.

**Impact**: Harmless for correctness. Subsequent calls with the same request_id would hit the non-terminal path, but since the plan is terminal, the code at line 1152-1167 returns the cached job result. Only the stale marker remains. Mild operational nuisance.

**Fix**: Have `_reconcile_job` call `_write_request_dedup_row(conn, key, terminal_result)` after successful archival (line 947), or have `_handle_reconcile` do it after returning from `_reconcile_job`.

---

## Unconfirmed observations (not filed as findings)

1. **`execution.py:1547-1551` — Normal-path `exec_identity` always `attempt=1`**: This is correct for the normal path. If `_handle_state_changed` is ever called from the resume path (P1-1), the resume's `exec_identity` (line 815-821, with correct attempt) would need to be passed. Verifying this requires implementing P1-1 and testing.

2. **Test naming**: `test_g8_resume.py` docstring (lines 11-14) says "both tests are expected to fail (red)" — all three tests in this file pass green in the current implementation. The docstring is stale from the red-first development phase; not a defect.

3. **`test_g8_unknown.py:93-99` assertion**: The `test_g8_returns_202_on_unknown_with_response` test checks `resp.json()["status"] == "UNKNOWN"` at line 99. This is correct per the G8 spec — confirmed during reading.

---

## Verified checklist — 8 criteria

| # | Criterion | Verdict | Evidence |
|---|-----------|---------|----------|
| 1 | **No double accounting** | ✅ PASS | `archive_execution_terminal` at `state.py:707-735` uses CAS `match_attempt=False` only on UNKNOWN rows; normal path at `state.py:463-474` requires `match_attempt=True`. `test_g8_allow.py:29-59` asserts L3 `plan_executions` count == 1. |
| 2 | **No HTTP inside transactions** | ✅ PASS | All L3 calls (`snapshot`, `prepare`, `execute`, `query`, `reconcile`) are outside `conn.begin()` blocks. Verified at `execution.py:1415-1418` (snapshot before transaction), `execution.py:1515-1536` (prepare before write txn), `execution.py:1606` (execute before write txn). |
| 3 | **Authorization claims hold correctly** | ✅ PASS | Claims inserted as CLAIMED at `execution.py:1444-1470` before L3 calls; consumed to CONSUMED only in `archive_execution_terminal` (`state.py:723`). `mark_execution_unknown` at `state.py:684-704` preserves CLAIMED status. Verified by `test_g8_allow.py:111-136`, `test_g8_fence.py:92-104`, `test_g8_reconcile.py:182-185`. |
| 4 | **Replay equivalence** | ✅ PASS | `replay.py:13-19` builds fresh `EvalContext` (identity/input fields only, no cached stage results), calls `pipeline.evaluate(mode=EXECUTE_REPLAY)`. `test_replay.py` verifies replay produces same decision. `test_g8_races.py:165-199` (F9) tests terminal replay returns `replayed=True` with same result. |
| 5 | **ADR-008 barrier** | ⚠️ PASS (normal path) — P1 on resume path | Normal path at `execution.py:1615-1648` correctly enters `_handle_state_changed`. `_handle_state_changed` (line 380-626) implements the full fence: `begin_fencing` → `fence_prepare` → `acknowledge_fence` → `publish_paused_decision` → 409 with new Decision. Verified by `test_g8_fence.py` (3 tests, all green). **P1-1**: resume path at line 864-869 doesn't enter barrier. **P1-2**: fence prepare hardcodes `attempt=1`. |
| 6 | **request_dedup** | ✅ PASS | `execution.py:110-147` checks hash at line 140, upserts via `_write_request_dedup_row` (line 76-107). Terminal writes at lines 1559-1577 and 1668-1681. Non-terminal markers at line 1541-1558 use `on_conflict_do_nothing` — correct: non-terminal rows must not overwrite terminal ones. `test_g8_allow.py:49-59` verifies same request_id idempotency returns 200, L3 only 1 execution. |
| 7 | **Migration 0003** | ✅ PASS | `migrations/versions/0003_plan_draft.py`: adds `plan_draft JSONB NULL` to plans, forward-only (downgrade raises RuntimeError). `plans.py:285-293` loads plan_draft during G8. NULL old rows → 503 per `execution.py:1218-1223`. Existing test coverage: `test_g8_allow.py`, `test_g8_confirmed.py`, `test_g8_fence.py` all depend on plan_draft being non-null (via `allow_plan_ctx` which uses the 0003 column). |
| 8 | **Real L3 tests** | ✅ PASS | All 13 test files use real L3 backend over HTTP/ASGI (uvicorn thread in `conftest.py`). Tests assert L3 DB state directly: `test_g8_allow.py:41-45` checks `cards.status == "FROZEN"`, `test_g8_allow.py:53-58` counts `plan_executions`, `test_g8_resume.py:95-99` card status and `execution_intents.attempt == 2`, `test_g8_reconcile.py:160-173` intent status and execution count, `test_g8_races.py:69-76` concurrency execution count. No mock L3 in these test files. |

---

## Summary

| Level | Count | Key issues |
|-------|-------|-----------|
| P0 | 0 | — |
| P1 | 2 | Resume STATE_CHANGED swallowed as UNKNOWN; hardcoded `attempt=1` in fence prepare |
| P2 | 3 | PAUSED+DENIED confusing resume; orphan PAUSED jobs; stale request_dedup markers |

All 8 review criteria are satisfied for the normal execution path. The two P1 findings affect the resume-after-fence path (attempt ≥ 2 executions after a STATE_CHANGED barrier). The P2 findings are non-blocking quality/cleanup items.

**Recommended fix order**: (1) Fix P1-2 first (replace `attempt=1` with `exec_identity.attempt` at line 415) — this is a one-line change with no behavioral impact on the current callers. (2) Fix P1-1 (add STATE_CHANGED check in the resume `except L3BusinessError` block at line 864-869, route to `_handle_state_changed`). (3) Add a new test in `test_g8_resume.py` for the resume + STATE_CHANGED scenario (simulate L3 returning STATE_CHANGED on attempt 2 by bumping state_version before the resumed execute).
## 复审（修复轮 1）

**Reviewer**: Claude (DeepSeek V4 Pro), 2026-10-26
**Scope**: `git diff 95225bc..dbb5426 -- services tests/gateway/w4c`
**Verdict**: **APPROVE** — all 4 findings addressed; no new P0/P1

### P1-1: Resume STATE_CHANGED enters `_handle_state_changed` → ADDRESSED

- `execution.py:869-902`: Resume path now checks `exc.code == "STATE_CHANGED"`, writes non-terminal dedup (`on_conflict_do_nothing`), and calls `_handle_state_changed` with `exec_identity`.
- The `exec_identity` (line 817-823) already uses `attempt=new_attempt` from `resume_paused_execution`, so the fence receives the correct attempt.
- Other `L3BusinessError` codes still go to `_unknown()` (line 903) — correct.
- Dedup pattern mirrors the normal path (line 1695-1701): both use `on_conflict_do_nothing()` with `{"non_terminal": True}`.

### P1-2: Hardcoded `attempt=1` removed → ADDRESSED

- `execution.py:415`: `attempt=1` → `attempt=exec_identity.attempt`.
- `execution.py:499`: `!= 1` → `!= exec_identity.attempt`.
- `grep -n 'attempt=1\|!= 1' execution.py` returns zero hits — no remaining hardcoded values.

### P2-1: PAUSED + DENIED/REPLAN/PENDING_APPROVAL intercepted → ADDRESSED

- `execution.py:1218-1237`: Early check before any resume/reconcile path.
- PENDING_APPROVAL is correctly intercepted, not rejected: a PAUSED job with PENDING_APPROVAL means the fence published a decision requiring confirmation. The plan becomes CONFIRMED only after explicit user approval, at which point this check no longer matches and resume proceeds normally.
- The check is inside `customer_lock` (line 166) — no race with concurrent plan status changes.
- Reads `evaluations.decision` directly from DB (line 1227-1231), independent of any in-memory `decision` dict.

### P2-3: Reconciliation writes terminal dedup → ADDRESSED

- `execution.py:1324-1336`: After `_handle_reconcile` returns 200, `_write_request_dedup_row` is called.
- Inside customer lock: yes — line 1327 is within `async with customer_lock(...)` at line 166.
- Independent short transaction: `_write_request_dedup_row` at line 87-107 does `await conn.rollback()` then `async with conn.begin()` — a fresh sub-transaction on the lock-holding connection.
- Won't overwrite terminal rows: `on_conflict_do_update ... WHERE status_code != 200` (line 105) upgrades non-terminal markers, skips existing terminal ones.
- Only `status_code == 200` results trigger the write (line 1326); non-200 reconciliation outcomes leave the non-terminal marker — acceptable (same request_id can't be reused anyway).

### New issues from this diff: none

- No double-execution risk: the resume STATE_CHANGED path writes non-terminal dedup before `_handle_state_changed`, which only fences (no L3 write).
- No transaction-in-HTTP: `_handle_state_changed` opens its own transactions; no L3 call is inside a write transaction.
- No authorization bypass: P2-1 intercepts non-executable plans before resume; P1-1 correctly fences on STATE_CHANGED instead of routing to UNKNOWN.
- Dedup behavior is consistent between normal path (line 1695-1702) and resume path (line 869-885).

### Unconfirmed

1. `tests/gateway/w4c/db/test_g8_review_fixes.py` — static review confirms test assertions match code changes (attempt==2 on fence, l3_attempt==2, execute_count assertions, PAUSED+REPLAN interception, terminal dedup write), but tests were not run by this reviewer (Docker/PG approval required).