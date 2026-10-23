# L2-S3a 实现独立评审

评审范围 `cca074b..537664a`（`feat/l3-obligations-data`，head 537664a）。只读：读任务书 `S3a-obligations-data.md`、提案 `S3a-model-proposal.md`、ADR-009、PRD §8.3 与实现 diff；未改任何文件，未运行 PG（本沙箱无 PG），仅做源码与测试逐条核对。

## 结论：REQUEST_CHANGES

无已证实的资金/版本破坏或数据泄漏 bug；冻结区、外部写工具、历史迁移字节、reserve 公开签名与核心边界（rank=3/4、前缀峰值、负 max_outflow、days=7、不可变、单一日历 `_business_time`）均保留。但两项**获批提案明确要求的验收门禁缺失**，且 OWN/OTHER 所有权与隐私边界在真实 PG/HTTP 层未覆盖。

## P1

**P1-1 旧 0002 带真实数据升级保留门禁不存在。**
任务书 §A、提案 §5 明确要求：先在 0002 用真实核心 seed/执行路径生成 posted/pending、transactions、PREPARED/COMMITTED/CANCELLED intents、evidence_history、plan_execution、operation/outbox、客户/clock 版本，采集基线 → upgrade head 两次 → 逐字段相等。实测 `tests/obligations_data/conftest.py:122-124` 只对**空库**跑 `upgrade head` 两次；`tests/obligations_data/` 中无任何「0002 带数据 → 0003 → 数据保留」用例。失败场景：若未来某迁移把 ALTER/GRANT 写得对非空旧库有副作用（如唯一键在真实数据上冲突、TRUNCATE 式重建），本组件无法发现。修法：新增临时 tmpfs PG 用例，按提案 §5 顺序 seed 0002 真实数据、采集基线、upgrade 两次、逐字段/行数/序列/权限比对，并复跑 prepare/query/reconcile/replay。

**P1-2 真实 prepare/execute → HTTP snapshot 链未实现。**
提案 §6 核心链要求「真实 prepare/execute API 提交该转账 → 再 HTTP snapshot」。实现 `tests/obligations_data/test_obligations_data.py:174-189` 直接 `post_transfer`+`persist`（seed 同路径）绕过 L3 prepare/execute HTTP，且**未增客户版本**（版本恒为 0）。进度文档 §未覆盖 也自认。失败场景：真实执行路径会写 intents/outbox/operation 并 bump state_versions，与 obligations 组合后 snapshot/coverage 校验（`reserve.py:336` 比较 `covered_customer_ids` 与 `state.versions`）未在真实执行后验证过。修法：用例经 `/internal/plans/prepare`+`execute` 提交 300000 转账，再 `/internal/snapshot` 反序列化并断言 reserve=4500/max_outflow=295500 与版本已增。

## P2

**P2-1 OWN/OTHER 所有权分支与第三客户非递归在真实 PG/HTTP 未覆盖。**
`tests/obligations_data/fixtures.py` 只造 `si_zs_mom`（payee target=None → EXTERNAL）与 mandate，无 `to_account_id`（OWN）也无 payee→内行 target（OTHER）。故 `app/state.py:150-197` 的 OWN/OTHER 分类与 `peer` 查询在生产 loader 中零覆盖。提案 §6 矩阵第 4 项（第三客户不进 versions/customers/accounts、哨兵电话/别名不进响应、单客户 snapshot 不含他人义务）未实现。修法：fixture 增一条 OTHER 定时指令指向第三客户（如 cus_ww→cus_sb），断言 snapshot 响应不含 cus_sb 电话/别名、covered 不含 cus_sb；增一条 OWN 本人互转指令。

**P2-2 RR 并发一致性测试缺失（提案 §6 矩阵第 8 项）。**
`test_empty_scope_returns_complete_empty_snapshot`（`test_obligations_data.py:248-269`）设了 REPEATABLE READ READ ONLY 但无并发更新竞态。修法：持有 RR 只读事务首读后，另一事务改 billing+增版本，断言同 conn loader 只见旧版本/旧账单，新 HTTP snapshot 见新值，且只读事务写入被拒。

**P2-3 混合时区回归测试被删除。**
`tests/bankcore/test_reserve.py` 相对 cca074b 删除 `test_mixed_offset_window_and_end_at_compare_instants`、`test_mixed_offset_report_keeps_merchant_and_scheduled_window_boundaries`、`test_offset_inputs_still_reject_wrong_shanghai_time_or_anchors`；仅剩 `test_report_offsets_preserve_same_seven_day_obligations`（:737-751）用统一 offset。多 offset 混用与 offset 下错误上海钟面/锚点拒绝的边界不再覆盖。修法：在 raw snapshot 路径恢复混合 offset 与「next_run_at 与本地 time_of_day/anchor 不一致须拒绝」用例（`_check_calendar` 经 `_validate_scheduled_raw`/`_validate_mandate_billing` 的 raw 路径目前无该拒绝测试）。

**P2-4 billing.active=False 且 mandate ACTIVE 不预留未测。**
代码 `reserve.py:299-300` `if not billing.active: continue` 与提案 §6 矩阵第 2 项组合无测试（全库无 `active=False` 用例）。修法：`raw_billing(active=False)` + ACTIVE mandate 断言 reserve 为 0、账单仍在。

**P2-5 DISABLED 授权下 SUCCESS 历史保留未测。**
F1 矩阵 `test_run_f1_matrix_...`（`test_obligations_data.py:351-356`）只把授权改 `DELETED`；提案 F1 表要求 DELETED 与 DISABLED 两分支。修法：补 DISABLED 场景重取 snapshot 断言 SUCCESS 历史与 last_run 仍在。

**P2-6 S2 未再校验 billing.account_id == mandate.from_account_id。**
`_validate_mandate_billing`（`reserve.py:234-253`）校验 customer_id/merchant_id 但不校验 account_id 与 from_account_id；借方腿取 `billing.account_id`（`reserve.py:313`）。生产由复合 FK `standing_instructions_billing_owner_fkey` 保证，但提案 §4 要求 S2 对 model_copy/手工构造输入防御。修法：`_validate_mandate_billing` 增 `billing.account_id != item.from_account_id` 拒绝。

**P2-7 instruction_runs 的 UPDATE 拒绝与 bank_ro 无写权限未测。**
`test_migration_and_runtime_roles`（`test_obligations_data.py:82-84`）只探 CREATE/TRUNCATE/DELETE；append-only 触发器 `BEFORE UPDATE OR DELETE` 的 UPDATE 路径及 bank_ro 的 INSERT/UPDATE 拒绝未断言。修法：补 `UPDATE instruction_runs` 拒绝与 bank_ro 写拒绝用例。

## 未确认（无证据，不列为发现）

- 空 merchant_ids 时 `id.in_([])` 的 SQL 行为：`test_empty_scope_returns_complete_empty_snapshot` 通过（协调者 25 passed 含此项）可证明空集合分支工作，未另行验证 SQL 文本。
- `docs/contracts/v1.json` 的 `LedgerTransfer` 段 `-`/`+` 为纯 diff 对齐伪影：已逐段核对旧文件（`git show cca074b:docs/contracts/v1.json` 4207-4340）确认 `step_id`/`required` 在新旧文件一致，真实增量仅七个新 obligation `$defs` + `BankState.obligations` 属性，read_tools/write_tools/llm_tool 未触碰。
