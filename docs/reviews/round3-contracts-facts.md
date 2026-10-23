# 第三轮独立审核：接口覆盖与事实核对

- 审核人：Claude 子 agent（接口与事实审核员）
- 日期：2026-10-23
- 基线：`PRD-v2.1-wip.md`，读取时 1,591 行。章节号以该文件为准。
- 范围：任务 A 检查接口覆盖，范围是 §5、§9（含 §9.10 页面）、§10、§12，并与 §6–§8 交叉核对；任务 B 核对 §0.4、§4、§6.6 的事实。
- 去重：已读 `docs/reviews/round2-contracts.md`。C01–C08 及其修订复核记录不再重复报告，只报告新问题，或 C05 修复后遗留的不一致。
- 证据来源：
  1. `/private/tmp/claude-501/-Users-zhangshijie--claude-spares-spare-As7eFL/89da7fbc-ae2f-4787-ada3-84d9c81217c3/scratchpad/d8_tools.txt`（首选路径存在，以下简称 d8，行号指该文件）。
  2. `PRD.md`（v1.2）的 §3.7 与 §5.5。
  3. 本次只读补充核实，时间为 2026-10-23：curl 读取 `api-docs.deepseek.com` 的 pricing、thinking_mode、tool_calls、updates 页面；用 arXiv API 取 10 篇相关工作的完整摘要；用 `gh api` 读取 assistant-ui `packages/react-langgraph/src/{useLangGraphMessages,types}.ts` 与 `package.json`；用 PyPI JSON 查询 invariant-ai、langgraph、langgraph-api 等包。
- 方式：静态审核。只写本报告，没有修改 PRD 或 AGENTS；没有运行任何代码测试，仓库中也没有实现可以运行。
- 合计 19 条。任务 A：P0 0 条、P1 6 条、P2 6 条。任务 B：P0 0 条、P1 2 条、P2 5 条。

---

## 任务 A：接口覆盖

总体结论：29 个需求 ID 在 §9.2 中都能对应到 R/W/G 接口。§12 中第 1、2、3、5、6、7 幕的数字和接口链路已逐项验算，结果自洽，例如第 6 幕 floor4(11955/1.2311)=9710.8277。下面列出的是接口锁定后仍然无法逐字段实现，或者无法只用已定义接口完成的部分。

### A-01 · P1 · 409 STATE_CHANGED 的返回体与 L1 处理路径互相矛盾；202 UNKNOWN 在 L1 图中没有分支

**位置**：
- §6.1 图：「409 STATE_CHANGED → [evaluate]（重新生成审批，最多 1 次）」；
- §7.10 第 5 条：「返回 409 STATE_CHANGED 及最新 Decision。若仍需批准，生成新审批」；
- §9.1：「统一错误 `{error:{code,message,details,trace_id}}`，details 为字段错误或版本冲突」；
- §9.5 G8：「→ ExecutionResult 或 202 `{plan_id,status:UNKNOWN}`」。

**问题**：
1. L2 在执行时会就地复演，并在**同一个 plan** 上生成新的 evaluation 和新审批。但错误体的 `details` 没有放 Decision 的位置，L1 拿不到新的 approval_id。
2. L1 图的处理方式是回到 evaluate 重新 POST `/plans/evaluate`。按 §7.2，这一步会生成**新的 plan_id**，于是同一 sid 下会出现两份计划，L2 刚建的审批随即被 SUPERSEDED，用户可能要连续确认两次。
3. G8 返回 202 UNKNOWN 时，execute 节点没有任何出边。

**证据**：§7.2「L2 生成并先保存 plan_id」；§7.10 第 2 条「新审批使同 sid 旧 PENDING/CONFIRMED 未执行审批 SUPERSEDED」。

**修改建议**：
- §9.1 追加：「STATE_CHANGED 的 `details` 固定为 `{decision: Decision}`，其中 plan_id 不变；需要批准时 decision.approval_id 为新审批。」
- §6.1 图改为：「409 STATE_CHANGED → 读取 details.decision：REQUIRE_APPROVAL → [await_approval]（新 approval_id，最多 1 次）；DENY/REPLAN → [respond]。不重新调用 /plans/evaluate。」
- §6.1 图再增加一行：「202 UNKNOWN → [respond]（"确认中"，并附 plan_id 卡片；结果由前端经 F8/G5 轮询，L1 不重试）。」

### A-02 · P1 · app_form 的 PlanDraft 形状没有定义，arg_sources 的枚举也缺少 action/form

**位置**：
- §9.5 G4「PlanDraft（F 另含 form_action_id）」；
- §9.5 G19 返回 `{action_id,normalized_fields,expires_at_wall}`；
- §6.2「`kind ∈ {utterance, read, step, derived, hint}`」「`utterance_ids` 由 L1 代码填写」；
- §7.5 第 1 条提到 `hint` / `action` 来源；
- §7.4 表中 action 一行写的是「action_id / form_action_id」。

**问题**：
- PlanDraft 的必填项（utterance_ids、intents、steps[].arg_sources、rationale）都是围绕 L1 设计的。BFF 提交表单时这些字段该怎么填，文中没有说明。
- G19 返回的是 `action_id`，G4 却要求 `form_action_id`，两者是不是同一个 ID 不明确。
- arg_sources 的枚举里没有 `action`，§7.5 又要求按 action 来源校验。
- 每种 action_kind 允许哪些 `fields`，也没有定义。

这几点合起来，问卷（W12）、卡片页调限额、订阅页取消等 app_form 主路径都无法逐字段生成模型。

**证据**：§9.9 要求「从本章生成 Pydantic 模型/OpenAPI…未知字段拒绝」，这和上面的歧义直接冲突。

**修改建议**：§9.5 增加一个独立的 `AppFormPlanRequest`，并说明 G4 按调用方身份做判别。

> **AppFormPlanRequest**（仅 F）：`{request_id, session_id, form_action_ids: string[1..5]}`。L2 读取各个 form_action 已保存的 `action_kind` 与 `normalized_fields`，按 §9.4 生成 steps，参数来源记为 `{kind:"action", ref:<act_id>}`。BFF 不提交 steps、args、arg_sources、utterance_ids。G19 返回的 `action_id` 就是 form_action_id。每种 action_kind 允许的 `fields` 等于对应 W 工具的 args，W12 的 fields 为 `{questionnaire_id, answers}`。

同时把 §6.2 的枚举改为 `kind ∈ {utterance, read, step, derived, hint, action}`。

### A-03 · P1 · SSE 中的 UICard 被锁定在 §6.5，但 §9 中没有它的模型

**位置**：§6.5「`custom` | `{"type": "cards", "cards": [UICard…]}` | `cards.py` 生成的结构化卡片（§9.10）」；§9.10 硬约束 ③「卡片动作必须 L2 ID」；§6.7 第 3 条「LLM 只能从候选卡片 ID 中选择」。

**问题**：§9.10 只有页面清单，没有 UICard 的字段定义。卡片上的按钮又必须带 L2 ID：A 身份不能调用 G19 创建动作；L2 发放的 ID 只有 replan_hints 的 `hnt_*` 和 G21 candidates 的 `entity_id`。前端拿到卡片后怎么把它变成一个动作，没有闭环。前端不锁定，但 SSE 的事件格式是锁定的，所以这是锁定接口里的空洞。

**修改建议**：§9.5 末尾增加以下定义：

> **UICard**：`{id, type: ACCOUNT|TRANSACTIONS|ANOMALIES|CARD|SUBSCRIPTIONS|PRODUCTS|HOLDINGS|PLAN_STATUS|REPLAN_HINTS|CLARIFICATION|NAVIGATE, source_ref, data, actions: CardAction[]}`。其中 `data` 直接取自 source_ref 对应的 ReadEnvelope.data 子集，或取自 Decision、ExecutionResult。
>
> **CardAction**：`{label, kind: HINT|CLARIFY|NAVIGATE|OPEN_APPROVAL, ref}`。ref 只能是 `hnt_*`、clarification 的 candidate entity_id、白名单页面路由，或 `apv_*`。前端点击 HINT 时调用 F3（kind=hint），点击 CLARIFY 时调用 F7/G19（kind=CLARIFICATION），点击 OPEN_APPROVAL 时调用 F9。L1 不能产生其他 kind。

### A-04 · P1 · scheduler 回调接口没有登记，违反「唯一登记表」规则

**位置**：§7.1「接口以 §9.5–§9.7 为唯一登记表；未登记的路径、工具或方法默认拒绝」；§7.11「S 回调 L2 `/internal/scheduled/authorize {event_id}`」；§7.1 表中 S 一行「bank-net :8201 的到期事件回调」。

**问题**：全文只有 §7.11 出现这个路径，§9 中没有它的请求、返回、错误码和身份定义。按 §7.1 字面实现，这个路由会被默认拒绝；不拒绝的话，它又是一个没有锁定的接口。所有定时转账、代扣扣费、基金确认都要经过它，涉及 T-3、S-4、W-3、W-4 和第 5、6 幕。

**修改建议**：在 §9.5 后增加「§9.5a L2 内部接口（仅 bank-net :8201）」：

> | S1 | POST `/internal/scheduled/authorize` | S（`SCHEDULER_SERVICE_TOKEN`） | `{event_id, attempt}` → `{event_id, status: SUCCESS|BLOCKED|FAILED_INSUFFICIENT|SKIPPED_REAPPROVAL, code?, result?}`；L2 自行从 L3 GET `/internal/events/{id}` 读取定义，请求中不接受金额、目标或 grant 字段；对同一 event_id 幂等；EVENT_NOT_DUE / EVENT_PAYLOAD_MISMATCH 为 409 |
>
> 同时把 §7.1 改为「§9.5–§9.7 与 §9.5a 为唯一登记表」。

### A-05 · P1 · 评测 runner 没有登录客户和确认审批的凭证，§10 的 setup 与断言无法只用已定义接口完成

**位置**：
- §10.3「真实 OTP 由可信 runner 通过控制台获取后走 F 确认」「runner 仅 agent-net，setup/clock/assert 经 D」；
- §10.2「runner 在可信 BFF 模拟器中另保存确认事实」；
- §9.7 D6「确认仍由真实 F 的 G6 完成」；
- §11.1 中 FRONTEND_SERVICE_TOKEN 只给 frontend 和 gateway，DEMO_LOGIN_PINS 只给 gateway；
- §7.3「登录接口要求 F + PIN」。

**问题**：A1、A3、A4、A6 和全部 35 条正常用例，都需要 runner 以客户身份登录、创建会话、确认黄/红审批。runner 没有 F 服务令牌，这是正确的。但它也拿不到任何 PIN，所以连经 BFF `/api/auth/login` 登录都做不到。「可信 BFF 模拟器」暗示 runner 自己扮演 F，又和 §11.1 的凭证分配冲突。按现有接口与凭证表，评测无法跑通。

**修改建议**：
- §11.1 中 DEMO_LOGIN_PINS 一行改为「gateway（校验）、可信评测 runner（仅 EVAL_MODE，单独挂载 `.secrets/eval_pins`）；不注入 agent / frontend」。
- §10.3 第 1 条改为「runner 作为普通浏览器客户端，经 frontend:3000 的 `/api/*`（app cookie + CSRF）完成登录、会话、消息、确认；不持有 FRONTEND_SERVICE_TOKEN」。
- §10.2 中「runner 在可信 BFF 模拟器中另保存确认事实」改为「runner 在本地报告中另记录它经 F10 提交的确认事实（approval_id、view_hash、提交时间）」。

### A-06 · P1 · 计划、审批、交易的状态枚举没有定义，前端恢复所依赖的 G5/G17 返回值无法建模

**位置**：§9.5 G5「→ `{plan_id,status,decision,execution?}`」；G17「status 可 PENDING/CONFIRMED/ALL」；PlanView 的 `status`；§7.2 中 `plans` 表的 status；§11.3「前端断线通过 G5/G17 恢复」；§9.3 TransactionView 的 `status`。

**问题**：§7.4 和 §7.10 用到了 EXECUTING、UNKNOWN、EXECUTED、FAILED、DENIED、REJECTED、EXPIRED、REPLAN、SUPERSEDED、USED 等状态，但 §9 没有给出 PlanStatus、ApprovalStatus、TransactionStatus 的闭合枚举。G17 的筛选值也覆盖不了 REJECTED、EXPIRED 等状态。前端按状态显示「确认中」还是「已完成」（硬约束 ④）的依据因此没有锁定。

**修改建议**：在 §9.5 中加入以下定义：

> **PlanStatus** = `EVALUATED | PENDING_APPROVAL | CONFIRMED | EXECUTING | UNKNOWN | EXECUTED | FAILED | DENIED | REPLAN | REJECTED | EXPIRED | SUPERSEDED | CANCELLED`。
>
> **ApprovalStatus** = `PENDING | CONFIRMED | REJECTED | EXPIRED | SUPERSEDED | USED | LOCKED`。G17 的 `status` 取 ApprovalStatus 或 `ALL`。
>
> **TransactionStatus**（§9.3）= `POSTED | PENDING | SETTLED | RELEASED`，与 §8.3 中各阶段的流水一一对应。

### A-07 · P2 · F1 的客户显示列表没有 L2 数据源

**位置**：§9.6 F1「GET `/api/demo/customers` …客户显示列表」；§7.3「客户列表公开仅含合成显示名」。

**问题**：G1–G21 中没有列出客户的接口，BFF 不知道从哪里取数，也不清楚这个列表是否只在 DEMO 下开放。登录页因此缺数据源。

**修改建议**：在 §9.5 增加「G22 GET `/auth/demo-customers` | F 服务凭证（无 app JWT）| → `{items:[{customer_id, display_name}]}`；仅在 DEMO_MODE/EVAL_MODE 下注册，不返回 PIN、测评或余额」，并把 F1 的映射改为 G22/G1/G14。

### A-08 · P2 · D4 外部事件缺少刷卡渠道字段和流水类型映射，卡片管控和冻结的效果无法演示与断言

**位置**：§9.7 D4 中的 `kind:EXTERNAL_CREDIT/EXTERNAL_DEBIT/SPLIT_PAYMENT … card_id?,merchant_id?`；§8.2「卡渠道只约束对应 CARD_SPEND（线上/境外/ATM/免密）」；§7.7 `apply_external_debit`「仍检查余额与适用卡限制」；§12 第 7 幕「外转被拒」。

**问题**：D4 没有 `channel` 字段，无法检验 C-4 的境外、ATM、免密开关是否真正生效。带 card_id 的 EXTERNAL_DEBIT 应该落成 `transactions.kind=CARD_SPEND` 还是 `EXTERNAL_DEBIT`，没有规定，而这决定了它是否计入消费汇总（§5.2）和异常检测。因此 C-4、C-5 只能断言配置字段，演示不出拦截效果。

**修改建议**：D4 增加 `channel: ONLINE|OVERSEAS|ATM|QUICKPASS|POS|null`。并写明映射规则：有 card_id 的 EXTERNAL_DEBIT 记为 `CARD_SPEND`，检查卡状态、渠道和限额，失败时 result 为 `{status:REJECTED, code}`；没有 card_id 的记为 `EXTERNAL_DEBIT`。

### A-09 · P2 · 评委视图找不到计划，也读不到审计链

**位置**：§2.1 评委角色「查看每份计划的预演结果、决策理由、单步基线对比、审计链」；§9.7 D8 `GET /demo/plans/{id}/comparison`；G11/G16 只对 F+app 开放。

**问题**：控制台没有列出计划的入口，D8 需要事先知道 plan_id；也没有用控制台身份读取审计链或校验哈希链的接口。评委只能切换成客户登录，这和「独立演示控制台与评委比较视图」不一致。

**修改建议**：增加两个接口。
- D11 `GET /demo/plans?customer_id=&cursor=&limit=` → `{items:[{plan_id, session_id, origin, status, risk, created_at_wall}], next_cursor}`。
- D12 `GET /demo/audit?customer_id=&plan_id=&cursor=&limit=` → `{items:AuditView[], next_cursor, chain_valid:bool, first_broken_id?}`。

两者都只读，并记 DEMO 审计。

### A-10 · P2 · 账户缺少可展示的标识，与 PlanView 的展示要求冲突

**位置**：§9.3 AccountView「id、type、currency、balance_cents…、linked_card_id?」；§8.3 中 `accounts` 表没有账号或名称字段，卡引用字段名为 `current_card_id`；§9.5 PlanView「目标姓名、账号尾号/本行标识与金额必须同时出现」。

**问题**：本人互转（T-1、X-2、X-4、第 7 幕）的目标是本人账户，PlanView 和首页都没有「储蓄 尾号 xxxx」一类可展示字段。另外 AccountView 用 `linked_card_id`，§8.2/§8.3 用 `current_card_id`，同一个概念有两个名字。

**修改建议**：
- `accounts` 表增加 `display_name`、`account_no`（种子固定）。
- AccountView 增加 `display_name`、`account_no_masked`，并把 `linked_card_id?` 改名为 `current_card_id?`。
- PlanView 中 TRANSFER_SELF 的目标展示改为 `display_name + account_no_masked`。

### A-11 · P2 · 部分请求体与返回模型只有映射关系，没有字段

**位置**：
- §9.6 F3 只写了「G3，登记后取得助手令牌…」，没有写 body；
- §9.7 只写了「`/demo/auth/login` 使用 D secret 换取 1 小时 console JWT」，没有定义 body 和返回；
- §9.5 G1 返回中的 `customer` 没有定义字段。

**问题**：按 §9.9 的要求「逐字段生成 OpenAPI」，这三处无法生成。F3 还需要说明是否接受 kind=hint/action：A-03 的卡片动作要靠它。

**修改建议**：
- F3 body 写为 `{request_id, kind: typed|hint|action, text?, hint_id?, action_id?}`，透传给 G3；BFF 不接受 kind=context 以外的附加字段。
- 增加「D0 POST `/demo/auth/login` `{secret}` → `{expires_at_wall}`（console cookie 由 BFF 设置）」。
- G1 的 `customer` 定义为 `{customer_id, display_name}`。

### A-12 · P2 · A4 授权证据的存放位置前后不一致（C05 修复后的遗留问题）

**位置**：§10.2「A4 所需授权摘要由 L2 在执行信封中传至 L3 operation_events」；§8.3 中 `plan_executions` 有 `authorization_evidence` 字段，而 `operation_events` 的字段列表中没有它；§8.5 的 execute 请求带 `authorization_evidence`。

**问题**：D7 的断言模板该到哪张表里取证据，文中说法不一致。

**修改建议**：§10.2 的这句改为「…传至 L3，并与执行结果同事务存入 `plan_executions.authorization_evidence`；每条 operation_events 通过 `(customer_id, plan_id)` 关联该证据」。

---

## 任务 B：事实核对

逐项核对后确认一致的内容（不计入问题数）：
- **V-TB**：文档路径、flags.history、timeout、`exists` / `linked_event_failed`（d8 行 2–535）。
- **V-BL**：PreviewTransaction 的注释与「advisory」表述、skip_queue 累积语义、atomic 补偿注释、2026-08-09 合入（d8 行 900–1161）。
- **V-FN**：四个枚举及 `INVALID(0)`（d8 行 1253）。
- **V-LC**：两个包都不回传 `reasoning_content`（d8 行 1905–2213）。
- **V-PG**：license、gated、language 元数据（d8 行 2323）。
- **V-LG**：各包版本与许可证（d8 行 1538）。今天复查时 langgraph-api 已升至 0.16.5，属于快照差异，不算错误。
- **V-AUI**：今天用 `gh api` 复核。`LangGraphKnownEventTypes` 的 8 个事件名、`__interrupt__?.[0]`、`resume: string`、custom 走 `onCustomEvent`、updates 中 `{node:{messages}}` 会被并入聊天，均一致。当前版本为 0.14.37。
- **arXiv 编号与标题**：10 篇均一致。
- **§6.6 DeepSeek 官方事实**：今天 curl 官方文档全部核对一致，包括 model ID、版本、1M/384K、并发 500/2500、价格（闲时为高峰价的一半）、UTC 01–04/06–10 高峰时段、Pro 不支持 Vision、`thinking.type` 与 `reasoning_effort ∈ {low,high,max}` 默认 high、思考模式下 temperature 无效、带 tools 时必须回传 `reasoning_content` 否则返回 400、strict 走 `/beta` 且思考和非思考模式都支持、类型列表、`$ref`/`$def`、不支持 minLength/maxLength/minItems/maxItems。预算按 8K 输入 + 1K 输出 × 5,000 次计算，高峰约 72.6 美元、闲时约 36 美元，与「约 70 / 35」相符。

### B-01 · P1 · `bind_tools(strict=True)` 自动切换到 beta 是有条件的，与本文配置的 base_url 冲突

**位置**：§0.4 V-LC「`bind_tools(strict=True)` 自动切到 `https://api.deepseek.com/beta`」；§4.2 langchain-deepseek 一行的同样表述；§6.6「base_url `https://api.deepseek.com`」。

**问题**：源码中只有当 `api_base == DEFAULT_API_BASE` 时才会切换，而 `DEFAULT_API_BASE` 是 `https://api.deepseek.com/v1`，不是 `https://api.deepseek.com`。如果按 §6.6 把 base_url 配成 `https://api.deepseek.com`，或者用 `DEEPSEEK_API_BASE` 覆盖，就**不会**切换到 beta，strict 模式也不生效。这正是 P0 最容易踩的坑。

**证据**：d8 行 1906 的结果：`34:DEFAULT_API_BASE = "https://api.deepseek.com/v1"`、`35:DEFAULT_BETA_API_BASE = "https://api.deepseek.com/beta"`、`535: if strict is True and self.api_base == DEFAULT_API_BASE:`。

**修改建议**：
- V-LC 结论改为：「…`bind_tools(strict=True)` 仅在 `api_base` 等于包内默认值 `https://api.deepseek.com/v1` 时自动切到 `https://api.deepseek.com/beta`；自定义 api_base 时不切换。」
- §6.6 追加一条：「使用 ChatDeepSeek 时不设置 `DEEPSEEK_API_BASE`（保持默认 `/v1`），或者对 understand/plan 显式设置 `api_base="https://api.deepseek.com/beta"`；P0 用抓包或日志确认 strict 请求确实发到 /beta。」

### B-02 · P1 · §4.5 对 AEGIS 的否定性断言超出了摘要，与本文自己的引用规则冲突

**位置**：§4.5 AEGIS 一行「AEGIS 扫描参数内容与组合策略，不模拟业务状态」；§14「相关工作只读摘要，不能据此断言竞品不支持某功能」。

**问题**：AEGIS 摘要描述的是三段流水线：deep string extraction、content-first risk scanning、composable policy validation，外加 Ed25519 + SHA-256 审计。摘要**没有**说它不模拟状态。「不模拟业务状态」是从摘要没提到推出来的否定结论，答辩时如果被反驳，没有依据可以支撑。

**证据**：今天取得的 arXiv 2603.12621 摘要全文。

**修改建议**：把「我们不同的地方」一栏改为：「据摘要，AEGIS 的检查以参数字符串抽取、内容风险扫描与可组合策略为主（未读正文，不断言其是否模拟状态）；我们对整份计划在确定性的银行账本模型上做累积预演。」其余几行的措辞也照此原则检查：只写「据摘要，X 做 A」，不写「X 不做 B」。

### B-03 · P2 · V-INV 的证据表述过度

**位置**：§0.4 V-INV「`invariant-ai==0.3.5`…能本机运行 `LocalPolicy`；三段链式 `(a) -> (b) -> (c)` 报 ValueError…；`prompt_injection` 检测器首次运行要从 Hugging Face 下载英文模型」；§4.2「不用」表中「三段序列语法报错、检测器要联网下载英文模型」。

**问题**：
1. 实测只完成了安装与 `LocalPolicy.from_string`，`analyze` 一调用就抛异常，从来没有观察到一次成功的规则求值。「能本机运行」说多了。
2. 这个 ValueError 是求值阶段（`evaluate_arrow_operator`）抛出的，不是解析阶段的语法错误。
3. 「首次运行要下载模型」是从源码推断的：`DEFAULT_PI_MODEL = "protectai/deberta-v3-base-prompt-injection-v2"` 加上 transformers `pipeline`。检测器并没有实际运行过。
4. 两两箭头写法的 POC 不是「未测完」，而是被用户中断、根本没有执行。

**证据**：d8 行 2213 是三段写法的命令；行 2258 的 Traceback 末尾为 `evaluation.py, line 448, in evaluate_arrow_operator … ValueError`；行 2142 是 prompt_injections.py 源码；行 2392 显示两两写法的命令被拒绝执行。

**修改建议**：
- V-INV 结论改为：「本机可安装并解析策略；三段链式 `(a) -> (b) -> (c)` 在 `analyze` 求值时报 `ValueError: The '->' operator can only be used with Identifier or TypedIdentifier`；两两箭头写法未执行（会话中断）；按源码，`prompt_injection` 检测器默认通过 transformers 加载 HF 英文模型 protectai/deberta-v3-base-prompt-injection-v2（未实际运行）。」
- §4.2 的理由改为「三段序列写法求值报错、检测器默认依赖 HF 英文模型（源码）」。

### B-04 · P2 · Prompt Guard 2 的「训练目标」与「官方评测语言」无证据

**位置**：§4.2「不用」表中「Prompt Guard 2 86M 以多语言为训练目标，但官方只在 8 种语言上评测，不含中文」。

**问题**：现有证据只有 Hugging Face API 返回的 `cardData.language` 元数据，没有任何关于训练目标或评测范围的原文。

**证据**：d8 行 2323：`meta-llama/Llama-Prompt-Guard-2-86M license= other gated= manual lang= ['en','fr','de','hi','it','pt','es','th']`。

**修改建议**：改为「Prompt Guard 2 86M/22M 的模型卡语言元数据为 en/fr/de/hi/it/pt/es/th，不含中文，中文效果【待核实】；license 为 other 且需人工审批下载（gated=manual）」。

### B-05 · P2 · CaMeL / FIDES / Progent 的移植表述超出了摘要

**位置**：§4.4.4 FD-1「完整性标签随数据传播，组合取最低」；PG-1「工具级权限，默认拒绝」。

**问题**：
- FIDES 摘要只说 Fides 是一个「tracks confidentiality and integrity labels, deterministically enforces security policies」的 planner，没有提到组合时取最低。
- Progent 摘要说的是对 tool names **and arguments** 的符号规则：由 LLM 生成初始策略，经 SMT 判定后只允许单调收窄。摘要没有出现「默认拒绝」。

这些描述虽然已标「☐ 只读摘要」，但写成了来源的思想，容易在答辩中被当成原文引用。

**证据**：今天取得的 arXiv 2505.23643、2504.11703 摘要全文。

**修改建议**：
- FD-1 的「移植的思想」改为「为数据附加机密性/完整性标签并确定性执行策略（摘要）；『组合取最低』为本项目按 IFC 通常做法的设定，待读正文确认」。
- PG-1 改为「以覆盖工具名与参数的符号规则表达最小权限，策略扩张需显式批准（摘要）；本项目简化为工具级登记、未登记即拒绝」。

### B-06 · P2 · BL-7 与 V-BL2 的源码位置和字段归属不准确

**位置**：§4.4.2 BL-7 来源「`model/balance.go`（`BalanceMonitor`、`AlertCondition`，以及余额更新后调用 `CheckCondition`）」；§0.4 V-BL2「`BalanceMonitor{field, operator, value}`」。

**问题**：
- 余额更新后触发监控的代码是包根目录 `balance.go:79` 的 `checkBalanceMonitors(ctx, updatedBalance)`，不在 `model/balance.go`。
- `CheckCondition` 是 `model/model.go:524` 上 BalanceMonitor 的方法。
- `field` 字段属于 `AlertCondition`，BalanceMonitor 本身的字段是 `Condition AlertCondition`。

**证据**：d8 行 1162（`79:func (l *Blnk) checkBalanceMonitors(ctx context.Context, updatedBalance *model.Balance)`，在 blnk 根目录执行的 grep）；行 1491（`model/model.go:524:func (bm *BalanceMonitor) CheckCondition`）；行 799 的输出（`type BalanceMonitor struct {… Condition AlertCondition …}`、`type AlertCondition struct {… Field string …}`）。

**修改建议**：
- BL-7 来源改为「`model/balance.go`（`BalanceMonitor`、`AlertCondition`）、`model/model.go`（`compare` 运算符与 `BalanceMonitor.CheckCondition`）、`balance.go`（余额更新后调用 `checkBalanceMonitors`）」。
- V-BL2 改为「`BalanceMonitor{Condition: AlertCondition{field, operator, value…}}`」。

### B-07 · P2 · Bank of Anthos「缺卡、订阅、理财」只有旧调研结论

**位置**：§4.2「不用」表「Bank of Anthos…前者过重且缺卡、订阅、理财」。

**问题**：v1.2 §3.7 原文写的是：「『没有卡 / 订阅 / 理财』是早先调研的结论，本次未重新读代码」。v2.1 去掉了这个限定，直接写成了事实。d8 中也没有阅读该仓库的记录。

**修改建议**：改为「前者以微服务 + K8s 部署为主，过重；『缺卡、订阅、理财』为早先调研结论，未重新读代码【待核实】，不影响『不用』决定」。

---

## 最重要的 5 条

1. **B-01**：ChatDeepSeek 只在 api_base 为默认的 `https://api.deepseek.com/v1` 时才自动切到 `/beta`。按 §6.6 配置 `https://api.deepseek.com` 时 strict 不生效，P0 必踩。
2. **A-01**：409 STATE_CHANGED 没有携带新 Decision，L1 的处理是重新调用 evaluate，会生成新 plan，形成双审批；202 UNKNOWN 在 L1 图中没有分支。
3. **A-02**：app_form 的 PlanDraft 形状、`action` 来源类型、G19 与 G4 的 ID 名称、各 action_kind 的 fields 都没有定义，问卷、表单写操作这条主路径无法生成契约。
4. **A-05**：评测 runner 拿不到 PIN，也不能持有 F 令牌，无法登录和确认审批，§10 的正常/攻击用例无法只用已定义接口跑通。
5. **A-04**：`/internal/scheduled/authorize` 没有登记在 §9。按 §7.1 的唯一登记表规则会被默认拒绝，所有定时、代扣、基金事件都受影响。
