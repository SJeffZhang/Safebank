# S3a 义务数据设计独立只读审核

日期：2026-10-23。指定基线：`c381a42`；审核期间 root 前进到 `cad4f7f`，新增内容为孤儿执行清理文档，未改变本次审核的 S3a 任务书、ADR-009、PRD §7.7/§8.3 和相关代码。

**规范：PASS。可实施性：APPROVE。**

未发现仍需阻止派发的、可复现或逻辑必然的设计阻塞。本结论仅确认该数据组件的边界与实现路径可以闭合，不代表组件已经实现、生产义务已经齐备或 W2/W3 已获准解除阻塞。

## 核查结果

- **最新结论与旧文。** 任务书第 19 行原来的种子/增量导入提案，已经由第 41 行明确收窄；ADR-009 第 9 行也明确禁止向现有 `cus_a/cus_b` 库凭空安装任意义务、重播资金历史或冒充 baseline。精确 PRD 场景使用独立 fresh fixture，完整 baseline 安装留后续组件。按后置的已采纳结论执行不存在必须改写现有资金库的要求。
- **真实空与未知。** PRD 第 1069 行、任务书第 37 行、ADR-009 第 5 行一致要求可空内部 `ObligationSnapshot`、完整 scope 覆盖以及未知/不全时拒绝预留。现有 `BankState` 的默认 `instructions/subscriptions=()`（`packages/contracts/src/safebank_contracts/state.py:203` 起）不再能单独充当“没有义务”的证明。真实空表可以由同事务 loader 显式证明为空，无须伪造义务或更改数据版本。
- **所有权与隐私。** 当前 loader 只加载请求客户的完整 payees（`services/bank-backend/app/state.py:116`），而 S2 的旧 `_own_target` 依赖该映射及目标账户（`packages/bankcore/src/safebank_bankcore/reserve.py:157`）。任务书第 38 行及 ADR-009 第 7 行给出了足够的最小适配路径：原始指令配 instruction/source owner/OWN-OTHER-EXTERNAL 目标映射，只有 OWN 产生保证贷记；OTHER 仅判定所有权，无须把第三客户余额加入 scope 或加载其他客户联系人。因此 A→B 的快照也能保留 B 自己对 C 的义务，而不递归扩大锁范围或旧 payees。
- **原始周期与 DUES。** 任务书第 39 行、PRD 第 1231–1232 行明确账单的原 anchor/weekday/time、DUES 只认 billing 周期、授权周期字段可空；SCHEDULED_TRANSFER 与 MERCHANT_MANDATE 的字段组合检查仍由任务书第 15 行要求。可直接修复旧 SubscriptionView 缺月度原始锚点的限制（`reserve.py:176`），继续复用 S2 的前缀算式、整数 rank=3/4 及七天公开接口，未要求另写一套算法。
- **真实验收。** 任务书第 27 行要求真实 PG 数据→现有 internal snapshot HTTP→BankState 反序列化→S2，覆盖负上限/既有缺口、取消后账单保留、周期与多 offset、多客户隔离及完整空/缺失区别；第 31–33 行要求零 skip 专项及协调者完整复跑。现有 `/internal/snapshot` 已在同一个 REPEATABLE READ READ ONLY 事务调用唯一 loader（`services/bank-backend/app/main.py:113`），可以沿该真实入口完成证据，无须新增前端、BFF 或以 FakeL3 替代生产加载。
- **迁移、权限及后续范围。** 任务书第 17、40 行允许银行 0003、新表必要父表唯一键/所有权复合 FK 和 db readiness 的最小升级，同时保留历史迁移；不要求修改历史 DDL。第 42 行和 ADR-009 第 11 行明确 signed max_outflow、1–30 天读取、R7/R10/R11、scheduler 与领域写留后续组件，未把它们混入 S3a 完成条件。

## 派发时需明示的既有测试适配

`tests/integration/test_database.py:19` 将当前 Alembic head 写死为 `0002_identity`；增加 0003 后，该断言必须定点升级，否则规定的 `make test-p1` 必然失败。这不是义务模型或迁移设计阻塞。协调者在本次审核消息中已确认沿用此前的最新版本/drift 断言定点许可惯例，并将在派发前写清：允许更新当前 head 断言及最新 drift 测试，仍保留历史 schema 不变、旧 0002 数据升级验证和原权限断言。本审核不建议扩大业务改动范围或放宽门禁。

## 证据边界

已读取整份任务书、ADR-009、指定增量 diff、AGENTS，以及相关 PRD/BankState/views、bankcore reserve/scope、L3 models/loader/HTTP/readiness、历史迁移和相关测试入口。仅执行文件读取、只读检索及 Git 状态/差异查看；未修改仓库文件，未运行 pytest、lint、迁移、HTTP 探针或 Docker，也未派发 agent。唯一新增文件为本报告。
