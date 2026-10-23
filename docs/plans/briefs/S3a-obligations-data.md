# S3a 义务存储与真实快照（准备稿，尚未派发）

依赖：S2预留已合并；共享组件按S1b、必要运行接线、S3a顺序串行。开始前从最新main建立feat/l3-obligations-data独立worktree。用户受控补缺授权覆盖bankcore/L3完整义务数据及领域增量迁移；协调者不写业务。先读AGENTS、PRD0.1/2.2/5.1/5.4/7.7/8.2–8.5/9.3及S2报告，不把当前空默认数据当已实现。

## 组件边界

输入：现有银行库与完整义务实体；输出：可从真实PG读取的商户、账单、定时/代扣授权、执行历史与唯一load_bank_state中的精确义务快照。此组件先完成存储与数据加载，不实现scheduler、G8、App/BFF或其他领域写工具。读取工具、1–30天展示与定时写工具拆后续组件，不在一个PR堆齐全部缺口。

在修改代码前先报告最小模型清单与迁移/加载设计：现有ScheduledView/SubscriptionView缺哪些原始字段、如何表达账单原始anchor/time/active及授权生命周期、scope内所有权如何完整且不暴露其他客户联系资料、如何区分加载完整空集合与未加载默认空集合。新增内部BankState字段允许受控增量，外部写工具参数保持冻结；contracts-check快照只更新经过核对的内部增量，不全量接受意外漂移。

允许范围：contracts内部state模型与必要freeze快照；bankcore.reserve为消费新原始义务所需兼容适配（固定现有公开函数签名、不复制算法）；bank-backend models/state/必要seed加载与新0003_obligations银行迁移；独立tests、Makefile/CI专项、报告。不得改银行0001/0002历史DDL、网关0002、W1–W4文件、外部写工具schema。若当前约束不足表达精确周期，先报告而不猜测。

## A：增量存储

依据PRD8.3逐字段核对merchants、merchant_billing、standing_instructions、instruction_runs。必须覆盖授权与账单独立生命周期、kind字段组合CHECK、FIXED/DUES金额、period/anchor/weekday/time、所有权外键、来源plan、唯一周期事件与真实last_run来源。所有时间有时区；业务Asia/Shanghai，weekday周一0至周日6。

0003仅新增/ALTER所需对象，不修改历史迁移、不清库、不重建卷。先在独立tmpfs PG验证空库升级两次及旧0002带账本/intent/outbox/客户版本数据升级保留、运行角色无DDL/账本变更权限不放宽、非法组合/跨客户关联拒绝。现有长期业务栈不得TRUNCATE或reset。

种子数据按PRD2.2的确切金额、日期和06:00导入新的空库；现有数据升级如何补新增领域必须显式设计为可重放的增量且不修改已有余额/交易/会话或重复账单，不因数据库已有customers就静默跳过所需领域，也不因缺字段重新播整库。先向协调者提交该增量策略。

## B：唯一真实加载器

load_bank_state用同一snapshot事务加载真实义务，不能另起连接或读取墙钟。授权DELETED不删除账单；预留只计ACTIVE有效授权对应的实际账单amount，非授权max。未知所有权、缺失账单、非法周期、过期但无可恢复锚点必须显式失败，不能漏义务返回零。

完整快照中的义务与账号、收款人/自有目标必须可一致解析。说明投影覆盖哪些客户，非请求客户的资料最小化；不能为让pure函数通过而伪造payee。生命周期尚未实现的推进不能假装已执行；准确记录未支持范围。

真实PG写入领域样本→调用现有internal snapshot HTTP→反序列化BankState→S2同源预留，验证300000分转出后的295500分上限、既有shortfall保留、本人互转、已取消代扣不预留但账单仍在、月末/闰年/多offset、不同客户隔离、真实空集合与缺失加载区别。禁止用FakeL3证明生产快照完成。

## 退出

实现者可运行lint/test/该组件独立PG专项/contracts-check；真实PG专项必须零skip。协调者亲自完整复跑，并串行执行make up、test-p1、test-p1-concurrency、test-p1-consistency、test-integration；这些命令仅协调者可跑，不能与其他compose控制并发。所有PGDATA显式tmpfs，不删除或重建任何卷。

每个小组件先红后绿，完成后本地提交并冻结；报告docs/progress/S3a-obligations-data.md。独立只读评审→原owner修复→协调者复跑→单独PR→当前head全部CI绿→merge commit。只有生产快照实际完整才能解除对应W2/W3接入阻塞；1–30天读取、signed max_outflow公开契约与三项定时写另立任务，不宣称本组件覆盖。

## 已采纳只读架构结论（ADR-009，实施前固定）

- BankState新增obligations: ObligationSnapshot|None，含covered_customer_ids及typed四领域集合、最小instruction_targets。None=未加载；完整空容器=真实无义务。全部scope客户须完整，S2拒绝未知/不全；原public函数签名和7天范围不变，旧View仅派生投影。
- target mapping仅instruction/source owner/OWN-OTHER-EXTERNAL及必要目标ID，绝不扩大旧state.payees到他人完整数据，不递归第三方余额。
- merchant_billing新增weekday；DUES周期字段可空且只以billing为周期事实源。priority保留URGENT/HIGH/MEDIUM/LOW，义务rank仍明确整数3/4，不按字符串排序。
- 允许bank db.py最新revision/readiness最小更新；银行0003仅四新表+必要父表候选唯一/所有权复合FK，历史0001/0002/schema_v1字节不变。
- 本组件默认不创建任意P1义务fixture、不将cus_a/b改名或标为baseline_v1、不重播开户/资金历史。若无真实数据写入只迁移schema/加载真实空表，不改sim_now/clock/data版本。精确PRD种子仅fresh独立验收fixture；完整baseline安装单独任务。上文增量seed提案据此收窄。
- S3b才实现R7/R10/R11与1..30天、受控signed max_outflow契约；S3a不得clamp负上限或放宽全部Cents。真实PG+HTTP证明完整加载，缺数据/所有权/周期明确失败，不用FakeL3当生产证明。
