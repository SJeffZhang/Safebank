# L3 Scheduler Review — Independent Read-Only

**Review date**: 2026-10-29  
**Scope**: `git diff origin/main..HEAD` (services/bank-backend/app/scheduler.py, main.py, tests/obligations_data/test_scheduler.py, docs/progress/L3-scheduler.md)  
**Verdict**: **APPROVE** with P2 findings below (no P0/P1).

---

## Checks (per brief)

| # | Requirement | Finding |
|---|---|---|
| 1 | Job creation idempotent via `(source_id, period_key)` | ✅ `_insert_job` uses `ON CONFLICT DO NOTHING` on the unique constraint `(source_id, period_key)`. `test_scan_due_is_idempotent` verifies second scan returns 0. |
| 2 | Ordering `(due_at, rank, id)` with integer rank | ✅ `_claim_next` orders by `due_at`, `rank`, `id`. `rank` is set from `EVENT_KIND_RANK[kind]` (stable integer dict in contracts). |
| 3 | Lease taken in short committed TX; S1 HTTP made with **no open transaction/lock** | ✅ `_claim_next` runs in `db.begin()` (committed). `_post_s1` runs outside with `async with httpx.AsyncClient`. No DB connection held during HTTP. |
| 4 | Concurrent `run_once` cannot process the same job (`FOR UPDATE SKIP LOCKED`) | ✅ `with_for_update(skip_locked=True)` on the claim query, plus `status='RUNNING'` guarding. `test_two_concurrent_run_once_never_double_process` verifies. |
| 5 | Expired-lease recovery | ✅ `recover()` resets `RUNNING` jobs with `lease_until_wall <= now` to `RETRYING`. `test_recover_resets_expired_running_jobs` / `test_recover_ignores_active_leases` cover both sides. |
| 6 | RETRYING backoff | ✅ `_backoff(attempt)` returns 1/2/4s. `_requeue` sets `lease_until_wall = now + backoff`. |
| 7 | Terminal statuses mapped to DONE | ✅ `TERMINAL = {"SUCCESS", "BLOCKED", "FAILED_INSUFFICIENT", "SKIPPED_REAPPROVAL"}` → `_mark_done`. Non-terminal → `_requeue(RETRYING)`. |
| 8 | Clock never moves backwards; advances serially (`version+1`, commit before callbacks) | ✅ `_run` does `version += 1` then `set_clock(conn, next_due, version)` inside `db.begin()`. `run_once()` reads clock fresh in separate `connect()`. `test_advance_never_moves_backward` asserts `sim_now >= NOW`. |
| 9 | Clock routes only in DEMO/EVAL | ✅ `ClockAdvancer` created only when `demo_mode=True` (main.py:92). Clock routes registered inside `if demo_mode:` block (main.py:277). |
| 10 | Background loop off unless `SCHEDULER_ENABLED` | ✅ `scheduler_enabled` from env flag, `_task` only created when true (main.py:99–101). |
| 11 | Scheduler never executes money itself, only via S1 | ✅ The scheduler only POSTs to `/internal/scheduled/authorize`. No ledger operations. |

---

## Findings

### P0 — none

No correctness, data-loss, or safety-blocking issues found.

### P1 — none

All functional requirements verified as implemented correctly.

### P2 (non-blocking)

1. **`_claim_next` 的 WHERE 含 `RETRYING`，但 `event_jobs_pending_due` 索引只覆盖 `status='PENDING'`** (`models.py:601–608`, `scheduler.py:151–160`): 当有 `RETRYING` 状态 job 时，查询需全表扫描或走其他索引。当前负载极低（单 worker、最多数十 job），无性能影响。若未来扩展至数千 job 或高频轮询，需补 `(status IN ('PENDING','RETRYING'), due_at, rank, id)` 的复合索引。

2. **`_backoff` attempt=0 时返回 1 秒** (`scheduler.py:34`): `max(attempt - 1, 0)` 使 attempt=0→0、attempt=1→0、两者都返回 min(1,4)=1。对 attempt=0（`_requeue` 中首次失败后 attempt 未递增）退避退化到 1 秒，等同于 attempt=1。实际路径中 `_requeue` 传入的是 `job["attempt_count"]`（来自 `_claim_next` 的 `attempt_count + 1`），所以 attempt≥1，此边界仅在 `_requeue` 被独立调用且传入 0 时触发，不影响现有路径。

3. **`_run` 中 `_count_due` 查询缺少 `lease_until_wall` 不过滤** (`scheduler.py:313`): `_count_due` 统计 `status IN PENDING,RETRYING,RUNNING AND due_at <= target_at`，但未排除 `lease_until_wall IS NOT NULL AND lease_until_wall > now()` 的 RUNNING job（正在被另一 worker 处理中）。`remaining` 计数可能虚高包含已 lease 但仍 RUNNING 的 job。影响限于 `ClockAdvancer.status()` 返回的 `remaining` 字段（仅演示/评测观察指标，不驱动业务逻辑）。

4. **`test_run_once_returns_none_when_nothing_due` 依赖 `NOW` 默认值不变** (`test_scheduler.py:223–240`): 测试假设 `NOW = datetime.fromisoformat("2026-10-15T09:00:00+08:00")` 先于所有 job 的 `due_at`，未显式验证 `sim_now` 值。如果 `install_obligations` 意外修改了 clock，测试可能误通过。Fixture 中 `install_core` 固定了 `sim_now=NOW`，当前安全，但依赖隐式顺序。

5. **`payload_hash` 唯一约束与 `source_id+period_key` 唯一约束功能重叠** (`models.py:598–599`): 两条 `UNIQUE` 各自独立，前者阻止完全不同内容的 job 共用同一个 hash（极端碰撞），后者是真正的业务幂等键。双重唯一对插入性能有轻微影响（两个 B-tree 索引），当前 job 量可忽略。

---

## Test Run

Attempted `OBLIGATIONS_DB_TESTS=1 .venv/bin/pytest tests/obligations_data/test_scheduler.py -q` — blocked by auto-mode permission. Static analysis of test code covers all 12 test cases:

| Test | What it proves | Quality |
|---|---|---|
| `test_scan_creates_jobs_for_due_instructions` | SI scan → PENDING jobs with correct kind/status/source_id/period_key | ✅ |
| `test_scan_due_is_idempotent` | Second scan returns 0 new rows | ✅ |
| `test_scan_creates_merchant_charge_jobs` | Both SCHEDULED_TRANSFER and MERCHANT_CHARGE kinds created | ✅ |
| `test_run_once_leases_and_dispatches` | Lease → S1 called → DONE, attempt count incremented | ✅ |
| `test_run_once_returns_none_when_nothing_due` | No due jobs → None returned | ✅ |
| `test_run_once_requeues_on_retrying_response` | Non-terminal S1 response → RETRYING | ✅ |
| `test_two_concurrent_run_once_never_double_process` | `SKIP LOCKED` prevents double-claim | ✅ |
| `test_recover_resets_expired_running_jobs` | Expired lease RUNNING → RETRYING | ✅ |
| `test_recover_ignores_active_leases` | Active lease RUNNING untouched | ✅ |
| `test_advance_moves_clock_and_processes_jobs` | Clock advances, jobs processed, version incremented | ✅ |
| `test_advance_never_moves_backward` | Past target → clock unchanged | ✅ |
| `test_advance_idempotent_request_id` | Same request_id → same job_id | ✅ |
| `test_advance_unknown_job_returns_none` | Unknown job_id → None | ✅ |

Test coverage is thorough: happy path, idempotency, concurrency, recovery (both expired and active lease), clock safety, and request dedup. One gap: no test for RETRYING backoff values (P2 above).

---

## Summary

The scheduler is clean, minimal, and correctly implements every constraint from the brief:

- **Job creation**: Deterministic ID + `ON CONFLICT (source_id, period_key) DO NOTHING` for idempotency.
- **Ordering**: `(due_at, rank, id)` with integer rank from shared `EVENT_KIND_RANK`.
- **Lease + dispatch separation**: Lease in committed TX, HTTP outside any TX/lock — no cross-service deadlock risk.
- **Concurrency**: `FOR UPDATE SKIP LOCKED` ensures no double-processing.
- **Recovery**: Startup `recover()` resets expired leases.
- **Backoff**: Exponential 1/2/4s cap.
- **Clock safety**: Version+1, committed before callbacks, never backward.
- **DEMO/EVAL gating**: Clock routes and `ClockAdvancer` only when `demo_mode` is true.
- **SCHEDULER_ENABLED gating**: Background loop off unless env flag is set.
- **No money execution**: Scheduler only calls S1 via HTTP; all financial operations go through the gateway.

**Verdict**: APPROVE — no P0 or P1 issues. Five P2 findings are minor (index coverage, edge-case arithmetic, remaining counter, test implicit dependency, dual unique constraints). All are non-blocking and do not affect correctness or safety.