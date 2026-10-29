# L3 Scheduler — 进度

## 已完成

- `services/bank-backend/app/scheduler.py`（新文件，306 行）已实现并提交检查点 `79942b4`：
  - `Scheduler.scan_due(conn, until)`：幂等创建 SCHEDULED_TRANSFER（rank 3）与 MERCHANT_CHARGE（rank 4）event_jobs，唯一键 `(source_id, period_key)`，`ON CONFLICT DO NOTHING`。
  - `Scheduler.run_once()`：按 `(due_at, rank, id)` 认领下一个 PENDING/RETRYING job，短事务内取真实租约（RUNNING、lease_until_wall、attempt_count+1）并 COMMIT，事务外 POST gateway S1 `/internal/scheduled/authorize`（`X-Service-Token: SCHEDULER_SERVICE_TOKEN`）；终态 → DONE，RETRYING/HTTPError → 退避重排。
  - `Scheduler.recover()`：启动时把过期租约的 RUNNING job 重置为 RETRYING。
  - `ClockAdvancer`：DEMO/EVAL only，内存进度、asyncio.Lock 串行，只前进不倒退，逐 due 事件步进并 run_once。
  - 修复 `_count_due` 的 `select(update.count())` → `select(func.count())`。

## 未完成

- `tests/obligations_data/test_scheduler.py`（真实 PG 测试）未写。
- `app/main.py` create_app 接线（demo_mode、Scheduler 构造、`/internal/clock/advance`、`/internal/clock/advances/{id}`、SCHEDULER_ENABLED 后台循环）未做。
- 未运行任何测试 / make 门禁（本会话未跑测试）。

## 实际命令结果（本会话）

- `git add services/bank-backend/app/scheduler.py && git commit` → 提交 `79942b4`，1 file changed, 306 insertions。
- 其余命令（lint/test）尚未运行。
