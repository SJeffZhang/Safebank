# L3 C3 standing instruction 执行接线进度

任务：把已完成的 C3 纯函数（schedule_transfer / cancel_scheduled / cancel_subscription）
接入 L3 真实执行链，并在真实 PostgreSQL 上持久化。

## 现状（2026-10-28 接续后实测）

- `make lint`：通过。
- `tests/bankcore/test_standing.py`：**44 passed**（纯函数，与契约效应类型一致）。
- `make test-obligations`：**13 failed / 61 passed**。两组失败：
  - `test_event_jobs_schema.py`（6 条）：对新增 `source_id` / `period_key` 列与
    `(source_id, period_key)` 唯一约束过期——`_valid_row()` 未包含新列（NOT NULL 违规），
    `test_duplicate_payload_hash_rejected` 仍断言旧的 `event_jobs_payload_hash_key`。
  - `test_standing_exec.py`（7 条骨架）：`async def test_*` 写法（本仓库无 pytest-asyncio）、
    `...` 空体、`_prepare_execute` 用 `expected_versions={}` / `clock_version=0`（错误，
    必须从 `load_bank_state` 计算）。

## 已完成（已提交检查点）

`49e0794` — 契约效应类型提升 + event_jobs 表扩展（4 文件）：
- `packages/contracts/.../state.py`：`InsertInstruction` / `SetInstructionStatus` /
  `ScheduleEventJob` 从 FUTURE_EFFECTS 移除，改为 `Contract` 子类并加入
  `LedgerEffect` 判别联合。
- `packages/bankcore/.../standing.py`：从 contracts 导入三个效应类型，
  `ScheduleEventJob` 携带 `source_id` / `period_key`。
- `services/bank-backend/app/models.py` + `migrations/versions/0004_event_jobs.py`：
  新增 `source_id` / `period_key` 列，唯一约束改为 `(source_id, period_key)`
  （`event_jobs_source_period_key`）。

## 剩余待补（未提交）

1. `banking.py` `step_result` 缺 3 个分支（schedule_transfer / cancel_scheduled /
   cancel_subscription），现 `else` 回退读 `args.card_id` 会崩溃。
2. `state.py` `persist` 的 `ScheduleEventJob` 分支：契约字段
   （job_id/instruction_id/customer_id/account_id/event_id/payload_ciphertext/payload_hash）
   与 event_jobs 表列（id/rank/immutable_payload/status/attempt_count/lease_until_wall）
   不匹配，需映射。✅ 已修：job_id→id、rank=0、immutable_payload={"ciphertext":…}、
   status="PENDING"、attempt_count=0，丢弃 instruction_id/customer_id/account_id/event_id。
   注意：`InsertInstruction` 的 `effect.target`（InstructionTarget）在库中**无独立表**——
   targets 由 `_load_obligations` 重载时按 payee/to_account 推导，persist 只写
   `effect.instruction` 即可，无需新迁移（原「instruction_targets」为错误前提）。
3. `apply_schedule_transfer` / `apply_cancel_scheduled` / `apply_cancel_subscription`
   未产出 `OperationEvent`（测试场景 1 要求 operation_events 行）。
4. 结果值未接线：`commitment_30d_cents`（ScheduleResult）、
   `monthly_saving_cents` / `annual_saving_cents`（CancelSubscriptionResult）。
5. no_op 路径返回 `StepResult`-as-effect（会破坏 persist），与已提交纯函数测试
   断言冲突，需定案。
6. `test_standing_exec.py`：改为 `def test_*` + `asyncio.run()` 包装，
   `_prepare_execute` 用 `resolve_scope` + `load_bank_state` 计算 expected_versions /
   clock_version（参照 `tests/integration/test_execution.py::commands()`）。
7. `test_event_jobs_schema.py`：更新为 `source_id` / `period_key` 新 schema。

未运行：`make up` / `make test`（全量）——因接续时先按任务书跑与本任务相关的
`make test-obligations` 与 `test_standing.py`，全量回归留待补完后再跑。
