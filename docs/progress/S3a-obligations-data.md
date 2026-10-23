# S3a 义务存储与真实快照 — 进度与验收记录

日期：2026-10-24。实现者：DeepSeek V4 Pro（Claude Code 无头 worker）。基线 main `cca074b`，
worktree `feat/l3-obligations-data`。按 `docs/plans/briefs/S3a-obligations-data.md`、
ADR-009 与已 APPROVE 的 `docs/plans/briefs/S3a-model-proposal.md` 实施，未另发明设计。

## 做了什么

1. **contracts 内部义务模型**（`packages/contracts/src/safebank_contracts/obligations.py`）：
   `ObligationSnapshot` 及 `Merchant / MerchantBilling / ScheduledInstruction /
   MerchantMandate / InstructionRun / InstructionTarget`，含 `covered_customer_ids`、
   四个 typed 集合与 `instruction_targets`；`BankState.obligations: ObligationSnapshot | None`
   （默认 None=未加载）。`FrozenMap` 移到 `base.py` 共用。`LocalExecutionTime` 接受 `time` 或
   ISO 字符串（拒绝带 tzinfo），保证 JSON 往返。
2. **L3 四表 metadata + 0003_obligations 迁移**：`merchants / merchant_billing /
   standing_instructions / instruction_runs`，父表候选唯一键
   （accounts/payees/transfers）、所有权复合 FK、周期/字段组合 CHECK、`instruction_runs`
   只追加触发器与运行角色 DML 授权。历史 0001/0002 与 schema_v1 字节不变。
3. **唯一真实 loader**（`app/state.py`）：同事务读取四表构造 `ObligationSnapshot`，
   最小 `instruction_targets`（OWN/OTHER/EXTERNAL），并派生旧 `instructions/subscriptions`
   视图（仅请求客户、固定 target_display 不携带联系人文字）。`db.py` readiness 升级到
   `0003_obligations`。
4. **S2 同源适配**（`bankcore/reserve.py`）：`projected_shortfall` 只消费 raw snapshot，
   obligations 为 None 或 covered 与 `state.versions` 不一致时拒绝；OWN 信用腿来自 targets，
   OTHER/EXTERNAL 不保证入账；保留 rank=3/4、前缀峰值、负 max_outflow 与私有日历迭代器；
   `scheduled_occurrences` 保持 View 签名兼容。
5. **专项测试**：`tests/obligations_data/`（纯模型 18 项 + 真实 tmpfs PG/HTTP 7 项），
   `make test-obligations` 目标，CI p1-postgres 追加一行。

## 文件清单

新增：`packages/contracts/.../obligations.py`、`services/bank-backend/migrations/versions/0003_obligations.py`、
`tests/bankcore/obligations_fixtures.py`、`tests/obligations_data/{conftest,fixtures,test_models,test_obligations_data}.py`。
修改：`contracts/base.py`、`contracts/state.py`、`bankcore/reserve.py`、`bank-backend/app/{models,state,db}.py`、
`tests/bankcore/test_reserve.py`、`tests/integration/test_database.py`、`Makefile`、
`.github/workflows/checks.yml`、`PRD.md`（§8.3 instruction_runs 补三个关系见证列与 SUCCESS CHECK）、
`docs/contracts/v1.{json,sha256}`（仅 BankState 递归新增与 sha256）。

## 红→绿

- 首次红：`standing_instructions` 两分支 CHECK 拆成两条约束导致 SCHEDULED 行违反
  mandate 分支 → 合并为单条 `kind_combo` 或式 CHECK（模型+迁移同步修）。
- 首次红：`_load_obligations` 返回 2 元组却解包 3 值 → 改为返回三元组。
- 首次红：`LocalExecutionTime` 只收 `time`，HTTP JSON 往返送字符串 → 收 `time`/ISO 字符串并
  用 AfterValidator 拒 tzinfo。
- 首次红：drift 测试报 `instruction_runs.event_id` 缺唯一约束 → 迁移补 `unique=True`。
- 首次红：测试共享会话库，clock 重复插入 → 每测试前 `TRUNCATE ... CASCADE`（仅临时 tmpfs 库）。
- 首次红：F1 无授权 SUCCESS 经模型构造先被拒，且事务中止 → 改裸 dict INSERT + savepoint，
  由 DB CHECK 拒绝、随后继续。

## 退出命令（全部实际运行）

| 命令 | 结果 |
|---|---|
| make lint | All checks passed / 182 files already formatted |
| make test | 391 passed, 309 skipped（5.34s；PG 项按约定跳过） |
| make test-obligations | **25 passed, 0 skipped**（6.07s；真实 tmpfs PG + 真实 TCP HTTP） |
| make contracts-check | PASS: contracts v1 match frozen schemas |
| make test-gateway | 173 passed, 254 skipped（4.39s） |

## 真实验收覆盖

- 空库 `upgrade head` 两次成功；运行角色不拥有表、`CREATE/TRUNCATE/DELETE` 拒绝；readiness
  只认 clock 存在 + revision=0003。
- 最新 PG `compare_metadata`（补 `accounts_current_card_fk` 后）零漂移；历史三文件 SHA256 不变。
- 真实 TCP `/internal/snapshot` → `BankState.model_validate_json` → `projected_shortfall`：
  张三转王五 300000 后 `available=300000`、`reserve=4500`、`max_outflow=295500`；
  295500/295501 分别报告缺口 0/1。
- 真实空 scope 返回非空完整空容器、reserve 0；非 scope 客户（李四）义务不入快照。
- F1 矩阵：无授权商户 SUCCESS 被 DB CHECK 拒绝；无授权 BLOCKED 可存；授权改 DELETED 后
  SUCCESS 历史与关联指令仍在。

## 未覆盖 / 待决

- R7/R10/R11 读取、1–30 天展示、`max_outflow` 有符号公开契约、scheduler 与三项定时写
  留后续组件（S3b 起），本组件不宣称覆盖。
- 完整 `baseline_v1` 数据安装（150 笔历史/全部基金）留独立组件；本组件只迁移 schema 与
  真实加载，PRD 精确场景仅 fresh 独立 fixture。
- `source_plan_id`/`event_id` 无本地父表，按现有 L3 逻辑 ID 约定保存，grant 恢复与事件核验
  留后续（提案 §7 已明示）。
- `target_display` 对请求客户统一用固定描述（本人/本行/他行收款人），不携带联系人文字；
  请求客户自身的 payee 名展示属 R7 展示细节，未在本组件实现（保守选择，避免泄漏）。

## 评审修复轮（`docs/reviews/L2-S3a-implementation-review.md`，2 P1 + 7 P2）

- **P1-1 0002 升级保留门禁**：新增 `tests/obligations_data/test_0002_upgrade.py` + 会话级
  `obligations_0002_db` fixture（先 `upgrade 0002_identity`）。真实 ledger/persist 生成
  posted/pending/transactions，并以 P1 ExecutionService 行形（raw SQL，注明来源
  `services/bank-backend/app/execution.py`）落三类 intent/evidence_history/plan_execution/
  operation/outbox、客户/clock 版本；`_capture` 采集全部旧表行+序列+权限基线 → upgrade head
  两次 → 逐字段相等；再真实 `prepare/query/reconcile/replay`（含 COMMITTED 重放 `replayed=True`）。
  未用最新 head 造数冒充旧库，未改生产 loader。
- **P1-2 真实 prepare/execute 链**：`test_real_http_snapshot_feeds_s2_projection` 改为真实 TCP
  `/internal/plans/prepare`+`/execute`（正确 InteractiveEvidence、GREEN），再 snapshot→S2；
  断言双方 version 各增一次、intent COMMITTED、plan_execution/operation/outbox 落盘、
  reserve 4500 / max 295500。原 `post_transfer`+`persist` 样本删除。
- **P2-1**：fixture 给 cus_sb 设哨兵电话、新增 OWN（si_zs_own）/OTHER（si_ww_sb→cus_sb）指令，
  真实 HTTP 断言 covered 不含 cus_sb、哨兵电话/别名/收款人名不泄露、OWN 贷借腿正确。
- **P2-2**：RR READ ONLY 首读后另一事务改 billing+增版本，同 conn loader 仍旧值、只读写被拒、
  新 HTTP snapshot 见新值。
- **P2-3**：恢复 raw 路径混合 offset 窗口/end_at 与「错误上海钟面/锚点拒绝」（scheduled 与
  billing 两路 `_check_calendar`）。
- **P2-4**：`billing.active=False` + ACTIVE mandate 不预留且账单仍在。
- **P2-5**：F1 矩阵参数化 DELETED/DISABLED，均保留 SUCCESS 历史与 last_run 来源。
- **P2-6**：`_validate_mandate_billing` 增 `billing.account_id != from_account_id` 拒绝；先写
  model_copy 红测（确认旧码不拒）再补 guard 转绿。
- **P2-7**：instruction_runs 的 UPDATE 被只追加触发器拒绝（owner 级），bank_ro 的 INSERT/UPDATE
  被权限拒绝。

## 复核二轮（reviewer 条件 APPROVE 的两条 P2 残余）

- **(a) 0002 真实执行代表性**：移除原 `hash_committed/digest_committed/versions={}` 手造执行行。
  新增冻结旧版 `load_bank_state` 测试 helper `tests/obligations_data/legacy_loader.py`（逐字取自
  `cca074b:services/bank-backend/app/state.py`，git blob `e998108e0026dc113b57b334c87a3bea97845e36`，
  只查 P1 旧表）；`_seed_0002` 仅在 0002 造旧状态期间 patch `app.execution.load_bank_state` 指向它，
  `resolve_scope/persist/prepare/execute/query/reconcile` 全部走未修改生产代码。真实
  `prepare(attempt1)→prepare(attempt2)→execute` 产生 COMMITTED（**非空 evidence_history** + 真实
  `result.versions/steps` + 真实 operation/outbox），`prepare`+`reconcile` 产生 PREPARED/CANCELLED；
  posted/pending 仍真实 ledger/persist 且保留活 pending。基线→`upgrade head` 两次→逐字段/行/序列/
  权限不变，再对原真实任务 query/replay/reconcile。
- **(b) N1 权限测试卫生**：`test_runs_append_only_and_bank_ro_read_only` 把 owner UPDATE 拒绝放进
  `begin_nested()`，断言 `run_x` INSERT 仍提交且行内容不变；bank_ro INSERT/UPDATE 各自独立 savepoint；
  均断言真实 SQLSTATE（触发器 `23514`、权限 `42501`），保留触发器拒绝、行不变与 bank_ro 只读证据。
- 红→绿：先加 `assert history`/`assert result_json["versions"]` 红测，复现 `evidence_history=[]`
  手造空历史失败；改真实执行路径后转绿（`history` 非空、`versions == {"cus_zs":1,"cus_ww":1}`）。

## 需协调者串行跑的全栈门禁

`make up`、`make test-p1`、`make test-p1-concurrency`、`make test-p1-consistency`、
`make test-integration` 由协调者独占执行（本 worker 被禁跑），并复核远端 CI。
