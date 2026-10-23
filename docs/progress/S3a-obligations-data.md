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
- 真实执行经 `post_transfer`+`persist`（与 seed 同一纯函数/persist 路径）而非完整
  InteractiveEvidence prepare/execute；完整执行链已由 P1 门禁覆盖，本组件聚焦快照正确性。

## 需协调者串行跑的全栈门禁

`make up`、`make test-p1`、`make test-p1-concurrency`、`make test-p1-consistency`、
`make test-integration` 由协调者独占执行（本 worker 被禁跑），并复核远端 CI。
