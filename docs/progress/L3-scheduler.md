# L3 Scheduler — 进度

## 已完成

- `services/bank-backend/app/scheduler.py`（307 行，已提交 `79942b4`）：
  - `Scheduler.scan_due(conn, until)`：幂等创建 SCHEDULED_TRANSFER（rank 3）与 MERCHANT_CHARGE（rank 4）event_jobs，唯一键 `(source_id, period_key)`，`ON CONFLICT DO NOTHING`。
  - `Scheduler.run_once()`：按 `(due_at, rank, id)` 认领下一个 PENDING/RETRYING job，短事务内取真实租约（RUNNING、lease_until_wall、attempt_count+1）并 COMMIT，事务外 POST gateway S1 `/internal/scheduled/authorize`（`X-Service-Token: SCHEDULER_SERVICE_TOKEN`）；终态 → DONE，RETRYING/HTTPError → 退避重排。
  - `Scheduler.recover()`：启动时把过期租约的 RUNNING job 重置为 RETRYING。
  - `ClockAdvancer`：DEMO/EVAL only，内存进度、asyncio.Lock 串行，只前进不倒退，逐 due 事件步进并 run_once。
  - 使用 `pg_insert`（SQLAlchemy PostgreSQL dialect）支持 `on_conflict_do_nothing`。

- `tests/obligations_data/test_scheduler.py`（13 项真实 PG 测试，已提交）：
  - `TestScanDue`（3）：scan_due 为每个 due SI 创建 PENDING job + 幂等 + MERCHANT_CHARGE。
  - `TestRunOnce`（4）：租约并分发、空闲返回 None、RETRYING 重排、并发 skip_locked。
  - `TestRecover`（2）：过期租约恢复、活跃租约保持。
  - `TestClockAdvance`（4）：前进并处理、不倒退、request_id 幂等、未知 job 返回 None。

- `app/main.py` 接线（本文提交）：
  - `create_app` 新增参数 `scheduler_enabled`、`s1_url`、`s1_token`。
  - 构造 `Scheduler` 与（demo 模式下的）`ClockAdvancer`。
  - Demo/EVAL 模式注册：`POST /internal/clock/advance`、`GET /internal/clock/advances/{job_id}`。
  - `SCHEDULER_ENABLED=true` 时 lifespan 启动恢复 + 后台循环（每 5 秒 run_once，on shutdown 取消）。

## 实际命令结果

- `make lint`：ruff check + ruff format 通过（446 files）。
- `make test`：3445 passed / 936 skipped。
- `make test-obligations`：188 passed（含 13 项 scheduler 测试），1 条既有 FK 环 SAWarning。
- 未运行 `make test-integration` / `make up` / Docker（白名单禁令）。

## 未完成

- S1 回调接口 `/internal/scheduled/authorize` 仍由网关侧实现（L2 W5 任务）。
- Demo 模式下 scheduler 后台循环由 `SCHEDULER_ENABLED` 控制，尚未在实际 Docker 栈中验证。
- 未做 make test-integration / make up / Docker compose 测试。
