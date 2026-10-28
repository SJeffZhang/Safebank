# L3-C3 独立评审报告

范围：`git diff origin/main...3e7d89a`（L3 C3：schedule_transfer / cancel_scheduled / cancel_subscription，event_jobs 表）
评审基准：PRD §8.3（event_jobs 列与 rank 表，line 1287）、§9.4（W3/W4/W11）。

## 结论

**REQUEST_CHANGES** — 1 条 P1（生产 persist 把 SCHEDULED_TRANSFER 的 rank 写死为 0，违反 PRD §8.3 固定 rank 表 SCHEDULED_TRANSFER=3），其余核对项通过，另有 5 条 P2 供合并前评估。

## P0

（无）

## P1

1. **event_jobs 的 rank 写死 0，违反 PRD rank 表** — `services/bank-backend/app/state.py:471`。persist 对每条 `ScheduleEventJob` 都 `rank=0` 落库，而 PRD §8.3（line 1287）固定 `SCHEDULED_TRANSFER=3`（FUND_CONFIRM=0…DEMO_EVENT=6），并明确「rank 为整数，不能按优先级字符串字典序排」用于 `(due_at, rank, event_id)` 排序。后果：C3 产出的定时转账 job 在 due_at 相同时会排在基金确认/结算/到期（rank 0/1/2）之前，W5 调度器接入后将按错误顺序处理。`ScheduleEventJob` 契约本身不带 rank 字段（`packages/bankcore/src/safebank_bankcore/standing.py:357-369` 构造时无 rank），persist 无法得知 kind→rank 映射，需要在此处加映射（或给契约补 rank）。佐证：schema 测试 `tests/obligations_data/test_event_jobs_schema.py` 的 `_valid_row` 对 kind=SCHEDULED_TRANSFER 用 `rank=3`，说明测试作者已知正确值，但没有测试校验生产路径写入的值。

## P2

1. **grant_scope_hash 未覆盖 end_at** — `packages/bankcore/src/safebank_bankcore/standing.py:206-237`。W3 参数含 `end_at`，但 hash 只覆盖 inst_id/customer/kind/from/ownership/target_fp/period/time_of_day/amount/first_run_at，不含 end_at。当前无更新工具可改 end_at，且 hash 绑定了唯一的 inst_id，故今日不可利用；但「覆盖 PRD 所列字段（含 first_run_at）」字面上漏了 end_at（first_run_at 已覆盖）。
2. **payload_ciphertext 实为明文** — `standing.py:356-368` 生成 `payload_data = f"{inst_id}|{amount}|{from_account}"` 存入 `payload_ciphertext`，`state.py:473` 落库为 `immutable_payload={"ciphertext": <明文>}`。命名误导（非密文），但 payload 只是非敏感指令引用，无安全影响；建议改名 `payload`/`payload_text`。
3. **`_target_info` 的 OWN-via-payee 分支与 load 重建矛盾** — `standing.py:108-109` 把「payee 指向本人账户」判为 OWN 并接受；但 `services/bank-backend/app/state.py:192` 加载重建时对 `payee→本人账户` 直接 `raise ValueError`。经 `apply_add_payee`（banking.py）已拒绝本人账户收款人，正常流程不可达；但若种子/旧数据存在此类 payee，创建会成功、重启后加载整客户崩溃。建议在 `_target_info` 同样拒绝 payee→本人，与 add_payee/load 对齐。
4. **月末/闰年测试名不副实** — `tests/bankcore/test_standing.py:406-429`（`test_monthly_jan31_to_feb_end`、`test_monthly_feb28_leap_year`）只断言 `anchor_day` 与恒真的 `next_run_at == first_run_at`，并未验证 `_recurrences` 把 Jan 31 钳到 Feb 28 / 闰年 Feb 29。共享逻辑 `reserve.py:_next_month`（用 `monthrange`）本身正确（S2 已评审），但这两个测试名夸大覆盖。
5. **payload_hash 唯一约束无直接测试** — `tests/obligations_data/test_standing_exec.py` 只测了 `(source_id, period_key)` 唯一（`test_same_source_period_duplicate_job_rejected`），`event_jobs_payload_hash_key` 仅靠 schema 测试的间接覆盖。极小缺口。

## 已核对项

- **首次执行时间（ONCE/WEEKLY/MONTHLY、业务时区）**：`standing.py:67-79` `_derive_anchors` 全部经 `reserve._business_time`（Asia/Shanghai）换算；ONCE→(None,None,local_time)、WEEKLY→(None,weekday,local_time)、MONTHLY→(day,None,local_time)。`_prep_instruction`（134-136）要求 `first_run_at` 严格晚于 `sim_now`、`end_at ≥ first_run_at`（131-132）。`_check_calendar`（147）校验 anchor/weekday/time 一致性。✅
- **目标所有权**：`_target_info`（82-110）——to_account 必须本人（91-92）；payee 必须为 str 且属本人（98-100）；EXTERNAL/OTHER/OWN 分类正确。✅
- **只能取消本人且正确 kind**：`apply_cancel_scheduled` 先查 customer_id（406-417），再 `isinstance(ScheduledInstruction)` 拒绝 MERCHANT_MANDATE（419-430）；`apply_cancel_subscription` 对称（494-505）。✅
- **重复取消/撤销 no_op 且零效果**：DELETED 状态直接 `return state, (), ()`（432-433、507-508）；测试 `test_cancel_already_deleted_no_op` / `test_revoke_already_deleted_no_op` 断言 `not effects`。✅
- **撤销代扣保留账单**：`apply_cancel_subscription` 只改 instruction.status（527-528），`_make_fresh_snapshot` 仅更新 standing_instructions/instruction_targets，不触碰 billings；DB 级测试 `test_revoke_mandate_changes_status` 断言 `merchant_billing count == 1`。✅
- **grant_scope_hash**：覆盖 customer/kind/from_account/ownership/target_fp/period/time_of_day/amount/first_run_at（206-237），确定性（316-328）、随 amount 变（331-340）、随 first_run_at 变（678+）。first_run_at 已覆盖；end_at 缺失见 P2-1。✅
- **不冻结余额**：effects 仅 `InsertInstruction`/`ScheduleEventJob`/`OperationEvent`/`SetInstructionStatus`，无任何 LedgerTransfer/PostTransfer/freeze 效果（standing.py 全文件）。✅
- **迁移 0004_event_jobs**：`payload_hash` 唯一 + `(source_id, period_key)` 唯一并存（48-49）；权限 `REVOKE ALL` 后 `GRANT SELECT,INSERT,UPDATE` 给 bank_app、`SELECT` 给 bank_ro（60-62），无 DELETE/TRUNCATE；`down_revision="0003_obligations"`，历史迁移文件未改；downgrade 前向不可逆（65-66）。models.py 与迁移 CHECK/约束/列一致。✅
- **接线**：banking.py 三分发进 `SUPPORTED_WRITES` 与 `apply_plan`（441-445），`step_result` 塑形 W3（instruction_id/next_run_at/grant_scope_hash/commitment_30d_cents）、W4（no_op=not bool(effects)）、W11（monthly/annual savings、no_op）；annual = monthly×12（MONTHLY）或 ×52（WEEKLY），与 `BillingPeriod=Literal["WEEKLY","MONTHLY"]` 吻合。persist 写 instruction（model_dump 全字段）、status（UPDATE）、event_jobs（带 source_id/period_key/payload_hash/status=PENDING）。✅
- **同 plan 幂等**：`test_same_plan_retry_20_times_idempotent` 真实 PG 断言 20 次 execute 后 standing_instructions/event_jobs/operation_events 各恰 1 行，`replayed=True`。✅
- **不越客户**：全部 apply 函数校验 `customer_id == ctx.customer_id`；event_jobs 通过 source_id(=inst.id)→standing_instructions.customer_id 关联，无跨客户写入。✅
- **测试真实**：`test_standing_exec.py` 用真实 tmpfs PG（admin/runtime 双连接）+ `svc.prepare`/`svc.execute`，同步测试函数内 `asyncio.run(check())`；`test_preflight_matches_persisted_state` 走真实 `load_bank_state` 往返并比对 grant_scope_hash/next_run_at/amount。✅
- **无删除/放宽断言**：`test_banking.py` 把 TOOL_NOT_ALLOWED 探针从 `schedule_transfer`（现已实现）换成 `subscribe_product`（仍未实现），属正当调整；3 处版本号 0003→0004 为 bump，无断言放宽。✅
