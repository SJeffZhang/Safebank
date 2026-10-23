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

---

# 修复复核（`537664a..f62b677`，当前 head fd82290）

只读逐条核对 9 项修复 + 查新增问题。未运行 PG / make / docker（本沙箱无 PG）；协调者七退出正在跑，本轮不以任何数字冒称通过。

## 逐条结论

- **P1-2 已关闭**：`test_real_http_snapshot_feeds_s2_projection` 改为真实 TCP `/internal/plans/prepare`+`/execute`（正确 InteractiveEvidence、GREEN），再 `/internal/snapshot`→S2；断言双方 version 各增一次（`state.versions == {"cus_zs":1,"cus_ww":1}`）、intent COMMITTED、plan_execution/operation/outbox 落盘、reserve 4500 / max 295500。原 post_transfer+persist 样本已删。
- **P1-1 门禁已存在且有效，但 seed 代表性有残余**：`tests/obligations_data/test_0002_upgrade.py` + `obligations_0002_db` fixture（先 `upgrade 0002_identity`）确有：真实 ledger/persist 生成 posted/pending（含活 pending `trf_pending` PENDING + `trf_posted` POST_PENDING）；三类 intent PREPARED/COMMITTED/CANCELLED、plan_execution、operation/outbox、客户/clock 版本；`_capture` 采集 12 旧表行 + 2 序列 + 表权限基线 → `upgrade head` 两次 → `after == baseline`；再真实 prepare/query/reconcile/replay（含 COMMITTED `replayed=True`）。**未用最新 head 造数冒充旧库，未改生产 loader。**
  - 残余（非阻塞）：intent/result/outbox 为 raw SQL 手造「ExecutionService 行形」，**evidence_history 全为 `[]`**（test_0002_upgrade.py:117 的 `history="[]"` 用于三类 intent），`authorization_evidence` 对 PREPARED/CANCELLED 为 `{}`，`result_json` 为 `versions:{}`、`steps:[]`。真实「非空 evidence_history」只出现在重审批 attempt+1 路径（`execution.py:126-145` 把旧 evidence 归档进 history）；本 seed 全为 attempt=1 单次状态，**未往返验证非空 evidence_history / 真实已执行结果**。字节级基线对比本身与值无关（0003 不触碰 execution_intents，故核心属性仍被证明），此条是「真实核心执行路径生成」的代表性不足，非破坏门禁。修法（可选）：用未修改的 ExecutionService 在 0002 上 prepare(attempt1)→prepare(attempt2)→execute 生成真实 attempt+1 历史，或至少 seed 一条非空 evidence_history + 非空 result.versions。
- **P2-1 已关闭**：`test_ownership_other_target_does_not_recurse_third_customer` 造 OWN（si_zs_own→acc_zs_sav）/OTHER（si_ww_sb→cus_sb）指令，fixture 给 cus_sb 设哨兵电话 13900009999 + 哨兵收款人名/别名；真实 HTTP 断言 covered 不含 cus_sb、cus_sb 不进 customers/accounts/versions、哨兵电话/别名/收款人名不进响应、OWN 贷借腿正确（acc_zs_sav reserve 0）。
- **P2-2 已关闭**：`test_repeatable_read_snapshot_isolation` 真实 RR READ ONLY 首读后另一事务改 billing+版本，同 conn loader 仍旧值、只读写被拒（UPDATE state_versions 抛 DBAPIError）、新 HTTP snapshot 见新值。
- **P2-3 已关闭**：raw 路径恢复 `test_raw_mixed_offset_window_and_end_at_compare_instants`、`test_raw_mixed_offset_report_keeps_merchant_and_scheduled_window_boundaries`、`test_raw_wrong_shanghai_time_or_anchor_is_rejected`（scheduled 与 billing 两路 `_check_calendar`）；legacy View 路径仍由 `test_invalid_active_schedule_is_rejected_instead_of_omitted` 覆盖。
- **P2-4 已关闭**：`test_inactive_billing_is_not_reserved_but_billing_stays`。
- **P2-5 已关闭**：F1 矩阵参数化 `["DELETED","DISABLED"]`，均保留 SUCCESS 历史与 last_run 来源。
- **P2-6 已关闭**：`reserve.py` `_validate_mandate_billing` 增 `billing.account_id != item.from_account_id` 拒绝（生产代码 + 红绿测试 `test_mandate_billing_account_must_match_source_account`）。
- **P2-7 已关闭（附测试卫生 N1）**：`test_runs_append_only_and_bank_ro_read_only` 覆盖 UPDATE 拒绝（owner 级触发器）与 bank_ro INSERT/UPDATE 拒绝。

## 新增发现

- **N1（P2，测试卫生，需协调者七退出定点确认）**：`test_runs_append_only_and_bank_ro_read_only`（test_obligations_data.py:480-481）的 UPDATE 断言直接在 `async with admin.begin()` 事务内执行失败语句，**未用 `begin_nested()`**；与本文件既有模式（`test_run_f1_matrix` :344-346、`test_migration_and_runtime_roles` :85-87 均用 `begin_nested()` 包裹预期失败）不一致。该失败语句会中止外层事务：PostgreSQL 服务端把后续 COMMIT 当作 ROLLBACK（无错），但 asyncpg/SQLAlchemy 可能抛 `InFailedSQLTransactionError`，且 run_x 的 INSERT 会被一并回滚（本测试只断言拒绝、不断言持久，故回滚无害，但断言与事务状态耦合脆弱）。建议把该 UPDATE 包进 `begin_nested()`；协调者七退出中 `make test-obligations` 若含此项失败，即按此修。

## 复核结论：APPROVE（条件通过，两条 P2 残余）

9 项均已实质关闭；无新引入的资金/版本/隐私回归。未关闭项仅两条非阻塞 P2：(a) P1-1 的 0002 seed 未含非空 evidence_history / 真实执行结果，未用未修改的 ExecutionService 生成重审批历史，代表性不足；(b) N1 测试卫生（UPDATE 断言未用 savepoint）。若协调者七退出全绿且接受 (a) 的「raw SQL 行形 + 字节级基线对比」为满足门禁，即可发布；否则按上两项各做一次最小补测。

---

# 复核三轮（`fd82290..f7223d0`，关闭两条 P2 残余）

只读核对，未运行 PG / make / docker。f7223d0 仅改 tests + 两份文档，**无任何生产代码漂移**（services/、packages/ 零改动）。

## (a) P1-1 seed 真实执行代表性 —— 已关闭

- **历史 helper 来源准确**：`tests/obligations_data/legacy_loader.py` 声称「逐字取自 `cca074b:services/bank-backend/app/state.py`，blob `e998108e0026dc113b57b334c87a3bea97845e36`」。实测 `git rev-parse cca074b:services/bank-backend/app/state.py` = `e998108e0026dc113b57b334c87a3bea97845e36`，与声明一致；逐行比对 cca074b state.py:85-203 的 `load_bank_state` 与 legacy_loader.py:36-152 完全一致（仅 import 收窄到该函数所需符号，无逻辑改动），只读 P1 旧表、不碰 0003 义务表。
- **patch 范围不污染**：`test_0002_upgrade.py:157-230` 仅在 `_seed_0002` 内 `try/finally` 中 `execution.load_bank_state = legacy_load_bank_state`，`finally` 恢复 `original`；且只重绑 `app.execution.load_bank_state`（execution.py:17 `from .state import load_bank_state` 的模块全局，prepare/execute 经行 80/218 引用它），不动 `app.state.load_bank_state` 生产 loader。post-upgrade 的 query/replay/reconcile（:271-296）在恢复后运行。异常时 finally 亦恢复。
- **真实执行路径生成非空 history + 真实 result**：`_seed_0002` 走未修改 `ExecutionService` 的 `prepare(attempt1)→prepare(attempt2)→execute`（:161-198）产生 COMMITTED——attempt+1 路径（execution.py:126-145）真实归档非空 `evidence_history`，`result_json` 带真实 `versions={"cus_zs":1,"cus_ww":1}` 与 steps，operation/outbox 由 execute 的 persist 真实落盘；`prepare`+`reconcile` 生成 PREPARED/CANCELLED；posted/pending 仍真实 ledger/persist 且保留活 pending（`trf_pending` PENDING + `trf_posted` POST_PENDING）。断言 `assert history`（:268）与 `result_json["versions"]=={"cus_zs":1,"cus_ww":1}`（:269）确证非空。基线→`upgrade head` 两次→`after==baseline`（12 旧表行 + 2 序列 + 权限）不变，再对原真实任务 query/replay(`replayed=True`)/reconcile 通过。

## (b) N1 权限测试卫生 —— 已关闭

`test_runs_append_only_and_bank_ro_read_only` 现把 owner UPDATE 拒绝与 bank_ro INSERT/UPDATE 拒绝各自包进 `begin_nested()`，并断言真实 SQLSTATE（触发器 `23514`、权限 `42501`）；owner UPDATE 后另校验 `run_x` 行仍提交且 code 不变（`MANDATE_NOT_ACTIVE`），bank_ro 只读仍可见 run_x。与外层事务解耦，不再有 commit-on-abort 风险。

## 复核三轮结论：APPROVE

两条 P2 残余均已按证据关闭；历史 helper 来源 blob 哈希与逐行内容核对一致、patch 仅限 0002 造数期且 finally 恢复、未污染生产 loader、无生产代码漂移。未发现新引入问题。前两轮 9 项与 N1 全部关闭，无未关闭项。建议协调者按其七退出全绿后按既有授权发布。
