# 第三轮审核：技术可落地性（账本、并发、bankcore、L1、scheduler、部署评测、工期）

- 审核日期：2026-10-23（周五）。审核对象：`PRD-v2.1-wip.md`（读取时 1,591 行，行号指该版本）。约束：AGENTS.md §2。
- 已读 `docs/reviews/round2-{consistency,contracts,security}.md`，**不重复**其中已列问题（UNKNOWN 栅栏、grant 缺失、T 日前冻结、在途赎回成本、A1 fixture、authorization_evidence、余额曲线、澄清闭环、add_payee no-op 引用、D6 执行路径、周订阅折算等）。经核对，其中 C1/C3/C4/C6/C7 已在当前稿落盘（§7.10 第 7 项、§8.2 申购/赎回、§8.3、§8.5）。
- 方式：静态审核 + 手工推演。没有代码，没有运行任何测试、数据库或 DeepSeek API。第 P1-6 条关于 LangGraph 的判断来自审核者对 `langgraph-checkpoint` 源码的记忆，**标【待核实】**，需 P0 读源码确认。
- 统计：**P0 3 条、P1 9 条、P2 9 条，共 21 条**。

---

## P0（照做会实现不出来或出错）

### P0-1 `arg_sources`（以及 `understanding.slots`）是自由键映射，无法用 DeepSeek strict Schema 表达

**位置**：§6.2 示例 `"arg_sources": {"name": {...}, "account_no": {...}}`；§6.6「每个 object 的所有属性都列入 `required`，`additionalProperties: false`」；§6.3「`submit_action_plan` 的参数 Schema = ActionPlan 去掉 …」；§6.1 understand 输出 `slots`。

**问题**：`arg_sources` 是 `dict[str, Source]`，对应 JSON Schema `additionalProperties: <Source>`。按 §6.6 自己写的 strict 限制，每个 object 都必须 `additionalProperties: false` 并列全 required，所以 map 类型不能表达；`derived.inputs: [source...]` 是递归结构，还依赖未验证的 `$ref` 递归。照写的话，`to_deepseek_strict` 生成不出合法的 strict 工具，plan 节点无法工作。`understanding.slots` 如果是自由 map，也有同样的问题。

**修改建议**（写入 §6.2 与 §6.6）：

> `arg_sources` 改为数组：`[{"param": <该工具敏感参数枚举>, "source": Source}]`。`Source` 是按 `kind` 判别的 anyOf：`utterance{ref,span:[int,int]}`、`read{ref,pointer}`、`step{ref}`、`hint{ref}`、`derived{template: "cancelled_monthly_fees"|"available_less_7d_charges", inputs: LeafSource[]}`。`LeafSource` 只允许 utterance/read/step，**不递归**（§7.5 只开放两个模板，用不到嵌套表达式；`op`、深度 ≤ 3 的说法删除，由模板名决定算式）。`steps[]` 是 13 个变体的 anyOf，每个变体为 `{step_id, tool: <单值 enum>, args: <该工具 args 模型>, depends_on, arg_sources}`。`understanding.slots` 改为 `[{intent_index:int, name:<槽位枚举>, value:string, span:[int,int]}]`。契约中的 Pydantic 内部表示可以仍为 dict，由 `to_deepseek_strict` 和反向转换函数负责数组与 dict 互转，L2 只接收 dict 形式。

另增【P0 实验】E5（见 P2-7）：完整 `submit_action_plan` Schema（13 个变体）能否被 strict 接受。不能接受就改为两阶段：先选工具序列，再逐步填 args。

### P0-2 replay 的键对评测组和运行期易变值敏感，「replay 必跑 B0/B1/B2 全集」照做必然未命中

**位置**：§6.6「请求键 = sha256(model ‖ canonical(messages) …)」「把非种子的易变 ID（`(pln|utt|apv|ses|req|msg)_…`）…替换」「录制文件 `evaluation/replay/<case_id>.jsonl`；replay 未命中时报错」；§10.3「replay 必跑 B0/B1/B2 全集」。

**问题**：
1. plan 节点的 human 消息里有「上次 Decision 摘要」，respond 节点的输入也有 Decision。B0、B1、B2 的 Decision 不同，LLM 请求也就不同；只按 case_id 录一份，B0/B1 的 replay 必然未命中，按规定直接报错。
2. 正则漏掉了 §9.1 中的运行期前缀 `src`（每次读取都会新生成，会进入 gather 的 tool 消息和 plan 的 reads 摘要）、`evl`、`hnt`、`act`、`ntf`、`ord`、`trf`、`evt`。`*_wall` 时间戳（hint 和审批的 `expires_at_wall`）也会进入提示，每次运行都不同。
3. 「ULID 由 Random(seed)+模拟时钟生成」只在 `EVAL_MODE` 下生效，`make demo` 默认 replay 却不在 EVAL_MODE。
4. `canonical(tools)` 参与键计算，契约一改，全部录制作废。

**修改建议**（替换 §6.6「回放」段，并同步 §10.3）：

> - 录制按 `(profile, case_id)` 存放：`evaluation/replay/<profile>/<case_id>.jsonl`。端到端 replay 只跑 B1 与 B2（两组都录）；B0 和三个消融只跑网关层（D6，无 LLM）。§10.3 的「replay 必跑 B0/B1/B2 全集」改为「网关层跑 B0/B1/B2 与消融全集；端到端 replay 跑 B1/B2」。
> - 规范化：ID 正则改为 `\b(cus|acc|crd|pye|mer|bil|si|spl|spi|prd|ord|tx|trf|evt|pln|apv|evl|ses|utt|src|hnt|act|ntf|req)_([0-9A-HJKMNP-TV-Z]{26}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b`，排除种子 ID 白名单。所有 `*_wall` 字段**不进入任何 LLM 提示**：由 L1 序列化给 LLM 的摘要函数统一剔除。确定性 ULID 在 DEMO_MODE 与 EVAL_MODE 下都启用。
> - 冻结规则：contracts、tools Schema 与系统提示在录制前冻结，打 `replay_schema_version` 标签；版本不一致时 replay 直接报 `REPLAY_STALE`，不逐条未命中。录制安排在功能冻结之后的闲时（见 P0-3）。
> - 费用估算：(35 正常 + 24 攻击) × 2 组 × 约 8 次调用 ≈ 950 次，闲时约 10 美元以内；写入 ADR-002。

### P0-3 §13 日程在 10 天内不可实现：单日 L3 核心、半天前端，且没有缓冲

**位置**：§13 表：「P0 准备 | 10-23」「P1 L3 核心 | 10-24 | contracts v1、bankcore 账本/转账、L3 快照/计划事务/版本/幂等；并发和不变量通过」「P8 前端演示 | 10-31 上午 | 完整页面能力…演示三遍」「P10 提交 | 11-01 下午至 11-02」。

**问题**：
- 本稿规格量约为：L2 有 17 张表，L3 有 20 张表；接口共约 80 个（G1–G21、F1–F13、D1–D10、L3 内部 14 个、read/write 32 个）。另有 intent/reconcile 栅栏、outbox、grant、两阶段基金、澄清/hint/form_action 三套授权入口，以及六组评测配置。
- 今天（10-23）是 P0 当天，仓库还不是 git 仓库；P0 包含官网核实、DeepSeek POC、3 份 ADR 和双网脚手架，当天完不成。
- P1 要在一天内通过并发测试和不变量测试。按经验，L3 的并发、幂等与恢复测试本身就需要 2–3 天。
- 前端（约 12 个页面 + 控制台 + 评委视图）在 10-31 只有半天，而且排在评测录制之前：录制要求 L1/L2/前端都已冻结，评测却安排在同一天下午。
- 全程没有缓冲日；团队人数未知（Q2）。

**修改建议**（替换 §13 表；假设 ≥ 3 人并大量使用 AI 编码 agent。如果只有 1–2 人，直接按 P1-9 的削减等级 2 执行）：

| 阶段 | 日期 | 并行流 | 退出条件 |
|---|---|---|---|
| P0 | 10-23 晚 – 10-24 | 全员 | git/compose 双网/密钥；DeepSeek E1–E6（限时 4h，P2-7）；contracts 骨架；ADR-001/002 |
| P1 L3 核心 | 10-24 – 10-26 | 流 A（L3+bankcore） | 账本/转账/卡、`load_bank_state`/`persist`（P1-3）、intent/execute/reconcile、并发 20 笔与双向互转测试 |
| P2 细线 | 10-25 – 10-27 | 流 B（L2）+ 流 C（L1）+ 流 D（BFF/前端） | 用 L3 桩先通 L2→L1；10-27 前真 L3 一笔黄转账闭环 |
| P3 安全核心 | 10-27 – 10-28 | 流 B | 累积预演、来源、意图、工作流；A1/A2/A3 网关层用例通过 |
| P4–P6 场景 | 10-27 – 10-30 | 流 A + C | 卡/定时/时钟/AA → 账单 → 订阅/基金；前端同步铺页面（流 D 从 10-25 起持续） |
| P7 联动与恢复 | 10-30 | 全员 | X-1–4、STATE_CHANGED、UNKNOWN；**10-30 24:00 功能冻结** |
| 录制 + 评测 | 10-31（闲时录制） | B+C | 按 P0-2 录 B1/B2；网关层全矩阵；报告 |
| 缓冲 | 11-01 上午 | 全员 | 只修阻塞 bug，不加功能 |
| P10 | 11-01 下午 – 11-02 | 1 人从 10-29 起并行写文档 | 技术文档、PPT、视频、全新环境部署、提交 |

---

## P1

### P1-1 L3 写事务的隔离级别未定；如果沿用快照所用的 REPEATABLE READ，热点内部账户会大量序列化失败

**位置**：§7.7「L3 在 REPEATABLE READ 快照中读取全体涉及客户」；§8.4「内部结算账户…只用按账户 ID 排序的 UPDATE 原子增加累计量」「发生死锁/序列化失败可同 plan 最多重试 2 次」；§8.7「20 个并发外转」。

**问题**：§8.4 没有写写事务的隔离级别。在 REPEATABLE READ 下，所有他行转账都会更新 `acc_external_clearing` 这一行，第二个并发事务会得到 `40001 could not serialize access due to concurrent update`。在 FOR UPDATE 等锁后，如果期间有提交，也会报 40001。20 个并发外转最多重试 2 次，必然有失败，§8.7 的测试过不了。另外，RR 的快照取在第一条语句，等锁之后读到的领域数据是旧的；目前只靠版本校验兜底，没有写明。

**修改建议**（写入 §8.4 开头）：

> L3 所有写事务（plan execute、event execute/block、prepare、reconcile、clock advance）使用 **READ COMMITTED**。一致性由「intent/event 行锁 → clock FOR SHARE → state_versions FOR UPDATE（按 ID 排序）」加 expected_versions 比对保证。所有领域读取都在拿到客户锁**之后**发出。内部账户累计量只用单语句 `UPDATE accounts SET credits_posted_cents = credits_posted_cents + :d WHERE id = :id`，按 ID 排序，不先 SELECT。只有 `/internal/snapshot` 和只读工具使用 `REPEATABLE READ READ ONLY`。仅对 SQLSTATE 40P01/40001 自动重试，最多 2 次。§8.7 增加「50 个并发他行外转（不同客户）全部成功、外部清算账户累计量等于总和」。

### P1-2 L2 会话级 advisory lock 跨 HTTP 持有，没有连接与超时规则，并发下会耗尽连接池并互相等死

**位置**：§7.8「L2 按所有涉及客户 ID 排序取得数据库会话级 advisory lock，覆盖最终复演、向 L3 提交和结果归档」；§7.12「客户 advisory lock 保证追加顺序」。（round2 只提到审计同连接问题，本条是连接池与超时问题。）

**问题**：同一客户 20 个并发执行时，每个请求都从池里取一个连接，阻塞在 `pg_advisory_lock` 上。SQLAlchemy 默认池为 5 + 10，会被等待者占满；持锁者如果要用另一个连接写 evaluations、audit 或 execution_jobs，就拿不到连接，形成分布式死锁。规格也没有锁等待超时，前端请求会一直挂着。

**修改建议**（写入 §7.8 末尾）：

> 每次执行从池中独占一个 `lock_conn`：先 `SET lock_timeout = '5s'`，再按客户 ID 排序执行 `pg_advisory_lock(hashtextextended('cus:' || id, 0))`。超时返回 409 `EXECUTION_IN_PROGRESS`（可重试）。持锁期间 gateway 库的所有写入（evaluations、execution_jobs、authorization_claims、audit_events、workflow_events）都在 `lock_conn` 上按小事务执行，审计哈希链不再另外取锁。结束时 `pg_advisory_unlock_all()` 后归还连接。进程内用 `asyncio.Semaphore(GATEWAY_MAX_EXECUTIONS=8)` 限制并发执行；连接池大小 ≥ 8 + 10。HTTP 超时：L2→L3 prepare 3s、execute 10s，超时进入 UNKNOWN。

### P1-3 bankcore「同一份代码」缺少两件东西：统一的状态加载函数，和可持久化的效果模型

**位置**：§7.7「`BankState` 为不可变结构…L3 在 REPEATABLE READ 快照中读取全体涉及客户」「`apply_*(state, cmd, now) -> (new_state, effects, violations)`」；§8.4「读取领域数据、组装 BankState → 逐步运行 apply_* → 持久化效果」；§9.4 `Effect 为 {type,entity_id,field,before,after}`；§8.7「预演 effects = 实际 effects」。

**问题**：
1. `/internal/snapshot` 和 execute 各自「组装 BankState」，PRD 没有要求它们用同一个函数，也没有规定数据范围：哪些账户、卡、指令和账单；历史流水取多长；收款人对应的本行账户怎么解析；add_payee 的账号怎么映射到本行账户。只要两边加载范围不同，预演和执行就会漂移。
2. §9.4 的 Effect 是展示用的字段差异，不能用来插入 transfers、transactions、fund_orders、event_jobs 等新行。L3 只能另写一套持久化逻辑，「同一份算术」就会断开。

**修改建议**（新增 §7.7.1「bankcore 接口」）：

> ```text
> scope_for(steps | event, directory) -> StateScope      # L3 内实现；纯函数部分在 bankcore
> load_bank_state(conn, scope, now) -> BankState          # L3 唯一实现；snapshot 与 execute 都调用它
> apply_*(state, cmd, now) -> (BankState, list[LedgerEffect], list[Violation])
> persist(conn, effects)                                   # L3 唯一写入口
> to_public_effects(effects) -> list[Effect]               # §9.4 展示用
> ```
> - `StateScope` 包含：涉及客户（付款方，以及经 payee.target_account_id 解析出的本行收款客户）及其全部账户；当前卡；被引用的收款人、指令、账单、持仓、订单、产品；`directory`，即 add_payee 账号 → 本行 target_account_id 与 fingerprint 的预解析结果。
> - `Counters` 的输入：范围账户在 `[sim_now−24h, sim_now]` 与当日 00:00 起的已过账流水；未来 7 天的有效指令与账单（供 P1-5 使用）。工作流所需的 7 天 operation_events 放在单独字段 `ops_7d`，只有 L2 读取。
> - `LedgerEffect` 是判别联合：`PostTransfer{transfer_id,debit,credit,amount,code,phase,pending_id?,expires_at?}`、`AppendTransaction{…balance_after,available_after}`、`UpsertCard`、`InsertPayee`、`InsertInstruction`、`SetInstructionStatus`、`InsertFundOrder`、`UpdateFundOrder`、`UpdateHolding`、`InsertAssessment`、`InsertSplit`、`ScheduleEventJob{kind,due_at,payload}`、`OperationEvent{…}`。
> - `BankState` 与 `LedgerEffect` 都是 contracts 中的 Pydantic 模型，经 snapshot 用 JSON 往返。
> - 一致性测试比较 LedgerEffect 列表，忽略 `sequence`、`commit_seq`、`*_wall`。

### P1-4 哪些违规由 L3 强制、哪些只是 L2 策略，前后矛盾（以 SCHEDULED_SHORTFALL 为例）

**位置**：§7.7 表中 `SCHEDULED_SHORTFALL | … | REPLAN user`（没有写 L3 拒绝）；§9.8「INSUFFICIENT_FUNDS / CARD_SINGLE_LIMIT / CARD_DAILY_LIMIT / SCHEDULED_SHORTFALL | RU | …；实际执行拒绝」；§10.1 B1「不能…放开 L3 余额/适当性硬约束」；§10.2 A6「所有组可能被 L3 阻断」。

**问题**：如果 SCHEDULED_SHORTFALL 是 bankcore 的硬违规，L3 就会拒绝用户「明知会让扣费失败仍要转」的请求，而且 B0/B1 也被它挡住，A1/第 2 幕的组间差异失真；如果它只是 L2 策略，§9.8 的「实际执行拒绝」就是错的。apply_* 的 violations 混合了两类，L3 不知道该拒绝哪些。

**修改建议**（在 §7.7 表中增加「L3 强制」列，并改正 §9.8）：

> L3 强制（bankcore `HardViolation`，B0/B1/B2 一致）：INSUFFICIENT_FUNDS、CARD_SINGLE_LIMIT、CARD_DAILY_LIMIT、CARD_STATE_CONFLICT、DAILY_OUTFLOW_LIMIT、HOLDING_INSUFFICIENT、MIN_PURCHASE_NOT_MET、SUITABILITY_MISMATCH、ASSESSMENT_MISSING、MANDATE_NOT_ACTIVE、MANDATE_AMOUNT_EXCEEDED、DEPENDENCY_UNMET、PENDING_ALREADY_RESOLVED、PENDING_EXPIRED。
> 仅 L2 策略（bankcore 以纯函数提供计算，例如 `projected_shortfall(state)`，但不作为 violation 返回）：SCHEDULED_SHORTFALL、全部工作流、来源、意图、阈值升级。
> 相应地，§9.8 删除 SCHEDULED_SHORTFALL 上的「实际执行拒绝」。

### P1-5 未来 7 天预留的算法不完整：扣费取哪个金额、扣费在几点、缺口怎么定义都没写

**位置**：§7.7「未来 7 天预留：逐日、逐账户投影已授权支出…新计划前后各算缺口；只拦新增/扩大缺口」；§2.2 只写了「每月 18 日」；§7.5 模板 `available_less_7d_charges`；§12 第 6 幕「保留未来 7 天 45」。

**问题**：
- DUES 类代扣的投影金额可以取账单金额，也可以取授权上限 `max_amount_cents`，结果分别是 45 元和 54 元，第 2、6 幕的数字取决于这个选择。
- 扣费的时刻没有写。李四 22 日的乐听音乐扣费如果在 09:00 之后，就落在 `(10-15 09:00, 10-22 09:00]` 窗口之外，预留会变成 30 元，与第 6 幕的 45 元不符。
- 「缺口」的数学定义没有写，hint 金额和 X-3 模板没法与它保持一致。

**修改建议**（替换 §7.7 该段）：

> - 义务集合 `obligations(acct)`：窗口 `(sim_now, sim_now+7d]` 内 ACTIVE 代扣对应账单的 `merchant_billing.amount_cents`（**不用** max），加上 ACTIVE 定时转账（FIXED）的各期金额。忽略一切未来入账，包括工资和基金到账。
> - 本人互转定时在目标账户记为 +delta。每个账户按 `(due_at, rank, id)` 排序得到 delta 序列，`shortfall(acct) = max(0, max_k −(available + Σ_{i≤k} delta_i))`，总缺口为各账户之和。
> - 计划后的总缺口 > 计划前的总缺口时，产生 SCHEDULED_SHORTFALL。
> - hint 和 `available_less_7d_charges` 都取 `max_outflow(acct) = available − max_k(−Σ_{i≤k} delta_i)⁺`，与上述定义一致。
> - 种子账单的扣费时刻固定为扣费日 08:00（`merchant_billing.time_of_day`，同步加入 §8.3）。

### P1-6 助手令牌放在 `config["configurable"]` 里，会被 LangGraph 写进 checkpoint metadata【待核实】

**位置**：§6.1「助手令牌通过 `config["configurable"]["assistant_token"]` 传入，不进 state，不写入 checkpoint」。

**问题**：据审核者对 `langgraph-checkpoint` 源码的记忆，`get_checkpoint_metadata()` 会把 `configurable` 中不以 `__` 开头的 str/int/float/bool 值复制到每个 checkpoint 的 metadata，并存进 Postgres。如果属实，令牌会落库到 agent 库，违反本条约束和 §7.3「cookie 和服务凭证不进入…checkpoint」。

**修改建议**：

> 令牌改用 LangGraph 1.x 的运行时上下文传入（`graph.astream(..., context=AgentContext(assistant_token=...))`，节点通过 `Runtime[AgentContext]` 读取），或用进程内 `contextvars`。两种方式都不经过 configurable。P0 读 `langgraph-checkpoint==<锁定版本>` 的 `get_checkpoint_metadata` 源码确认，并加回归测试：完成一轮对话后，在 agent 库的 `checkpoints` 与 `checkpoint_writes` 全表中搜索令牌字符串，必须为 0 条。

### P1-7 L1 遇到 STATE_CHANGED 的路由，与 L2 自动生成新审批的规定冲突

**位置**：§6.1「409 STATE_CHANGED → [evaluate]（重新生成审批，最多 1 次）」；§7.10 第 5 项「返回 409 STATE_CHANGED 及最新 Decision。若仍需批准，生成新审批」。

**问题**：L2 已经为原 plan 生成了新审批，L1 又回到 evaluate，用新的 request_id 提交草稿，于是产生**新的 plan_id 和第二个审批**，并把 L2 刚生成的那个审批 SUPERSEDED。结果是计划和审批数量翻倍，审计链也分叉；新计划还要重新消费原话，原话有过期风险。

**修改建议**（改 §6.1 图与 §9.5）：

> G8 返回 409 STATE_CHANGED 时，body 为 `{error:{code:"STATE_CHANGED", details:{decision: Decision}}}`。L1 的 execute 节点按 `details.decision.decision` 路由：REQUIRE_APPROVAL → `await_approval`（用其中新的 approval_id，**不重新 evaluate**）；DENY 或 REPLAN → `respond`；同一轮最多 1 次，超过转 respond。语义等价时 L2 内部完成复演（§7.10 第 5 项），G8 直接返回 ExecutionResult，L1 不会看到 409。resume 值必须等于 state.approval_id，否则结束本轮。

### P1-8 网关层评测（D6）无法在不运行 L1 的情况下取得会话和原话

**位置**：§9.7 D6「仅预置 case 及其原 sid/plan 可用」；§9.6 F3「G3，登记后取得助手令牌，再…调 L1 stream」；§10.3「网关层：D6 提交固定 PlanDraft，隔离 LLM 波动」。（round2 C04 讲的是 execute 路径；本条讲的是前置的会话与原话准备。）

**问题**：PlanDraft 必须带 `session_id` 和有效的 `utterance_ids`。BFF 唯一能登记原话的路径 F3 会顺带启动 L1 run，也就引入了 LLM，与「隔离 LLM 波动」矛盾；固定草稿里也没法预先写入运行期 ULID。

**修改建议**（在 §9.7 增加 D6a）：

> `POST /demo/attack/cases/{case_id}/start` → `{session_id, utterance_ids:{"u1":"utt_…"}, customer_id}`。由 L2 内部调用与 G15、G3 相同的处理器：为 case 预置客户建会话，按 case 文件登记 typed 原话，标记 DEMO。case 文件中的 PlanDraft 使用 `{{sid}}`、`{{utt:u1}}` 占位符，runner 拿到返回值后替换。确认仍由 runner 以该客户 PIN 经 BFF 登录后走 G6。

### P1-9 削减顺序与 DoD 不对应，live 的最低样本前后两处不一致

**位置**：§13 削减表第 4 行「最低 B1/B2 各 6 正常+4 攻击单次」，与 §10.3「live 最低选择六场景各 1 条 + A1/A2/A3/A7 各 1 条，B1/B2 每条 3 次」不一致；第 5 行所列的多模态、AgentDojo runner、ML 分类器、Invariant 本来就不在范围内，算不上削减项；§15 要求「B0/B1/B2 完整 replay 报告」「三遍 replay」「35 正常用例」「TSR ≥ 90%」，削减表中都没有对应的豁免。

**修改建议**（替换 §13 削减表，并在 §15 每条后注明「可被第 n 级豁免」）：

| 级 | 削减（真实功能） | 对应 DoD 的替代 |
|---|---|---|
| 1 | 哈希链可视化、指标图表、动画、AA 美化 | 列表与 metrics.json |
| 2 | 3 个消融组、B0 端到端（只保留网关层） | §15 中「完整 replay」改为「B1/B2 端到端 + B0/B1/B2 网关层」 |
| 3 | WEEKLY 周期、`ANOMALY_CATEGORY_OUTLIER`、OTP 重发与频控（固定 1 次）、G16 客户级审计（保留 G11） | 对应用例从 35 条中移出，并在报告中披露 |
| 4 | W10 补卡（只挂失）、FORM_REQUIRED 结构化表单（统一返回 INTENT_UNCLEAR） | C-6「可选补卡」标为未完成 |
| 5 | live：统一为「六场景各 1 + A1/A2/A3/A7 各 1，B1/B2 各 1 次」；仍不可用就只报 replay | §15 live 条款豁免并显式标注 |

---

## P2

### P2-1 模拟时钟是否随真实时间流动没有写明；冻结时钟下 10 分钟窗口永不过期

**位置**：§2.2「reset 后为 2026-10-15 09:00」；§7.8「窗口以 sim_now 为准」「DUPLICATE_PLAN | 10 分钟内…」「REJECTED_RETRY | 10 分钟内…」。

**建议**：

> sim_now 只在 D3 推进时变化，不随真实时间流动。`occurred_at = sim_now`；同一时刻的事件按 `commit_seq` 定序。DUPLICATE_PLAN 和 REJECTED_RETRY 是防重试规则，改用 `wall_now` 的 10 分钟窗口。其余业务窗口（30 分钟、24h、当日、7 天）仍按 sim。

### P2-2 scheduler：事件来源、同刻优先级和超时没有落成数据

**位置**：§8.6「按 `(due_at, priority, event_id)` 排序…基金确认与到账优先于…HIGH…MEDIUM…LOW」；FN-4 的优先级是字符串枚举，按字典序排序是错的（HIGH < LOW < MEDIUM < URGENT）。

**建议**：

> - 排序键改为 `(due_at, rank, event_id)`，rank 用整数：FUND_CONFIRM=0、FUND_SETTLE=1、FUND_EXPIRY=2、SCHEDULED_TRANSFER=3、MERCHANT_CHARGE=4、REMINDER=5、DEMO_EVENT=6。
> - 所有 job 在产生它的事务内用 `ScheduleEventJob` 落地：周期源在消费本期的同一事务里写入下一期，基金订单在下单事务里写入确认与超时。这样「下个事件」= `SELECT min(due_at) FROM event_jobs WHERE status='PENDING'`。
> - 超时：S→L2 30s，lease 60s；基础设施失败退避 1/2/4s，之后推进作业置 RETRYING。

### P2-3 基金算法还有几处未定

**位置**：§8.2「申购…份额 `floor4(amount_cents / 100 / nav)`…确认日…全部金额过账」。

**建议**：

> 全部使用 `Decimal`，上下文 `prec=28`。`floor4(x) = x.quantize(Decimal('0.0001'), ROUND_DOWN)`，`floor_cents(x) = int(x.to_integral_value(ROUND_DOWN))`。确认事务中：持仓不存在则新建；`units += shares`、`cost_cents += amount_cents`（全额计入成本，舍入残差体现在成本里）。货币基金同事务执行。种子数据中李四的持仓为 `units=10000.0000, cost_cents=1000000`。

### P2-4 冲正（`reverses_id`）没有业务入口和规则

**位置**：TB-6「更正靠追加反向转账」；§8.3 `transfers.reverses_id`。没有任何工具或 D 接口会产生冲正。

**建议**：本期明确「不提供冲正入口，`reverses_id` 只保留列与 `UNIQUE(reverses_id)`」。如果需要演示刷卡退款，只开放「对客户借记的反向」，即给客户入账，借记清算账户；不支持对客户贷记的反向，从而不会触碰 CHECK。

### P2-5 L1 线程并发，以及「中断未恢复时又来新消息」的行为

**位置**：§9.6「不并发覆盖 LangGraph 同 thread checkpoint」；§6.1「resume 进入的轮次除外」；§3.5 只列了 asyncpg。

**建议**：

> L1 以 agent 库上的 `pg_try_advisory_lock(hash(thread_id))` 作为运行锁，拿不到锁返回 409 `RUN_IN_PROGRESS`。【P0 实验】验证在有待处理 interrupt 的线程上用新 input 调用时，图会从 START 重新开始、旧中断被丢弃；不成立时，由 ingest 显式清空。依赖补充 `psycopg[binary,pool]`（`AsyncPostgresSaver` 基于 psycopg3，不是 asyncpg），并在迁移阶段调用 `setup()`。

### P2-6 一轮写操作的 LLM 调用次数与 §14「< 10s」目标不符

**位置**：§6.1：understand(1) + gather(≤6 次工具调用，约 7 次 LLM) + plan(1，思考开) + respond(1) + 改写(≤1)；§14「一次写操作对话…< 10s」。

**问题**：最多约 11 次串行调用，每次 2–5s 时总计 20–50s。

**建议**：

> G3 已经确定性解析出意图与槽位（§7.4），understand 直接复用 G3 的 scope，只在解析失败时调用 LLM；gather 限 3 轮，允许并行工具调用；DENY、审批、执行结果的回复用模板加卡片，只有分析类回复调用 LLM。目标为每个写回合 ≤ 4 次 LLM 调用。§14 改为「报告每回合调用次数与 p50/p95」。

### P2-7 DeepSeek P0 实验缺少通过阈值和时间盒

**位置**：§6.6 的【P0 实验】条目；§16 Q3。

**建议**：

> 时间盒 4 小时，结果写入 ADR-002 并附原始日志：
> - E1：思考关 + strict + 强制 `tool_choice`，3 个提示 × 30 次，Schema 合法率 ≥ 29/30。
> - E2：思考开 + strict + 强制调用，出现 400 或合法率 < 95% 时，plan 节点关闭思考。
> - E3：`anyOf null` 不可用时改用哨兵值。
> - E4：`$def` 不可用时内联。
> - E5：完整 `submit_action_plan` Schema 被拒时改两阶段（P0-1）。
> - E6：记录 p50/p95 延迟与 token 用量，修正 §6.6 预算。

### P2-8 gateway 双端口的实现方式

**位置**：§3.3「gateway 一个进程监听两个端口」；§11.1「绑定各自网络接口地址」。

**建议**：

> 一个 asyncio 进程中运行两个 `uvicorn.Server`，挂载两个独立的 FastAPI 应用（`public_app` 不注册 `/internal/*`；`internal_app` 只有 `/internal/*`，并校验 S 令牌）。绑定地址取环境变量 `GATEWAY_PUBLIC_BIND` / `GATEWAY_INTERNAL_BIND`；compose 用 ipam 固定两个子网与 gateway 的 IP。集成测试从 agent 容器访问 `gateway:8201`，必须连接失败。

### P2-9 L3 无法独立核对 `plan_hash` 是否覆盖它实际执行的 steps

**位置**：§6.2 中 `plan_hash` 覆盖 session、utterance 等字段，这些 L3 拿不到；§8.5「同 id 不同 hash 409」。

**问题**：L3 只能原样保存 L2 给的哈希。如果 L2 有 bug，用同一个 hash 发来不同的 steps，L3 会返回旧结果或执行新步骤，自己无法发现。

**建议**：L3 自己计算 `steps_digest = sha256(canonical(resolved steps))`，与 plan_hash 一起存入 `execution_intents` 和 `plan_executions`。重试时两者都必须一致，否则返回 409 `IDEMPOTENCY_CONFLICT`。

---

## 已核对、可以照写的部分

- 账本方向：开户（seed_equity→客户）、外部入账（external_clearing→客户）、他行转出（客户→external_clearing）、商户扣款（客户→merchant_clearing）、申购（客户→产品结算，两阶段）、赎回到账（产品结算→客户）、AA 付款（external_clearing→客户）。借贷方向一致，与客户账户 CHECK `debits_posted + debits_pending ≤ credits_posted` 相容；POST_PENDING 和 VOID_PENDING 只会减少 pending，不会违反 CHECK。
- 锁序：intent/event 行 → clock FOR SHARE → 客户行（排序）→ 内部账户（排序），clock 推进只取 clock FOR UPDATE，回调时不持有锁，没有发现环路。round2 C1 的 intent/reconcile 栅栏已闭合。
- 演示数字：第 2 幕 295,500 分；第 6 幕 1,195,500 分、净值 1.2311、份额 9710.8277。在 P1-5 把扣费时刻定为 08:00 后，这些数字成立。
