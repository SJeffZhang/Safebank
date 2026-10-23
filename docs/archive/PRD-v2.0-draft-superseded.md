# SafeBank Gateway 产品需求与技术设计文档（PRD）

> 版本 v2.0 · 2026-10-23 · 状态：设计已闭合，待开发（仓库中尚无代码）
> 配套文档：[AGENTS.md](AGENTS.md)（接手指南，任何 agent 先读它）、[01-技术选型综合报告.md](01-技术选型综合报告.md)（调研与核实记录）。上一版 v1.2 存档于 [docs/archive/PRD-v1.2.md](docs/archive/PRD-v1.2.md)。
> 本文档既是需求，也是实现规格。实现时如需偏离，先改本文档并在 `docs/decisions/` 写 ADR。v2.0 经过两轮多角度审核（闭合性、安全、技术可行性、事实核对）。

## 0. 文档约定、已确认事实与核实记录

### 0.1 约定

- 标注【待核实】的内容没有一手来源；标注【P0 实验】的内容已给出决策规则和回退分支，由 P0 实验结果决定选哪个分支，不需要再做设计决定。
- **金额**一律是以「分」为单位的整数（字段名 `*_cents`，Python `int`，Postgres `bigint`），禁止 float。**基金份额与净值**用 `Decimal`，Postgres `numeric(18,4)`；在 JSON 与规范化哈希中一律序列化为固定 4 位小数的字符串（如 `"12.5000"`）。
- **时间分两种**：
  - 业务时间：取 L3 的模拟时钟（`sim_now`）。所有业务窗口（24h 累计、30 分钟新收款人、2 小时序列、测评 12 个月、T+n）都按它计算。
  - 安全时间：审批有效期、OTP、JWT、委托令牌、原话授权窗口都取真实时钟（`wall_now`），避免演示时快进模拟时钟导致审批失效或被绕过。
- 「24h」一律指滚动 24 小时窗口；「当日」只用于卡的日累计限额（按模拟日 00:00 切分）。
- 时区统一为 Asia/Shanghai。交易日为周一至周五，本期不处理法定节假日；`bankcore.calendar.add_trading_days(t, n)` 是唯一的交易日计算函数。
- 风险等级「绿 / 黄 / 红」是本项目自定义的权限分级，不是赛题原文。
- 规则与错误代码是三层共享的稳定标识，唯一来源是 `packages/contracts/codes.py`，总表见 §7.15。

### 0.2 已确认事实

| 事项 | 状态与来源 |
|---|---|
| 作品提交截止 2026-11-02 | 已确认（团队，2026-10-22） |
| 主办方不提供 LLM、沙箱或数据 | 已确认（团队）。LLM 自备，沙箱就是本项目的 L3，数据全部为合成数据 |
| 六个业务场景全部实现 | 团队决定 |
| 三明治三层架构、L1 用 LangGraph | 团队决定 |
| LLM：DeepSeek V4 Pro（主），V4.1 Flash（仅多模态） | 团队决定；API 细节按官方文档核实（§6.7） |
| 前端是银行 App 形态，不是聊天框 | 团队决定（2026-10-22），见 §9 |
| 评分维度、权重、提交物格式 | 【待核实】 |
| 参考项目的 license、star、维护状态、关键源码行为 | 已核实（2026-10-22，`gh api`、`git clone` 读源码、PyPI / Hugging Face / arXiv API，见 §0.4、§4）。**通用依赖**（FastAPI、pytest、Next.js 等）的 license 于 2026-10-23 经 PyPI 与 npm registry 查询，版本在 P0 锁定 |

### 0.3 本版相对 v1.2 的主要变化

1. L3 账本改为移植 TigerBeetle 的账户模型（posted / pending 四个累计量）和转账语义（不可变、两阶段、链式原子、幂等 id），见 §8。
2. 计划执行由「逐步调用、失败不回滚」改为「整份计划在 L3 一个数据库事务内全部成功或全部回滚」，移植自 TigerBeetle 的 linked events，见 §7.11。
3. 预演器以 Blnk 的 dry-run 为先例：预演和执行跑同一份规则代码，作用于不可变状态，见 §7.8。
4. L2 独立记录用户原话与助手消息，L1 只拿到短时委托令牌，不再持有用户会话 JWT，见 §7.3、§7.4。
5. 污点判定不再依赖「L1 读过什么」，改为查询该客户的全部不可信语料，并识别「复述」（echo），见 §7.6。
6. 累计类风险规则的数据来源改为 L3 账本快照；L2 事件表只用于 L3 中不存在的事件（序列、拒绝、重复），见 §7.9。
7. 前端从「聊天界面」改为「银行 App」：首页、账单、卡片、订阅、财富等原生页面是主体，助手「小安」是 App 内的一个入口，回复以原生卡片呈现；所有写操作统一走以 `approval_id` 为键的确认面板，见 §9。
8. 定下原本待定的选择：不用 Invariant、不用 ML 注入分类器、不用 assistant-ui 与 LangGraph Server 协议（改为自定义的简单 SSE 协议）、Ed25519 JWT，见 §4.3。
9. 补全端到端闭合细节：步骤间引用与推导金额、规则代码总表、L1 路由表、评测用户模型与计划级回放、按日历排的开发计划。
10. 相关工作检索发现了与「执行前检查」「影子状态」「有状态策略」高度相关的先行工作（AEGIS、Cordon、Sapien 等），创新表述据此收窄，见 §4.5。

### 0.4 核实记录（v2.0 新增部分）

「d8」指 2026-10-22 会话 `d8aba59b` 中实际执行的命令与输出。

| 编号 | 事项 | 结论 | 来源 |
|---|---|---|---|
| V-TB | TigerBeetle 账户、转账、两阶段、链式事件、幂等、更正、限流、余额边界、条件转账、历史余额 | 语义与 §4.4.1 一致 | `tigerbeetle/tigerbeetle` 的 `docs/reference/{account,transfer,account-balance}.md`、`docs/coding/{two-phase-transfers,linked-events,reliable-transaction-submission}.md`、`docs/coding/recipes/{correcting-transfers,rate-limiting,balance-bounds,balance-conditional-transfers}.md`，稀疏克隆后阅读。`transfer.md` 只读了标题与部分字段，`code` 字段的正文取自 `account.md` |
| V-BL | Blnk 预演（dry run） | `PreviewTransaction` 对余额深拷贝运行与真实过账相同的算术，不写任何状态；结果是「建议性」的；批量预演在 `skip_queue=true` 时累积计算；拒绝码与真实过账相同；atomic 批量失败时是**补偿**（void / refund）而不是回滚，补偿本身也可能失败 | `blnkfinance/blnk`（Apache-2.0）的 `transaction_dryrun.go`、`transaction_dryrun_bulk.go:27-115`、`model/transaction_preview.go`。该文件最早可见提交为 2026-08-09 |
| V-BL2 | Blnk 余额模型与监控 | `Balance` 含 `balance / credit_balance / debit_balance` 及对应 inflight 量；`canProcessTransaction`（`model/model.go:156`）以 `balance − inflight_debit` 为可用余额，开启 queued 检查时再减 `queued_debit`；监控条件为 `BalanceMonitor.Condition{Field, Operator, Value / PreciseValue}`，`CheckCondition` 支持 `> < != ==`（**仅 grep 到分支，函数体未读**）；交易哈希链由 `ChainProcessor` 后台封链 | `model/balance.go`、`model/model.go`、`chain_worker.go`、`model/chain.go` |
| V-FN | Fineract standing instruction | `StandingInstructionType{FIXED, DUES}`、`StandingInstructionStatus{ACTIVE, DISABLED, DELETED}`、`AccountTransferRecurrenceType{PERIODIC, AS_PER_DUES}`、`StandingInstructionPriority{URGENT, HIGH, MEDIUM, LOW}`。**只读了枚举，未读调度器如何使用优先级** | `apache/fineract` 的 `portfolio/account/domain/*.java`（`gh api`） |
| V-LC | LangChain 是否回传 `reasoning_content` | **不回传**。`langchain-deepseek==1.1.1` 只把响应里的 `reasoning_content` 存进 `additional_kwargs`（`chat_models.py:406-417`），`_get_request_payload`（:350-383）不放回请求；`langchain-openai==1.7.2` 的 `_convert_message_to_dict` 也不处理。`bind_tools(strict=True)` **仅当 `api_base` 等于默认值 `https://api.deepseek.com/v1` 时**自动切到 `https://api.deepseek.com/beta`（:534-541） | 下载 wheel 后阅读源码 |
| V-INV | Invariant POC | `invariant-ai==0.3.5`（PyPI 最近发布 2025-07-28；GitHub 最近提交 2026-01-12，为文档迁移）能安装、能解析策略；三段链式 `(a) -> (b) -> (c)` 在求值时报 `ValueError: The '->' operator can only be used with Identifier or TypedIdentifier`；**目前没有任何规则成功求值**；两两箭头写法未运行（该命令被用户拒绝）。据源码推断，`prompt_injection` 检测器会通过 `transformers.pipeline` 加载 `protectai/deberta-v3-base-prompt-injection-v2`（未实测联网行为）；`remote_policy.py` 默认连 `explorer.invariantlabs.ai` | 本机 venv 实测 + 源码 |
| V-PG | 注入分类器的语言支持 | `protectai/deberta-v3-base-prompt-injection-v2`：Apache-2.0，语言仅 `en`；`meta-llama/Llama-Prompt-Guard-2-86M/22M`：license `other`、gated（需人工审批），语言列表不含中文 | Hugging Face API `cardData` |
| V-LG | LangGraph 生态版本与许可 | `langgraph 1.2.16`、`langgraph-checkpoint-postgres 3.2.0`、`langchain-deepseek 1.1.1`、`langchain-openai 1.7.2`、`langgraph-cli 0.4.37` 均为 MIT；**`langgraph-api 0.16.4` 为 Elastic-2.0**，以内存为主的开发服务器。`interrupt()` / `Command(resume=)` 的签名读自 GitHub main 分支的 `langgraph/types.py`，P0 在 1.2.16 上复核 | PyPI JSON API，2026-10-22 |
| V-AUI | assistant-ui 的 LangGraph 集成 | `@assistant-ui/react-langgraph 0.14.36`（MIT），peer 依赖 `@langchain/langgraph-sdk ^1.9.20`；提供 `useLangGraphInterruptState`、`useLangGraphSendCommand`（`resume: string`）；事件处理依赖 `messages` tuple、`updates.__interrupt__`、`values`（流结束时以最后一个 `values` 为准对齐）。**本版不采用**，理由见 §4.3 | `packages/react-langgraph/src/*.ts`（2026-10-22、10-23 两次读取） |
| V-CN | 中文数字解析 | `cn2an 0.5.24`，MIT | PyPI |
| V-REL | 相关工作 | 见 §4.5。**只读了摘要** | arXiv API |

---

## 1. 背景与目标

### 1.1 一句话定义

SafeBank Gateway 是一个覆盖六大银行业务场景的 AI 银行 App。App 内置的智能助手「小安」可以代客户完成多步骤业务，但它的所有写操作都必须经过一个独立的安全网关：网关在执行前对整份操作计划做**金融状态预演**，再结合**跨步骤、跨会话的工作流风险监控**与**参数级污点判断**，最后由确定性规则决定放行、要求确认、要求重新规划或拒绝。

### 1.2 核心研究问题

金融状态预演与工作流感知的安全机制，能否在保持正常业务完成率的前提下，显著降低银行智能体的未授权及高风险操作？

### 1.3 目标与衡量方式

| 编号 | 目标 | 衡量方式 |
|---|---|---|
| G1 | 六个业务场景端到端可用 | 每个场景至少 5 条正常用例通过 |
| G2 | 安全网关强制生效，L1 无法绕过 | 网络、凭证、委托令牌隔离，加绕过测试（§10.2 A8） |
| G3 | 预演能发现单步检查发现不了的风险 | 对照实验：单步校验基线 B1 vs 完整方案 B2 |
| G4 | 能检测跨步骤、跨会话风险 | 拆分转账、危险操作序列、意图越权等用例 |
| G5 | 安全层不明显降低正常任务完成率 | 正常用例完成率下降不超过 5 个百分点 |
| G6 | 演示稳定可复现 | 模拟时钟、固定种子数据、确定性 ID、LLM 回放 |
| G7 | 像一个真实的银行 App | 评委不看说明也能完成「查余额 → 让小安转账 → 确认」；确认面板与真实手机银行的支付确认形态一致 |

### 1.4 非目标

- 不做真实资金清算、真实支付通道、真实短信。
- 不做多 Agent、RAG 知识库、模型微调。
- 不提供投资建议。理财比较只呈现客观产品信息。
- 不追求生产级高可用，追求演示稳定与实验可复现。
- 不做原生 iOS / Android 应用。App 是移动端优先的 Web 应用（§9.1）。
- 不做真实的身份认证体系。演示登录用种子数据中的 PIN（§7.3）。

---

## 2. 用户与演示数据

### 2.1 角色

| 角色 | 说明 | 入口 |
|---|---|---|
| 银行客户 | 在 App 中浏览账户、账单、卡、订阅、理财；通过小安完成多步骤业务；在确认面板上授权写操作 | App（§9） |
| 演示操作员 | 推进模拟时钟、注入外部交易、加载攻击场景、查看模拟短信 OTP | 演示控制台（仅 `DEMO_MODE=true`，§9.7） |
| 评委 / 审计员 | 查看每次计划的预演结果、决策理由、审计链 | 评委视图（桌面右栏）与 App「安全中心」 |
| 评测脚本 | 批量运行正常与攻击用例，统计指标 | `evaluation/` runner（§10） |

### 2.2 种子数据（固定，可一键重置）

种子脚本：`services/bank-backend/seed/seed.py`（L3）与 `services/gateway/seed.py`（L2：定期授权与演示 PIN 的哈希）。固定随机种子，所有实体 ID 确定性生成。`make reset` 恢复初始状态，模拟时钟拨回 **2026-10-15（周四）09:00**。全部为合成数据，不含任何真实个人信息；技术文档和演示中标注「模拟数据」，并说明生成规则（类别分布、金额分布、预埋异常的方式）。

**客户**

| 客户 | 账户 | 卡 | 收款人 | 测评 | 其他 |
|---|---|---|---|---|---|
| 张三（主演示） | 活期 8,000.00；储蓄 20,000.00 | 借记卡 1 张：单笔限额 5,000、日累计 20,000、境外关、线上开、ATM 开、免密开 | 王五（本行）、赵六（他行）、妈妈（他行）；均创建于 60 天前，`provenance=user_form`，且有历史转账 | **无** | 4 个代扣订阅：视频会员 25.00 / 月、音乐会员 15.00 / 月、云存储 21.00 / 月、健身房 199.00 / 月；下次扣费分别在 10-18、10-20、10-21、10-22 |
| 李四 | 活期 50,000.00 | 借记卡 1 张（同上默认值） | 王五 | **R3**，2026-03-01 完成 | 持有 R2 短债 10,000.0000 份；代扣：视频会员 25.00 / 月（10-20） |
| 王五 | 活期 3,000.00 | – | – | – | 本行收款方 |
| 赵六、妈妈、AA 朋友 3 人 | 他行，只以收款人或 AA 参与人的形式出现 | | | | |
| 钱七（攻击者） | 他行账号 `6222020000001234567`，只出现在注入数据中，不在任何人的收款人列表里 | | | | |

演示 PIN：张三 `1111`、李四 `2222`，L2 只存 PIN 的哈希；agent 容器拿不到。

**流水**：张三近 90 天约 150 条，覆盖餐饮、交通、购物、娱乐、转账（含对王五、赵六的历史转账），并预埋：
- 4 个订阅的历史扣费（各 3 次）；健身房近 60 天没有「到店」类消费（`mcc=7997` 的非扣费流水）；
- 3 条异常交易：凌晨 02:13 网购 1,280.00；同一商户 5 分钟内两笔 88.00；新商户首单 2,600.00；
- 攻击 A2 用的外部入账不在种子里，由演示控制台或评测 setup 注入（§10.4）。

历史流水由种子脚本按时间顺序调用同一个记账函数（`bankcore.ledger`）生成，保证历史余额字段正确。

**理财产品 6 支**：R1 货币基金（`confirm_lag_days=0`、`settle_lag_days=0`）、R2 短债（1 / 1）、R3 稳健混合（1 / 2）、R4 偏股混合（1 / 2）、R5 行业股票（1 / 2）、R3 定开债（1 / 2，起购 10,000.00）；其他产品起购 1.00。近 60 天净值历史由种子按固定随机游走生成。

**商户与账单**：`merchants` 表含 4 个订阅商户；`merchant_bills` 为每个代扣在每个扣费日生成一条账单，金额等于订阅月费。

---

## 3. 总体架构（三明治架构，已确定）

### 3.1 分层职责

| 层 | 职责 | 明确不做 |
|---|---|---|
| 前端 App + BFF | 银行 App 页面；小安面板；确认面板；BFF 持有会话 cookie、校验 CSRF 与 Origin、代理请求 | 不直接访问 L3；确认面板的内容只取自 L2，不取自 LLM 输出 |
| L1 Banking Agent | 意图理解、缺参追问、只读信息收集、生成结构化写计划、解释结果 | 不持有用户会话 JWT 与任何银行写凭证；不判断是否授权；不能签发身份、不能确认审批 |
| L2 Security Gateway | 所有读写的唯一入口；签发与校验身份和委托令牌；独立记录用户原话与助手消息；读数据打可信度标签；计划预演；工作流风险评估；策略决策；审批绑定；执行前复核；定期指令授权；审计 | 不实现业务记账规则（复用 `bankcore`）；不调用 LLM 做授权；不读取计划中的 `intents`、`rationale` 做任何判断 |
| L3 Banking Backend | 账本、账户、转账、卡、定期指令与代扣、理财、模拟时钟与 scheduler；数据库事务、幂等、并发控制 | 不面向 agent-net 开放 |

### 3.2 强制执行边界（L1 不能绕过 L2）

1. **网络隔离**：两个 Docker 网络。`agent-net` = {frontend, agent, gateway, postgres-agent}；`bank-net` = {gateway, bank-backend, postgres-bank}。agent 与 frontend 容器无法解析或访问 bank-backend。
2. **凭证隔离**：L3 只有 `/internal/*` 接口，只接受 L2 持有的 `GATEWAY_SERVICE_TOKEN`。
3. **身份不可伪造，L1 拿不到用户会话**：
   - 用户登录后，L2 用 Ed25519 私钥签发会话 JWT（`aud=bff`），只存在浏览器的 httpOnly cookie 与 BFF 中。
   - 每次用户给小安发消息，L2 在登记原话时另签一张**委托令牌**（`aud=gateway`、`scope=assistant`、10 分钟、绑定 `sid` 与 `utterance_id`），BFF 把它转交 L1。L1 只能凭它调用读工具、计划评估与执行、审批状态查询，**不能**调用审批确认、登录、演示、通知等接口。
   - BFF 的所有路由都要求会话 cookie；L1 没有 cookie，即使在同一网络中调用 BFF 也会被拒绝。
4. **确认不经过 LLM**：审批的确认 / 拒绝只接受 BFF 凭证 + 会话 JWT（`aud=bff`），BFF 侧再校验 CSRF 令牌与 `Origin`。确认面板以 `approval_id` 为唯一键从 L2 取内容，确认请求必须带上面板显示的 `plan_hash`。
5. **原话与 origin 不由 L1 声明**：用户原话由 BFF 写入 L2；计划的 `origin` 由 L2 按调用方凭证推导（委托令牌 ⇒ `agent`，BFF ⇒ `app_form`）。
6. **绕过测试**：`evaluation/bypass/` 覆盖 §10.2 A8 的全部路径，必须全部失败。

### 3.3 部署拓扑

```text
            浏览器（手机或桌面）
              │  httpOnly cookie（会话 JWT, aud=bff）+ CSRF 令牌
              ▼
 ┌──────────────────────── agent-net ───────────────────────────────┐
 │  frontend (Next.js App + BFF route handlers)   :3000（唯一对外端口） │
 │     │ 小安 SSE（转交委托令牌）│ App 读取 / 原话登记 / 审批 / 演示（F + 会话 JWT）│
 │     ▼                       │                                     │
 │  agent (LangGraph + FastAPI，自定义 SSE 协议)  :8100               │
 │     │ 读工具 / 计划评估 / 执行 / 审批状态（A + 委托令牌）             │
 │     ▼                       ▼                                     │
 │  gateway (FastAPI)            :8200  ◀── 同时位于 bank-net          │
 │  postgres-agent :5432（库 agent：LangGraph checkpoint）              │
 └─────┼─────────────────────────────────────────────────────────────┘
       │ GATEWAY_SERVICE_TOKEN
 ┌─────▼──────────────── bank-net ──────────────────────────────────┐
 │  bank-backend (FastAPI + scheduler)  :8300                       │
 │  postgres-bank :5432（库 bank 归 L3；库 gateway 归 L2）             │
 └──────────────────────────────────────────────────────────────────┘
```

- 两个 Postgres 实例分属两个网络，都不映射宿主机端口。agent 只拿到 `agent` 库的凭证。
- postgres-bank 由 `infra/postgres-bank/init.sql` 初始化：库 `bank`、`gateway`；角色 `bank_app`（只能访问 bank）、`gateway_app`（只能访问 gateway，对 `audit_events` 只有 INSERT / SELECT）、`eval_ro`（两库只读，仅评测 runner 使用）、库 owner 角色（只用于 Alembic 迁移与 `make reset`）。
- Redis 不作为必需组件。

### 3.4 一次写操作的完整链路

```text
张三在 App 里打开小安："帮我给王五转 3000，再给赵六转 6000"
 0. BFF → L2 POST /sessions/{sid}/utterances：登记原文，返回 utterance_id 与委托令牌
 1. BFF → L1 POST /v1/sessions/{sid}/turns（SSE）：带 utterance_id、原文、委托令牌
 2. L1 understand：场景 transfer，两笔
 3. L1 gather：经 L2 调 get_accounts / list_payees（L2 记入读取账本，打 trusted 标签）
 4. L1 plan：生成 ActionPlan（两步 transfer），引用 utterance_id
 5. L1 → L2 POST /plans/evaluate：L2 分配 plan_id，运行流水线
      ① 契约 ② 快照 ③ 实体与所有权 ④ 污点 ⑤ 意图范围 ⑥ 预演 ⑦ 工作流 ⑧ 决策 ⑨ 审计
 6. 预演：第一笔后可用余额 5,000，第二笔 6,000 → INSUFFICIENT_FUNDS → REPLAN（ask_user）
      replan_hints：第二笔最多可转 5,000.00
 7. L1 respond：解释原因并给出选项（BlockedCard），不自行改金额
 8. 张三："那只转王五 3000"（新原话，回到第 0 步）
 9. 新计划 → REQUIRE_APPROVAL（红：单笔 > 1,000，需 OTP）。L1 interrupt，SSE 推送 {approval_id}
10. App 弹出确认面板：BFF 以 approval_id 调 L2 GET /approvals/{id}，展示步骤、转前 / 转后、风险项
11. 张三输入模拟短信中的 OTP，点确认 → BFF → L2 POST /approvals/{id}/confirm {plan_hash, otp}
12. App → BFF → L1 POST /v1/sessions/{sid}/resume {approval_id}
13. L1 → L2 GET /approvals/{id}/status = CONFIRMED → POST /plans/{plan_id}/execute
      L2：客户级串行锁 → 复核审批 → 新快照上重跑 ⑥⑦⑧ → L3 POST /internal/plans/execute
      L3：一个数据库事务；幂等键 (customer_id, plan_id)
14. L3 返回结果 → L2 写事件与审计 → L1 用 ResultCard 汇报；App 首页余额、流水随之刷新
```

### 3.5 技术栈

| 模块 | 选型 | 说明 |
|---|---|---|
| 语言 | Python 3.12（后端）、TypeScript（前端） | 本机为 3.13，容器内固定 3.12 |
| L1 | `langgraph==1.2.16` + `langgraph-checkpoint-postgres==3.2.0`（psycopg3）+ `langchain-deepseek==1.1.1` | 对前端暴露自定义 SSE 协议（§6.6），不引入 `langgraph-api` |
| LLM | DeepSeek V4 Pro `deepseek-v4-pro`（主），V4.1 Flash `deepseek-flash`（仅多模态） | strict tool calling 承载结构化输出（§6.7） |
| API | FastAPI + Pydantic v2 | 三个后端服务统一 |
| 数据库 | PostgreSQL 16 + SQLAlchemy 2.x async（asyncpg）+ Alembic；L1 checkpoint 用 psycopg3 | |
| 领域规则 | `packages/bankcore`（纯函数，无 IO） | L2 预演与 L3 执行共用 |
| 策略引擎 | 自研：Python `Rule` + YAML 阈值 + 序列匹配器 | 不用 Invariant、OPA、Cedar（§4.3） |
| 注入防御 | 结构性隔离（委托令牌、原话独立记录、语料级污点与复述识别）为主；中文规则启发式为辅，只升级风险 | 不用 ML 分类器（§4.3） |
| 中文金额解析 | `cn2an==0.5.24` | L2 意图提取与污点规范化 |
| 前端 | Next.js（App Router）+ Tailwind + Recharts；小安面板自写（约 300 行） | 银行 App 形态（§9） |
| 测试 | pytest、pytest-asyncio、hypothesis、httpx；Playwright（前端冒烟）；自研评测 runner | |
| 部署 | Docker Compose，`make keys && make up` | |

### 3.6 仓库结构

```text
Fintech2026/
├── AGENTS.md
├── PRD.md                     # 本文档
├── 01-技术选型综合报告.md
├── docs/
│   ├── decisions/             # ADR-00N-<slug>.md
│   ├── archive/               # 旧版 PRD
│   ├── third-party.md         # 实际引入的依赖：版本、license、用途、核实来源
│   └── api/                   # 导出的 OpenAPI 文档
├── infra/postgres-bank/init.sql
├── packages/
│   ├── contracts/             # 共享 Pydantic 模型：plan、decision、tools/*、codes、ui_cards、canonical
│   └── bankcore/              # 纯函数：ledger、cards、standing、funds、analytics、calendar、classify、derive、ids
├── services/
│   ├── bank-backend/          # L3：app/{internal,models,services,scheduler}/、migrations/、seed/、tests/
│   ├── gateway/               # L2：app/{api,auth,utterance,readproxy,taint,intent,preflight,workflow,policy,approval,scheduled,audit,demo}/
│   │   ├── policies/          # tools.yaml、thresholds.yaml、workflow.yaml、trust.yaml、intent_lexicon.yaml、injection_patterns.yaml
│   │   ├── seed.py
│   │   └── tests/
│   └── agent/                 # L1：app/{graph,nodes,llm,server,prompts}/、tests/
├── frontend/
│   ├── app/                   # (app) 客户页面、(desk) 演示布局与评委视图、api/ BFF
│   ├── components/
│   ├── lib/                   # SSE 客户端、BFF fetch、金额格式化
│   ├── styles/tokens.css
│   └── e2e/                   # Playwright
├── tests/integration/         # 跨层：预演 = 执行、两种 origin 同一流水线
├── evaluation/
│   ├── scenarios/{normal,attack}/
│   ├── bypass/
│   ├── replay/                # LLM 录制
│   ├── plans/                 # 计划级回放（消融用）
│   ├── runner.py
│   └── reports/
├── docker-compose.yml
└── Makefile
```

---

## 4. 开源项目：定选、移植映射与相关工作

本章是参考项目的**最终清单**。每个项目只有一种用法，P1 之后不再重新讨论；要改，先写 ADR。

### 4.1 用法定义

| 用法 | 含义 | 代码层面 |
|---|---|---|
| **依赖** | 作为库直接安装，版本锁定 | `pyproject.toml` / `package.json` 精确版本，登记到 `docs/third-party.md` |
| **逻辑移植** | 按对方的数据模型、不变量、算法用 Python 重新实现 | 不复制代码；模块 docstring 写明来源，例如 `Ported from TigerBeetle docs/coding/two-phase-transfers.md`；每条对应 §4.4 的一个编号，测试函数名带上该编号（如 `test_tb4_linked_rollback`） |
| **改写** | 读懂对方的任务或数据集，按我们的契约重写 | 用例文件头注明来源与 license |
| **参考** | 只借思路，不对应到具体模块 | 在技术文档「相关工作」中如实列出 |
| **不用** | 评估后放弃 | 本表记录放弃理由 |

### 4.2 定选清单

**依赖**（会出现在锁文件里的只有这些；「P0 锁定」的版本在 P0 建脚手架时确定并写入 `docs/third-party.md`）

| 项目 | 版本 | License（来源） | 层 | 用途 |
|---|---|---|---|---|
| langgraph | 1.2.16 | MIT（PyPI，V-LG） | L1 | `StateGraph`、`interrupt()`、`Command(resume=...)` |
| langgraph-checkpoint-postgres | 3.2.0 | MIT（PyPI，V-LG） | L1 | checkpoint 持久化；依赖 `psycopg[binary,pool]` |
| langchain-deepseek | 1.1.1 | MIT（PyPI，V-LG） | L1 | `ChatDeepSeek`；strict 工具绑定。`reasoning_content` 的处理见 §6.7 |
| openai（官方 Python SDK） | P0 锁定 | Apache-2.0（PyPI，2026-10-23） | L1 | 仅在 §6.7 的第二级回退中使用 |
| cn2an | 0.5.24 | MIT（PyPI，V-CN） | L2 | 中文数字转阿拉伯数字（「三千五」→ 3500） |
| PyJWT + cryptography | P0 锁定 | MIT；Apache-2.0 OR BSD-3-Clause（PyPI，2026-10-23） | L2、L1 | EdDSA（Ed25519）JWT 签发与验证 |
| FastAPI、Pydantic v2、SQLAlchemy 2.x、asyncpg、Alembic、httpx、psycopg | P0 锁定 | MIT / BSD-3-Clause / Apache-2.0（asyncpg）/ LGPL-3.0（psycopg，作为库动态使用）（PyPI，2026-10-23；psycopg 在 P0 复核） | 全部 | 通用 |
| pytest、pytest-asyncio、hypothesis、ruff | P0 锁定 | MIT / Apache-2.0（pytest-asyncio）/ MPL-2.0（hypothesis）/ MIT（PyPI，2026-10-23） | 全部 | 测试与风格 |
| Next.js、React、Tailwind CSS、Recharts | P0 锁定 | MIT（npm registry，2026-10-23） | 前端 | App 框架与图表 |
| Playwright | P0 锁定 | Apache-2.0（npm registry，2026-10-23） | 前端测试 | 演示脚本冒烟 |

**逻辑移植**（本项目复用最多的方式，逐条映射见 §4.4）

| 项目 | License | 移植到 | 移植什么 |
|---|---|---|---|
| TigerBeetle（tigerbeetle/tigerbeetle） | Apache-2.0 | `bankcore.ledger`、L3 `accounts` / `transfers` | 四个累计量的账户模型、余额边界标志、两阶段转账、链式原子、客户端生成幂等 id、只追加与反向更正、历史余额、「先查后转不原子」的教训 |
| Blnk（blnkfinance/blnk） | Apache-2.0 | L2 `preflight`、L2 `audit` | 「同一份算术 + 不改原状态」的预演方式、累积批量预演、拒绝码与真实执行一致、前后余额投影、余额条件监控、哈希链审计 |
| Apache Fineract（apache/fineract） | Apache-2.0 | `bankcore.standing`、L3 `standing_instructions` | 定期划转指令的类型、状态、周期、优先级枚举 |
| CaMeL（google-research/camel-prompt-injection，arXiv:2503.18813） | Apache-2.0 | L2 `taint` | 控制流（来自用户）与数据流（来自工具）分离；不可信数据不能决定敏感参数。README 自述为研究制品、可能有 bug、不计划修复，所以不作依赖 |
| FIDES（microsoft/fides，arXiv:2505.23643） | MIT | L2 `taint` | 机密性与完整性标签、动态污点跟踪 |
| Progent（sunblaze-ucb/progent，arXiv:2504.11703） | MIT | L2 `policy` 的工具策略表 | 工具名 + 参数级的最小权限策略 |

**改写**

| 项目 | License | 改写成什么 |
|---|---|---|
| AgentDojo（ethz-spylab/agentdojo）`src/agentdojo/default_suites/v1/banking/` | MIT | 16 个用户任务、9 个注入任务（v1.2 按 `^class` 统计）的构造方式 → `evaluation/scenarios/` 中 A2、A7 的变体与若干正常用例。判定改为 L3 数据库状态。**不接入其 runner** |

**参考**（只进「相关工作」，不对应模块）

| 项目 | 借鉴点 | 限制 |
|---|---|---|
| BankPilot（souravmighty/banking-agent） | 超阈值转账要 OTP、工具层校验收款人已授权 | **没有 LICENSE，一行代码都不复制** |
| Rasa starterpack-retail-banking-en | 转账 flow 的步骤拆分（收款人不存在先新增、余额检查、立即 / 定时分支） | **没有 LICENSE 文件**；运行需要 Rasa Pro |
| redis-developer/banking-agent-semantic-routing-demo | 意图路由与槽位追问的组织方式 | MIT；源码已读，没有写操作 |
| devbanksdk（ranjankumar-gh/devbanksdk） | 客户、账户、卡、交易的字段命名；`data/generator.py` 的流水生成思路 | MIT；源码已读：没有认证、转账没有行锁（`api/transactions.py:123-131` 两次普通 select 后改余额）、没有幂等键、卡日限额只存不查、没有测试，所以不 fork |
| Formance Ledger（formancehq/ledger） | 多方资金流的表达方式（只读公开文档） | **仓库根目录没有 LICENSE**，不读代码 |
| InjecAgent、Agent Security Bench | 间接注入的攻击类型清单 | 只参考数据 |
| AEGIS、Cordon、Sapien、DreamGuard、SafeMCP、ToolEmu、SagaLLM | 见 §4.5 | 只读了摘要 |

**不用**

| 项目 | 原因 |
|---|---|
| Invariant Guardrails | V-INV：三段链式写法求值报错，至今没有规则成功运行；维护停滞；注入检测器据源码依赖英文模型。序列规则自研（§7.9），几百行 Python |
| Prompt Guard 2（LlamaFirewall 的注入分类器）、protectai DeBERTa 注入分类器 | V-PG：语言不含中文；Prompt Guard 2 还需人工审批下载 |
| assistant-ui（`@assistant-ui/react-langgraph`）与 `@langchain/langgraph-sdk` | 要求后端兼容 LangGraph Server 的流协议（`messages` tuple、`updates.__interrupt__`、`values` 对齐），而该协议的官方实现 `langgraph-api` 是 Elastic-2.0。兼容它要额外处理 `values` 对齐与 UI 消息，收益只是省下约 300 行面板代码。改为自定义 SSE 协议（§6.6） |
| langgraph-api / LangGraph Server | Elastic-2.0，且是以内存为主的开发服务器 |
| NeMo Guardrails、Guardrails AI、LLM Guard（已 archived） | 定位是对话输入输出过滤，与计划级预演不符 |
| OPA、Cedar | Python 规则 + YAML 足够，引入策略语言只增加成本 |
| Promptfoo | 判定以 L3 数据库状态为准，自研 runner 更直接 |
| Bank of Anthos | 微服务与 K8s 过重；「没有卡 / 订阅 / 理财」是早先调研的结论，未重新读代码【待核实】，不影响「不用」的结论 |
| mcp-mifosx-self-service | 只是 Fineract API 的 MCP 封装 |
| TigerBeetle、Blnk、Fineract 的**部署** | Zig / Go / Java 写的独立服务，无法与 L2 共用 `bankcore`，预演和执行会分成两套逻辑，违背核心设计。只移植设计 |
| shadcn/ui 等复制式组件库 | 银行 App 的视觉要自成一体，组件自己写（§9.6） |

### 4.3 关键决策的证据

| 决策 | 证据 | 结论 |
|---|---|---|
| 不用 Invariant | V-INV | `RISKY_SEQUENCE` 等序列规则用 §7.9 的自研匹配器 |
| 不用 ML 注入分类器 | V-PG | 用 `policies/injection_patterns.yaml` 的中文规则启发式给不可信字段打 `suspicious` 标记，**只能升级风险** |
| 不靠 LangChain 回传 `reasoning_content` | V-LC | §6.7：只有 `plan` 节点开思考，且每次是不带历史 assistant 消息的单轮新请求，不触发回传要求 |
| 预演与执行同一份代码 | V-BL：Blnk 的 `PreviewTransaction` 正是这样做的，并在注释中写明理由（避免预演与执行漂移） | §7.8 照此实现，并用属性测试证明一致 |
| 计划执行用数据库事务而不是补偿 | V-BL：Blnk atomic 批量是补偿，补偿可能失败；V-TB：TigerBeetle linked events 在链内回滚 | §7.11：一份计划一个 L3 数据库事务 |
| 自定义 SSE 协议 | V-LG、V-AUI | §6.6 |

### 4.4 逻辑移植映射

「复核」列：✔ 表示 2026-10-22 已读原文确认语义；◐ 表示只读了部分（说明见备注）；☐ 表示只读了摘要。实现该条前要再读一遍原文，并在 docstring 中引用。

#### 4.4.1 TigerBeetle → `packages/bankcore/ledger.py` 与 L3 账本

| # | 移植的设计 | 来源（`tigerbeetle/tigerbeetle`） | 落点 | 我们的改动 | 复核 |
|---|---|---|---|---|---|
| TB-1 | 账户不存「余额」，只存 `debits_posted`、`credits_posted`、`debits_pending`、`credits_pending` 四个累计量；全体账户 Σdebits = Σcredits | `docs/reference/account.md` | `accounts` 表、`bankcore.Account` | 整数分；账户增加 `normal_side`（D / C）用于计算展示余额（§8.2） | ✔ |
| TB-2 | 余额边界标志 `debits_must_not_exceed_credits`：`debits_pending + debits_posted + amount > credits_posted` 时拒绝 | `docs/reference/account.md`、`docs/coding/recipes/balance-bounds.md` | `accounts.debits_must_not_exceed_credits`；`bankcore` 的 `INSUFFICIENT_FUNDS` | 所有客户存款账户都带此标志；数据库另加行级 `CHECK` 兜底 | ✔ |
| TB-3 | 两阶段转账：`pending` 冻结 → `post_pending_transfer`（可部分过账，余量退回）或 `void_pending_transfer`；超时自动释放；一个 pending 最多被处理一次 | `docs/coding/two-phase-transfers.md`、`docs/reference/transfer.md` | 理财申购（冻结 → 确认过账）、理财赎回在途 | 超时用模拟时间 `expires_at_sim`（不是秒数），由 scheduler 处理；部分唯一索引保证只处理一次（§8.2） | ✔ |
| TB-4 | 链式事件：带 `linked` 标志的一串事件全部成功或全部失败；链内顺序执行，前一个的效果对后一个可见；首个失败事件报真实错误，其余报 `linked_event_failed` | `docs/coding/linked-events.md` | **计划级原子执行**（§7.11） | 推广到所有写操作；用一个 Postgres 事务实现；首个失败步骤报真实代码，其余报 `LINKED_STEP_FAILED` | ✔ |
| TB-5 | 幂等：`id` 由客户端生成并先持久化，重试用同一个 `id`；已存在则返回 `exists` | `docs/coding/reliable-transaction-submission.md` | `transfers.id`、`plan_executions(customer_id, plan_id)` | 「客户端」是 L2：`transfer_id = bankcore.ids.transfer_id(plan_id, step_id, leg)`（uuid5）；重复执行返回首次结果 | ✔ |
| TB-6 | 转账不可修改、不可删除；更正靠追加反向转账 | `docs/reference/transfer.md`、`docs/coding/recipes/correcting-transfers.md` | `transfers` 只追加（触发器禁止 UPDATE / DELETE） | 冲正写 `code=8` 的反向转账，指向原转账 | ✔ |
| TB-7 | 用 pending + timeout 实现限流窗口 | `docs/coding/recipes/rate-limiting.md` | 不采用 | 卡日累计与 24h 转出用聚合查询实现，更易解释 | ✔ |
| TB-8 | `ledger` 划分可互相转账的账户集合；`code` 是用户自定义的类别枚举 | `docs/reference/account.md`（`transfer.md` 只读了标题） | `accounts.ledger`、`accounts.code`、`transfers.code` | 本期只有 `ledger=CNY`；`code` 取值见 §8.2 | ◐ |
| TB-9 | 带 `history` 标志的账户保留每笔转账后的余额 | `docs/reference/account-balance.md` | `transfers.debit_available_after_cents` / `credit_available_after_cents` | 存的是「按 `normal_side` 计算的可用余额」，不是四个累计量的快照 | ✔ |
| TB-10 | 不能「先查余额再转账」，两者不是原子的；条件检查必须和转账在同一原子操作内 | `docs/coding/recipes/balance-conditional-transfers.md` | L3 执行时 `SELECT … FOR UPDATE` 后在同一事务内调 `bankcore` | 不用控制账户技巧，直接靠数据库行锁 | ✔ |

#### 4.4.2 Blnk → L2 预演与审计

| # | 移植的设计 | 来源（`blnkfinance/blnk`） | 落点 | 我们的改动 | 复核 |
|---|---|---|---|---|---|
| BL-1 | 预演跑**与真实过账相同的算术**，作用于余额的深拷贝；不写交易、不改余额、不入队列、不发 webhook、不消耗 reference | `transaction_dryrun.go`（`PreviewTransaction` 的注释） | `gateway/preflight/simulator.py` | 「同一份算术」= 调 `bankcore` 的同一批 `apply_*`；`BankState` 不可变，天然不改原状态 | ✔ |
| BL-2 | 预演结果是「建议性」的，真实执行前状态可能变化；需要占住资金应该用 inflight | `transaction_dryrun.go` | 执行前总是在新快照上复演（§7.11） | 不在预演时冻结资金 | ✔ |
| BL-3 | 批量预演的累积模式：顺序执行时后一笔能看到前一笔的效果，发现「一批之内把钱花超」 | `transaction_dryrun_bulk.go:29-42、65-68` | 预演器按步骤数组顺序累积执行（§7.8） | 我们只有累积模式，因为执行也是顺序的同一事务 | ✔ |
| BL-4 | 预演的拒绝码与真实过账的错误码相同 | `model/transaction_preview.go:63-72` | 执行期违规码（§7.15 中 stage=`preflight` 且 `l3=yes`）预演与 L3 同一枚举 | — | ✔ |
| BL-5 | 预演输出每个余额的「当前 / 结果」两组值（`BalanceProjection`） | `model/transaction_preview.go:74-91` | `Decision.preflight.steps[].balances`，确认面板的「执行前 / 执行后」 | 另加 24h 转出、卡当日已用、持仓 | ◐ 第 92 行以后未读 |
| BL-6 | 可用余额 = 余额 − 在途借记（开启 queued 检查时再减排队借记） | `model/model.go:156`（`canProcessTransaction`） | 与 TB-1 一致：`debits_pending` 即在途 | 我们没有排队概念 | ✔ |
| BL-7 | 余额监控：`Condition{Field, Operator, Value}` 在余额更新时求值 | `model/model.go`（`CheckCondition`，仅 grep 到 `> < != ==` 分支） | `SCHEDULED_SHORTFALL` 与 App「低余额提醒」 | 在预演结果上求值，而不是只在真实更新后 | ◐ 函数体未读 |
| BL-8 | 交易哈希链：把交易封进防篡改链，校验方用相同字段重算 | `chain_worker.go`、`model/chain.go` | `gateway.audit_events` 的哈希链（§7.13） | 同步写入，用 advisory lock 串行化，不用后台 worker | ✔ |

#### 4.4.3 Fineract → `packages/bankcore/standing.py` 与 L3 定期指令

| # | 移植的设计 | 来源（`apache/fineract`，`fineract-provider/.../portfolio/account/domain/`） | 落点 | 我们的改动 | 复核 |
|---|---|---|---|---|---|
| FN-1 | 指令类型 `FIXED`（固定金额）/ `DUES`（按应付金额） | `StandingInstructionType.java` | `standing_instructions.amount_type` | 客户定时转账 = FIXED；商户代扣 = DUES，金额取商户账单，不得超过授权上限 `max_amount_cents` | ✔ |
| FN-2 | 状态 `ACTIVE` / `DISABLED` / `DELETED` | `StandingInstructionStatus.java` | `standing_instructions.status` | 取消订阅 / 取消定时转账 = `DELETED`；`DISABLED` 本期不开放 | ✔ |
| FN-3 | 周期 `PERIODIC` / `AS_PER_DUES` | `AccountTransferRecurrenceType.java` | `recurrence` + `period`（`ONCE` / `WEEKLY` / `MONTHLY`） | `ONCE` 是我们新增的，用于「指定日期单次转账」 | ✔ |
| FN-4 | 优先级 `URGENT` / `HIGH` / `MEDIUM` / `LOW` | `StandingInstructionPriority.java` | 同一模拟时刻多条到期指令的执行顺序：`bankcore.standing.order_key(run_at, priority, id)` | **执行顺序语义是本项目定义的**（只读了枚举，未读 Fineract 调度器）。默认：客户定时转账 HIGH、代扣 MEDIUM | ◐ |

#### 4.4.4 CaMeL / FIDES / Progent → L2 `taint`、`policy`

| # | 借用的思想 | 来源 | 落点 | 我们的简化 | 复核 |
|---|---|---|---|---|---|
| CM-1 | 用户指令决定控制流；工具返回的数据只能作为数据，不能决定敏感参数 | CaMeL，arXiv:2503.18813 | §7.6 污点规则 | 不做解释器级的数据流跟踪，只在计划的敏感参数上做「值来源」判定 | ☐ |
| FD-1 | 给数据打完整性标签并动态跟踪污点 | FIDES，arXiv:2505.23643 | `TrustLabel` 与读取账本 | 标签只有几级（§7.6）；「实体取各属性中最差一级」是我们自己的规则，不声称来自 FIDES | ☐ |
| PG-1 | 工具名 + 参数级的最小权限策略 | Progent，arXiv:2504.11703 | `policies/tools.yaml`：工具 → 基础等级、允许的 origin、敏感参数；未登记的工具一律 DENY | 策略是静态的，不做 LLM 生成或动态更新 | ☐ |

### 4.5 相关工作与创新边界

2026-10-22 用 arXiv API 检索，实际查询包括：`abs:agent AND abs:"tool" AND abs:"pre-execution"`、`"world model" + safety + tool`、`transaction/rollback/dry-run + agent`、`stateful + policy/guardrail + multi-step`、`look-ahead + tool + safety`、`LLM agent + banking/financial + security`（`"dry run"` 的查询没有结果）。以下工作高度相关，**全部只读了摘要**，P10 写技术文档前要读方法部分。

| 工作 | 摘要中与我们重合的部分 | 我们的定位（只写我们确实做了的，不断言对方缺少什么） |
|---|---|---|
| AEGIS（arXiv:2603.12621） | 执行前拦截工具调用；高风险调用转人工审批；Ed25519 签名 + SHA-256 哈希链审计 | 摘要中未描述业务状态模拟；我们对整份计划做银行账本预演 |
| Cordon（arXiv:2606.17573） | 任务级「语义事务」；可逆变更在影子状态中执行；对外效果暂存；提交前验证组合后的执行流，发现跨步骤违规 | 我们的影子状态是银行账本的领域模型，与真实账本共用一份代码，并按金融约束（余额、限额、适当性、未来扣费）给出结论 |
| Sapien（arXiv:2610.00797） | 有状态的上下文策略；用带状态谓词的正则表达式描述允许的工具调用序列 | 我们的序列规则同属此类；区别在于与账本预演、污点判断组合决策，且规则的状态来源是 L3 账本 |
| DreamGuard（arXiv:2608.05695）、SafeMCP（arXiv:2606.01991） | 用（学习得到的）世界模型做前瞻性风险判断 | 我们的「世界模型」是确定性的账本规则，结论可复现、可审计 |
| ToolEmu（arXiv:2309.15817） | 用 LM 模拟工具执行来发现风险（测试用途） | 我们在运行时用确定性规则模拟 |
| SagaLLM（arXiv:2503.11951） | 多步计划的事务保障与补偿 | 我们用数据库事务实现原子性 |
| CaMeL、FIDES、Progent | 见 §4.4.4 | 金融参数级的轻量实现 |
| Blnk dry run | 账本级预演 | 我们预演 Agent 生成的跨业务计划（转账 + 卡 + 订阅 + 理财），并组合工作流与污点判断 |

**对外表述**：不说「首个执行前预演」「首个影子状态」「首个有状态策略」。说：「把确定性的金融账本预演、跨会话工作流规则、参数级污点与复述识别组合进银行 Agent 的强制网关，并以 L3 数据库状态量化攻击成功率。」

### 4.6 引用规则

1. 引入任何依赖前确认 license 允许修改和比赛使用，登记到 `docs/third-party.md`（项目、版本、license、用途、核实来源）。
2. 没有 LICENSE 的项目（BankPilot、Rasa starterpack、Formance Ledger）一律不复制代码，也不复制其文档段落。团队曾讨论「只是本地使用」，但作品源码要提交给主办方，属于对外分发，所以规则不变。
3. 逻辑移植的模块在 docstring 中写明来源与 §4.4 编号；对应测试函数名带编号。
4. 答辩材料与技术文档的「相关工作」必须列出 §4.5 全部条目，如实说明关系。

---

## 5. 功能需求：六大业务场景

### 5.0 通用约定

- 工具分为 `read`（只读）和 `write`（改变状态）。read 工具经 L2 `/tools/read/{name}` 调用；write 工具只能出现在 ActionPlan 中，由 L2 评估、审批后执行。参数定义见 §6.3。
- 写操作有两个发起方，走**同一条** L2 流水线：
  - 小安：L1 用委托令牌提交计划，L2 记 `origin=agent`。
  - App 表单：转账页、卡片页等页面由 BFF 提交计划，L2 记 `origin=app_form`。表单计划只能有**一个**步骤，参数只来自用户在该页面上亲手编辑的字段（页面不得从 URL 参数或助手卡片预填写操作参数，§9.3）。
- 基础风险等级来自 `policies/tools.yaml`；L2 只会上调，**永不下调**。
  - 绿：自动执行（保护性操作）
  - 黄：确认面板上点击确认
  - 红：确认 + 6 位 OTP（模拟短信，§7.11）
- 资金流向分类（`tools.yaml` 的 `outflow` 字段）：`external`（转给他人、AA 不涉及资金）、`internal`（本人名下账户互转、申购、赎回）、`none`。阈值与累计只统计 `external`。
- 默认阈值（`policies/thresholds.yaml`）：

| 键 | 默认值 | 含义 |
|---|---|---|
| `single_red_cents` | 100000 | 单笔对外转出 > 1,000.00 元为红，≤ 为黄 |
| `outflow_24h_red_cents` | 500000 | 滚动 24h 对外转出（历史 + 本计划）> 5,000.00 元，升红（`CUMULATIVE_OUTFLOW`） |
| `outflow_24h_hard_cents` | 5000000 | 滚动 24h 对外转出 > 50,000.00 元，拒绝（`DAILY_OUTFLOW_LIMIT`） |
| `new_payee_window_min` | 30 | 收款人创建后 30 个模拟分钟内对其转账，升红（`NEW_PAYEE_FIRST_TRANSFER`） |
| `split_count` / `split_sum_cents` | 3 / 100000 | 24h 内对同一收款人 ≥ 3 笔且合计 > 1,000.00 元，升红（`SPLIT_TRANSFER`） |
| `risky_sequence_window_min` | 120 | `RISKY_SEQUENCE` 的窗口 |
| `duplicate_window_min` | 10 | `DUPLICATE_PLAN`、`REJECTED_RETRY` 的窗口 |
| `shortfall_days` | 7 | `SCHEDULED_SHORTFALL` 的前推天数 |
| `approval_ttl_s` | 300 | 审批有效期（真实时间） |
| `utterance_window_s` | 600 | 原话授权窗口（真实时间，§7.4） |

### 5.1 场景一：智能转账

| ID | 需求 | 工具 | 基础等级 | App 页面 |
|---|---|---|---|---|
| T-1 | 按收款人姓名 / 别名 / 手机号转账，有歧义时追问；也支持本人账户间互转 | `list_payees`、`get_accounts`(read)；`transfer`(write) | 对外：按金额黄 / 红；本人互转：黄 | 转账页 / 小安 |
| T-2 | 新增收款人 | `add_payee`(write) | 黄 | 收款人管理 / 小安 |
| T-3 | 定时转账：指定日期单次，或每周 / 每月周期 | `schedule_transfer`(write)、`list_scheduled`(read)、`cancel_scheduled`(write) | 创建：按单次金额同 `transfer`；取消：黄 | 定时转账页 / 小安 |
| T-4 | AA 收款：输入总额与参与人，生成收款请求，可看收款进度 | `create_split_request`(write)、`get_split_status`(read) | 黄 | AA 收款页 / 小安 |
| T-5 | 转账前展示预演结果（转后余额、24h 累计、触发的限额） | 由 L2 提供 | – | 确认面板 |

验收要点：
- AA 分摊用整数分计算，余数分给发起人，例如 100.00 元 3 人分为 33.34 / 33.33 / 33.33（`bankcore.split.allocate`）。AA 只生成收款请求，不产生资金流出；参与人付款由演示控制台模拟。
- 定时转账到期由 L3 scheduler 发起，执行前经 L2 授权（§7.12）。余额不足时该次执行记为 `FAILED_INSUFFICIENT`，不透支、不重试。
- 同一计划重复执行只产生一次记账（TB-5）。

### 5.2 场景二：账单分析

| ID | 需求 | 工具 | 等级 | App 页面 |
|---|---|---|---|---|
| B-1 | 按时间、类别、商户查询流水 | `get_transactions`(read) | 绿 | 账单页 |
| B-2 | 消费分类汇总（月度、类别占比、环比） | `get_spending_summary`(read) | 绿 | 账单页图表 |
| B-3 | 异常交易识别 | `detect_anomalies`(read) | 绿 | 账单页「异常」标签 |
| B-4 | 生成月度账单报告（文字 + 图表数据） | `get_spending_summary` + L1 | 绿 | 小安 `SpendingReportCard` → 跳转账单页 |

实现要求：
- 分类和异常检测由 L3 调 `bankcore.analytics` 的确定性规则计算，LLM 只负责解读和表达。
- 分类：按 `mcc` 映射到 8 个类别（餐饮、交通、购物、娱乐、生活缴费、医疗、订阅、其他）；无 `mcc` 的转账归「转账」。
- 异常规则（分析标签，不参与授权决策，代码见 §7.15）：`ANOMALY_AMOUNT_OUTLIER` 金额超过该类别近 90 天均值 + 3 倍标准差；`ANOMALY_NIGHT_LARGE` 00:00–05:00 单笔 > 500 元；`ANOMALY_DUPLICATE` 同商户 10 分钟内同金额重复；`ANOMALY_NEW_MERCHANT_LARGE` 首次出现的商户且金额 > 1,000 元。
- 回复中出现的每个数字都必须来自工具返回值（§6.8）。
- 交易备注、商户名、对方户名是**不可信数据**（§7.6）。

### 5.3 场景三：卡片管理

| ID | 需求 | 工具 | 基础等级 | App 页面 |
|---|---|---|---|---|
| C-1 | 查询卡状态与各项限额 | `get_cards`(read) | 绿 | 卡片页 |
| C-2 | 调低限额 | `update_card_limits`(write) | 黄 | 卡片页滑杆 / 小安 |
| C-3 | 调高限额 | 同上 | 红 | 同上 |
| C-4 | 交易渠道开关：境外、线上、ATM、免密 | `update_card_controls`(write) | 黄（全部是关闭）/ 红（任一开启） | 卡片页开关 |
| C-5 | 临时冻结 / 解冻 | `freeze_card`（绿，保护性）/ `unfreeze_card`（红） | | 卡片页按钮 |
| C-6 | 挂失（不可逆，补卡生成新卡号） | `report_card_lost`(write) | 红 | 卡片页「挂失」 |

方向由 `bankcore.classify.card_change(snapshot_card, args)` 计算：任一限额上调、任一开关由关变开、解冻 → `CARD_LOOSEN`（红）；其余 → `CARD_TIGHTEN`（黄）；冻结 → `CARD_FREEZE`（绿）。同一步里有升有降按 `CARD_LOOSEN` 处理。

### 5.4 场景四：订阅管理

| ID | 需求 | 工具 | 等级 | App 页面 |
|---|---|---|---|---|
| S-1 | 自动识别周期性扣费（从流水中挖掘） | `detect_subscriptions`(read) | 绿 | 订阅页 |
| S-2 | 查看订阅列表、下次扣费日期与金额、年化花费 | `list_subscriptions`(read) | 绿 | 订阅页 |
| S-3 | 扣费提醒：未来 N 天将扣费的订阅 | `upcoming_charges`(read) | 绿 | 首页「即将扣费」 |
| S-4 | 取消订阅（删除代扣指令，之后商户的扣款被 L3 拒绝） | `cancel_subscription`(write) | 黄 | 订阅页 / 小安 |
| S-5 | 识别闲置订阅并建议取消 | `detect_subscriptions` + L1 | 绿 | 订阅页「闲置」标签 |

实现要求：
- 识别规则（`bankcore.analytics.detect_recurring`）：同一商户、金额波动 ≤ 5%、间隔 28–31 天或 6–8 天、出现 ≥ 3 次。
- 闲置判定：同一商户近 60 天内没有非扣费类消费。
- 代扣是 `standing_instructions` 中 `kind=MERCHANT_MANDATE` 的指令（FN-1）；取消即 `status=DELETED`。
- 商户扣款流程：模拟时钟到达 `merchant_bills.due_at` 时，L3 查找该商户对该客户的 ACTIVE 代扣指令。找到 → 经 L2 授权（§7.12）后扣款；找不到 → 记 `instruction_runs(status=DENIED, reason_code=MANDATE_NOT_ACTIVE)` 并推送通知「已拦截一笔已取消订阅的扣款」。

### 5.5 场景五：理财操作

| ID | 需求 | 工具 | 基础等级 | App 页面 |
|---|---|---|---|---|
| W-1 | 风险测评问卷（固定 6 题，得出 R1–R5） | `get_risk_questionnaire`(read)；`submit_risk_assessment`(write，**只允许 app_form**) | 黄 | 财富页「风险测评」 |
| W-2 | 产品列表与比较（风险等级、近 30 天收益、期限、起购金额、确认与到账规则） | `list_products` / `compare_products`(read) | 绿 | 财富页 / 小安 `ProductCompareCard` |
| W-3 | 申购 | `subscribe_product`(write) | 红 | 产品详情页 / 小安 |
| W-4 | 赎回（按份额） | `redeem_product`(write) | 黄 | 持仓页 |
| W-5 | 持仓与在途订单查询 | `get_holdings`(read) | 绿 | 财富页 |

合规硬规则（L2 确定性执行）：
- 未完成测评或测评已超过 12 个月 → `ASSESSMENT_MISSING`，DENY；小安用 `RiskAssessmentCard` 引导去测评页。
- 产品风险等级高于客户测评等级 → `SUITABILITY_MISMATCH`，DENY（「适当性不匹配」）。
- 24h 内测评等级上调过 → 依赖新等级的申购 DENY（`ASSESSMENT_JUST_CHANGED`），防止「先改测评再买」。
- 小安不能提交测评（`submit_risk_assessment` 的 `allowed_origins: [app_form]`；L1 工具清单中没有它）。
- 申购的确认面板必须展示风险揭示（`products.risk_disclosure`），用户打开揭示全文后才能勾选「我已阅读」（§7.11）。
- 预演算出：申购后活期可用余额、确认日、预计份额（按最新净值估算，仅展示，不参与校验）、赎回到账日。
- 申购与赎回都是两阶段（TB-3），细节见 §8.2。

### 5.6 场景六：跨场景联动

联动是多场景工具组成的一份 ActionPlan，L2 对整份计划做预演，L3 在一个事务内执行。验收至少覆盖：

| ID | 用户输入示例 | 计划内容 | 预期 |
|---|---|---|---|
| X-1 | 分析我上月消费，找出异常交易，并把卡的单笔限额设成 1000 | read：汇总、异常 → write：调低限额 | 黄，一次确认 |
| X-2 | 找出没用的订阅取消掉，省下来的钱每月 1 号自动转到我的储蓄账户 | read：识别 → write：取消 ×N + 定时本人互转（金额为 `derived` 求和，§6.2） | 黄；确认面板展示「每月节省 = 定时转账金额」及算式 |
| X-3 | 把下周要扣的会员费留够，剩下的钱买稳健型理财 | read：即将扣费、余额、产品 → write：申购（金额 = 可用余额 − 未来 7 天扣费，`derived`） | 李四：红（申购）+ OTP；预演保证申购后可用余额 ≥ 未来 7 天扣费，否则 REPLAN（`SCHEDULED_SHORTFALL`） |
| X-4 | 帮我把这笔可疑交易对应的卡冻结，再从储蓄转 2000 到活期应急 | write：冻结 + 本人互转 | 冻结绿、本人互转黄，整份计划黄，一次确认 |

---

## 6. L1 Banking Agent 设计

### 6.1 LangGraph 状态图

```text
START → [ingest] → [understand] ─┬─ missing 非空 ─────────────▶ [clarify] → END
                                 ▼
                             [gather] ─┬─ needs_write = false ─▶ [respond]
                                       ▼
                                    [plan] → [evaluate] ──▶ 按 §6.1.2 路由
                                                           │
                       REQUIRE_APPROVAL ─▶ [await_approval]（interrupt）
                                                           │ resume(approval_id | "abandoned")
                                                           ▼
                                                   [check_approval] ──▶ 按 §6.1.2 路由
                                                           │ CONFIRMED / ALLOW
                                                           ▼
                                                      [execute] ──▶ 按 §6.1.2 路由
                                                           ▼
                                                      [respond] → [verify_output] → END
```

#### 6.1.1 节点规格

| 节点 | LLM | 思考 | 工具 | 读 → 写（状态字段） |
|---|---|---|---|---|
| ingest | 否 | – | – | 请求 → 重置本轮字段（见下）、`utterance_id`、`utterance_text` |
| understand | 是 | 关 | `report_understanding`（strict，强制调用） | 原话 + 最近 6 轮展示消息 → `understanding` |
| clarify | 是 | 关 | – | `understanding.missing` 或 L2 的 `replan_hints` → 追问消息 |
| gather | 是 | 关 | 16 个 read 工具（strict） | → `reads[]`；循环上限 6 次工具调用 |
| plan | 是 | 开（§6.7） | `submit_action_plan`（strict，强制调用） | 原话、`reads` 摘要、上次 `decision` 摘要 → `plan` |
| evaluate | 否 | – | – | `plan` → `decision`（含 L2 分配的 `plan_id`） |
| await_approval | 否 | – | – | `interrupt({"type":"approval_required","approval_id":…})` → `resume_value` |
| check_approval | 否 | – | – | L2 `GET /approvals/{id}/status` → `approval_status` |
| execute | 否 | – | – | L2 execute → `execution` |
| respond | 是 | 关 | – | → 文字 + `ui_cards[]` |
| verify_output | 否 | – | – | 校验（§6.8），不通过改写一次，仍不通过用模板 |

- `await_approval` 在调用 `interrupt()` 之前不得有任何副作用（resume 时该节点会从头重跑）。
- ingest 把本轮字段重置为：`understanding=None, reads=[], plan=None, decision=None, replan_count=0, approval_id=None, approval_status=None, execution=None, ui_cards=[]`。

#### 6.1.2 路由表

`codes.py` 给每个 REPLAN 代码标注 `replan_mode`（总表 §7.15）。

| 位置 | 条件 | 去向 |
|---|---|---|
| evaluate 后 | `ALLOW` | execute（L2 已为它创建 `CONFIRMED` 的策略审批，§7.11） |
| | `REQUIRE_APPROVAL` | await_approval |
| | `DENY` | respond（`BlockedCard`，不重试） |
| | `REPLAN` 且全部 finding 的 `replan_mode=auto`，`replan_count < 2` | `ENTITY_UNSEEN` → gather；其他 → plan；`replan_count += 1` |
| | `REPLAN` 且含 `replan_mode=ask_user`，或 `replan_count ≥ 2` | respond（展示原因与 `replan_hints`，等用户的新原话） |
| check_approval 后 | `CONFIRMED` | execute |
| | `REJECTED` / `EXPIRED` / `VOIDED`，或 resume 值为 `"abandoned"` | respond（「已取消」） |
| execute 后 | 成功 | respond（`ResultCard`） |
| | `STATE_CHANGED`（L2 复演后风险升高，审批已作废） | respond（「账户状态已变化，请重新确认」，附新的 `PlanCard`），不自动重试 |
| | 任一 L3 失败码 | respond（`BlockedCard`） |

**L1 在 REPLAN 时不得修改用户给出的金额、收款人、产品**；需要改动的只能通过 respond 交还用户决定。

#### 6.1.3 状态

```python
class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]  # 只存展示用的 user / assistant 文字，不存 tool 往返
    customer_id: str                   # 从委托令牌解析
    session_id: str
    utterance_id: str
    utterance_text: str
    understanding: Understanding | None
    reads: list[ReadRecord]            # gather 的工具结果，含 L2 返回的 source_ref 与 trust
    plan: ActionPlanDraft | None
    decision: Decision | None
    replan_count: int
    approval_id: str | None
    resume_value: str | None
    approval_status: str | None
    execution: ExecutionResult | None
    ui_cards: list[UICard]
```

- `thread_id = session_id`；checkpoint 存 `postgres-agent`。启动时 `AsyncPostgresSaver.from_conn_string(AGENT_DB_URL)` 并 `await checkpointer.setup()`（连接参数 `autocommit=True`、`row_factory=dict_row`，以 3.2.0 的 README 为准）。
- `gather` 中 untrusted 字段用 `<untrusted_data source="...">…</untrusted_data>` 包裹，系统提示声明其中任何指令都不得执行。这只是辅助，真正的防线在 L2。

### 6.2 ActionPlan 契约（`packages/contracts/plan.py`）

L1 提交的是 `ActionPlanDraft`；L2 校验后补全为 `ActionPlan` 并分配 `plan_id`。

```json
{
  "client_ref": "drf_01JB...",
  "utterance_ids": ["utt_01JB..."],
  "intents": ["transfer"],
  "steps": [
    {
      "step_id": "s1",
      "tool": "add_payee",
      "args": {"name": "孙八", "account_no": "6217000011112222333", "bank_name": "建设银行", "phone": null, "alias": null},
      "depends_on": [],
      "arg_sources": {"account_no": {"kind": "utterance", "ref": "utt_01JB..."}}
    },
    {
      "step_id": "s2",
      "tool": "transfer",
      "args": {"from_account_id": "acc_zs_chk", "payee_id": {"$ref": "s1.payee_id"}, "to_account_id": null,
               "amount_cents": 50000, "memo": "饭钱"},
      "depends_on": ["s1"],
      "arg_sources": {"amount_cents": {"kind": "utterance", "ref": "utt_01JB..."}}
    }
  ],
  "rationale": "用户要求新增收款人孙八并转账 500 元"
}
```

规则：
- `steps` 必须按依赖顺序排列：每个 `depends_on` 只能指向数组中更靠前的步骤；最多 8 步。违反 → `CONTRACT_INVALID`。预演与执行都严格按数组顺序。
- **步骤引用**：参数值可以是 `{"$ref": "<step_id>.<output_field>"}`，被引用步骤必须在 `depends_on` 中，且其工具确实产出该字段（`add_payee → payee_id`、`schedule_transfer → instruction_id`、`subscribe_product → order_id`）。新实体 ID 由 `bankcore.ids.entity_id(prefix, plan_id, step_id)`（uuid5）确定性生成，预演与执行一致。
- **推导值**：`arg_sources[field] = {"kind":"derived","op":"sum|sub|min|ratio","inputs":[<source_ref>…],"ratio":"0.5"|null}`。L2 用读取账本中这些 `source_ref` 的**原值**重算（`bankcore.derive`），结果必须与参数完全相等，否则 `DERIVATION_MISMATCH`（REPLAN）。可信度规则见 §7.6。
- `arg_sources` 只是 L1 给 L2 的「去哪里找」的提示，L2 独立核验；`intents`、`rationale` L2 不读取（只存档、只在评委视图展示，并标注「模型生成」）。
- `customer_id`、`session_id`、`origin`、`plan_id` 都不由 L1 提供。
- 规范化与哈希（`contracts.canonical`）：键排序、无空白、整数金额、Decimal 固定 4 位字符串。
  - `plan_hash = sha256(canonical({origin, steps: [{tool, args, depends_on}]}))`：审批绑定、确认面板校验、`DUPLICATE_PLAN` 都用它。
  - `$ref` 在哈希中保持引用形式（不展开）。

### 6.3 工具参数（契约 v1，P1 冻结）

可空字段在 strict Schema 中写成 `anyOf: [T, null]`；所有字段都列入 `required`。

**write**

| 工具 | 参数 | 产出 | outflow | 基础等级 | 敏感参数 | ActionKind |
|---|---|---|---|---|---|---|
| `transfer` | `from_account_id, payee_id\|null, to_account_id\|null, amount_cents, memo\|null`（`payee_id` 与 `to_account_id` 恰好一个非空） | `transfer_ids` | 对外 external / 本人 internal | 对外按金额；本人黄 | `payee_id`（属性：账号、户名）、`amount_cents` | TRANSFER / SELF_TRANSFER |
| `add_payee` | `name, account_no, bank_name, phone\|null, alias\|null` | `payee_id` | none | 黄 | `name`、`account_no` | ADD_PAYEE |
| `schedule_transfer` | `from_account_id, payee_id\|null, to_account_id\|null, amount_cents, period: ONCE\|WEEKLY\|MONTHLY, first_run_date, day_of_month\|null` | `instruction_id` | 同 `transfer` | 按单次金额同 `transfer` | 同 `transfer` + `period` | SCHEDULE |
| `cancel_scheduled` | `instruction_id` | – | none | 黄 | `instruction_id` | SCHEDULE_CANCEL |
| `create_split_request` | `total_cents, participants: [{name, phone}], memo\|null` | `split_id` | none | 黄 | `total_cents` | SPLIT |
| `update_card_limits` | `card_id, single_limit_cents\|null, daily_limit_cents\|null` | – | none | 按方向黄 / 红 | 全部 | 按方向 CARD_TIGHTEN / CARD_LOOSEN |
| `update_card_controls` | `card_id, overseas\|null, online\|null, atm\|null, contactless\|null` | – | none | 按方向黄 / 红 | 全部 | 同上 |
| `freeze_card` | `card_id` | – | none | 绿 | – | CARD_FREEZE |
| `unfreeze_card` | `card_id` | – | none | 红 | `card_id` | CARD_LOOSEN |
| `report_card_lost` | `card_id` | `new_card_id` | none | 红 | `card_id` | CARD_LOST |
| `cancel_subscription` | `instruction_id` | – | none | 黄 | `instruction_id` | SUB_CANCEL |
| `submit_risk_assessment` | `answers: [int]`（6 个，取值 1–4） | `level` | none | 黄；只允许 app_form | `answers` | RISK_ASSESS |
| `subscribe_product` | `product_id, from_account_id, amount_cents` | `order_id` | internal | 红 | `product_id`、`amount_cents` | FUND_BUY |
| `redeem_product` | `product_id, units`（4 位小数字符串） | `order_id` | internal | 黄 | `units` | FUND_SELL |

**read**（L2 从令牌注入 `customer_id`，参数中的所有 ID 都校验归属）

| 工具 | 参数 | 返回要点 |
|---|---|---|
| `get_accounts` | – | 账户 ID、类型、可用余额、账面余额 |
| `list_payees` | `query\|null` | 收款人 ID、户名、银行、脱敏账号、`provenance`、创建时间 |
| `get_transactions` | `account_id\|null, from_date, to_date, category\|null, merchant\|null, limit` | 流水（含 untrusted 字段） |
| `get_spending_summary` | `month`（YYYY-MM） | 类别汇总、环比、月末余额 |
| `detect_anomalies` | `month` | 异常交易与代码 |
| `get_cards` | – | 卡 ID、尾号、状态、限额、开关、当日已用 |
| `list_scheduled` | – | 定时转账指令 |
| `get_split_status` | `split_id` | AA 进度 |
| `detect_subscriptions` | – | 识别出的周期扣费与闲置标记 |
| `list_subscriptions` | – | 代扣指令、下次扣费、年化花费 |
| `upcoming_charges` | `days` | 未来扣费与定时转账 |
| `get_risk_questionnaire` | – | 题目（供 App 测评页与小安解释用） |
| `get_risk_profile` | – | 测评等级与日期 |
| `list_products` | `max_risk_level\|null` | 产品客观信息 |
| `compare_products` | `product_ids` | 对比数据 |
| `get_holdings` | – | 持仓、冻结份额、在途订单 |

- 工具 JSON Schema 由 Pydantic 模型生成：`contracts.schema.to_deepseek_strict(model)`。它负责满足 strict 限制（§6.7），并把 `$ref` 全部内联展开（不依赖 `$def` / `$defs` 拼写）。
- L1 绑定的 write 工具中**没有** `submit_risk_assessment`；`submit_action_plan` 的参数 Schema 就是 `ActionPlanDraft`。

### 6.4 新增工具的流程

`packages/contracts` 定义参数模型 → `bankcore` 写 `apply_*` 纯函数 → L3 实现执行分支 → L2 在 `policies/tools.yaml` 登记（基础等级、`outflow`、`allowed_origins`、敏感参数、ActionKind）→ L1 绑定 → 写正常用例与至少 1 条攻击用例。

### 6.5 会话与中断处理

- 新会话：App 调 BFF `POST /api/assistant/new-session` → L2 `POST /sessions` 签发新 `sid`（更新会话 cookie）→ L1 用该 `sid` 作为 `thread_id`。
- 用户在审批挂起时发新消息：BFF 先查 L1 `GET /v1/sessions/{sid}/state`；如有挂起的 `approval_id`，先调 L2 reject，再调 L1 `POST /v1/sessions/{sid}/resume {"value":"abandoned"}`，然后才提交新消息。
- 刷新页面：App 调 `GET /api/assistant/state`；有挂起审批时重新弹出确认面板；面板发现审批已 `CONFIRMED`（确认成功但 resume 未发出）时直接 resume，不再弹出。

### 6.6 流式接口（自定义 SSE 协议）

L1 用 FastAPI 实现，只被 BFF 调用：

| 方法 | 路径 | body | 返回 |
|---|---|---|---|
| POST | `/v1/sessions/{sid}/turns` | `{utterance_id, text, page_context\|null}` | SSE |
| POST | `/v1/sessions/{sid}/resume` | `{value}`（`approval_id` 或 `"abandoned"`） | SSE |
| GET | `/v1/sessions/{sid}/state` | – | `{pending_approval_id\|null, messages:[{id, role, text, cards}]}` |

- 请求头 `Authorization: Bearer <委托令牌>`；L1 用公钥验证，要求 `aud=gateway`、`scope` 含 `assistant`、`sid` 与路径一致、未过期。BFF 把最近一次的委托令牌放在 httpOnly cookie `dlg` 中，`resume` 时复用（审批有效期 5 分钟 < 委托令牌 10 分钟）。
- 实现：`graph.astream(stream_mode=["updates","custom"])`，由 `server/sse.py` 转成下列事件（`event:` + `data:` JSON，UTF-8）：

| event | data | 何时 |
|---|---|---|
| `status` | `{node, label}`，如 `{"node":"gather","label":"正在查询账户…"}` | 每个节点开始 |
| `clarify` | `{message_id, text}` | clarify 结束 |
| `approval` | `{approval_id}` | 遇到 interrupt |
| `final` | `{message_id, text, cards:[UICard]}` | verify_output 结束（文字已校验） |
| `error` | `{code, message}` | 异常 |
| `done` | `{}` | 流结束 |

- 回复不逐 token 流式输出，而是在 `verify_output` 之后一次性发出，避免把未经校验的数字先显示给用户；前端用打字机动画呈现。
- Next.js 的 BFF 路由 `export const runtime = "nodejs"; export const dynamic = "force-dynamic"`，直接 `return new Response(upstream.body, {headers: {"Content-Type": "text/event-stream", "Cache-Control": "no-cache"}})`，不缓冲。

### 6.7 LLM 接入

**模型（团队已确定）**

| 用途 | 模型 |
|---|---|
| 意图理解、追问、信息收集、计划生成、回复 | **DeepSeek V4 Pro** `deepseek-v4-pro` |
| 多模态（仅在有图片输入时） | **DeepSeek V4.1 Flash** `deepseek-flash`（本期没有必需的多模态功能，§16） |

**官方 API 事实**（2026-10-22 查阅 [DeepSeek API 文档](https://api-docs.deepseek.com/)、[价格页](https://api-docs.deepseek.com/quick_start/pricing/)、[更新日志](https://api-docs.deepseek.com/updates)）

| | V4 Pro | V4.1 Flash |
|---|---|---|
| API model ID | `deepseek-v4-pro` | `deepseek-flash` |
| 当前版本 | DeepSeek-V4-Pro-0813（2026-08-13 GA） | DeepSeek-V4.1-Flash-1017（2026-10-17 升级） |
| 上下文 / 最大输出 | 1M / 384K | 1M / 384K |
| Tool Calls / JSON Output | 支持 / 支持 | 支持 / 支持 |
| 图片输入 | **不支持** | 支持 |
| 思考模式 | 默认开启（effort 默认 `high`） | 同左 |
| 并发上限 | 500 | 2500 |
| 价格（美元 / 百万 token，闲时 / 高峰）| 输入命中缓存 0.022 / 0.044；未命中 0.66 / 1.32；输出 1.98 / 3.96 | 输入命中缓存 0.003 / 0.006；未命中 0.15 / 0.3；输出 0.6 / 1.2 |

- base_url：`https://api.deepseek.com`；strict 模式需 `https://api.deepseek.com/beta`。
- 高峰时段：周一至周五 UTC 01:00–04:00 与 06:00–10:00（北京时间 09:00–12:00 与 14:00–18:00），中国法定节假日除外；其余为闲时，价格减半。录制回放和跑评测放在闲时。
- 旧名 `deepseek-v4-flash`、`deepseek-v4-flash-vision-exp` 目前被临时路由到 V4.1 Flash，官方没有给出截止日期。项目只使用 `deepseek-v4-pro` 和 `deepseek-flash`。
- 价格以官方页面为准，官方声明价格可能调整。

**实现（`services/agent/app/llm/`）**

- `client.py`：`make_llm(node) -> ChatDeepSeek`，**显式**设置 `api_base="https://api.deepseek.com/beta"`（不依赖 V-LC 中只在默认 base 下才生效的自动切换），`model="deepseek-v4-pro"`。
  - 思考关（understand、clarify、gather、respond）：`extra_body={"thinking": {"type": "disabled"}}`，`temperature=0`。
  - 思考开（plan）：`extra_body={"thinking": {"type": "enabled"}}` 加 effort 参数（见 P0 实验 E2），不设 `temperature`。
- **`reasoning_content`**（V-LC）：唯一开思考的 `plan` 节点每次都**新建**消息列表 `[system, human(原话 + reads 摘要 + 上次 decision 摘要)]`，不含历史 assistant 消息；`gather` 有历史 assistant / tool 消息，但思考关闭，没有 `reasoning_content`。因此不触发回传要求。
- **结构化输出**：`understand` 与 `plan` 绑定单个 strict 工具并强制调用；L1 先用 Pydantic 校验，失败重试 1 次；L2 再校验。不使用 `json_object` 模式。
- strict Schema 限制（`to_deepseek_strict` 保证）：所有属性列入 `required`，`additionalProperties: false`；可选字段 `anyOf: [T, null]`；去掉 `minLength` 等长度约束（只在 Pydantic 里执行）；`$ref` 内联展开。
- **多模态**：凡带图片的消息，L1 调 L2 的 read 工具 `extract_image`（L2 内部调 `deepseek-flash`），抽取结果作为 `trust=untrusted` 写入读取账本，再交回主流程。本期 UI 不开放图片上传（§16 Q6）。

**P0 实验与决策规则**（结论写入 ADR-002）

| # | 实验 | 决策规则 |
|---|---|---|
| E1 | 带 tools 的多轮 gather（思考关）与单轮 plan（思考开）各 10 次，观察是否出现 `reasoning_content` 相关的 400 | 不出现 → 采用上述方案。出现 → 第一级回退：`DeepSeekWithReasoning(ChatDeepSeek)` 子类覆盖 `_get_request_payload`，把 `additional_kwargs["reasoning_content"]` 写回对应 assistant 消息；仍失败 → 第二级回退：官方 `openai` SDK 薄封装 |
| E2 | plan 节点：思考开（依次试 `reasoning_effort:"low"`、`thinking.effort:"low"`）、思考开默认 effort、思考关，× strict × 强制 `tool_choice`，各 20 次；统计 Pydantic 合法率与 p95 时延 | 选合法率 ≥ 95% 且 p95 ≤ 8s 的配置中思考最强的一个；都不满足 → 思考关 + strict + 1 次重试 |
| E3 | 强制 `tool_choice`（指定函数）在思考开时是否被接受 | 不接受 → 改为 `tool_choice="required"`（只绑定一个工具，效果相同） |
| E4 | strict 下 20 次 `submit_action_plan`，Pydantic 校验失败率 | > 10% → 改为非 strict + Pydantic 重试 2 次 |

**预算**：总调用约 3,000–5,000 次，按每次约 8K 输入、1K 输出、不计缓存估算：高峰价约 70 美元，闲时约 35 美元。P0 实测后在 ADR-002 修正。

**回放（`services/agent/app/llm/replay.py`）**

- `LLM_MODE=live|record|replay`。
- 确定性 ID：`record`、`replay` 或 `EVAL_MODE=true` 时，三层的 ID 生成都走 `bankcore.ids.next(prefix)` 的确定性计数器，种子为 `scenario_id + session_id`；审批、OTP 在这些模式下也确定性生成。
- L1 喂给 LLM 的摘要中不包含 `approval_id`、`expires_at`、`state_version`、`plan_id`（LLM 不需要）。
- 请求键 = `sha256(model ‖ canonical(messages 的 role 与 content) ‖ canonical(tools) ‖ canonical(params))`。
- 录制文件 `evaluation/replay/<scenario_id>.jsonl`，每行 `{key, response}`；replay 未命中时报错并输出 key，不回退到真实 API。
- 演示与评测默认 replay；`make demo LLM_MODE=live` 可现场用真实模型。

### 6.8 幻觉防护

1. **数字来源校验**（`verify_output`）：用正则提取回复中的金额（`¥`、`元`、中文数字经 `cn2an`）、日期、卡号尾号、份额，与本轮 `reads`、`decision`、`execution` 中出现过的值（含 `derived` 结果）比对。存在无来源的数字时改写一次，仍不通过就用模板回复。
2. **实体不臆造**：计划中的 ID 必须在本会话读取账本中出现过，或是本计划的 `$ref`（L2 ③），否则 `ENTITY_UNSEEN`。
3. **结果以 L3 为准**：卡片与确认面板的数字取自 L2 / L3 返回的结构化字段，LLM 只写解释文字。`ui_cards` 中的数值字段由 L1 代码从 `reads` / `execution` 复制，不由 LLM 生成。

---

## 7. L2 Security Gateway 设计（核心创新）

### 7.1 对外接口

凭证记号：
- `F` = 请求头 `X-Caller-Token: BFF_SERVICE_TOKEN`（只注入 frontend 服务端）
- `A` = 请求头 `X-Caller-Token: AGENT_SERVICE_TOKEN`（只注入 agent）
- `S` = 请求头 `X-Caller-Token: SCHEDULER_CALLBACK_TOKEN`（只注入 bank-backend）
- `U` = `Authorization: Bearer <会话 JWT>`（`aud=bff`）
- `D` = `Authorization: Bearer <委托令牌>`（`aud=gateway`、`scope=assistant`）

| 方法 | 路径 | 凭证 | 说明 |
|---|---|---|---|
| POST | `/auth/login` | F | `{customer_id, pin}` → 会话 JWT（新 `sid`） |
| POST | `/sessions` | F + U | 新建助手会话：签发带新 `sid` 的会话 JWT |
| POST | `/sessions/{sid}/utterances` | F + U | 登记用户原话 → `{utterance_id, delegation_token}` |
| POST | `/sessions/{sid}/assistant_messages` | F + U | BFF 把 L1 `final` / `clarify` 事件的文字登记到 L2（复述识别用，§7.6） |
| POST | `/tools/read/{name}` | (A + D) 或 (F + U) | 只读工具代理（§7.5）；A + D 时记入会话读取账本 |
| POST | `/plans/evaluate` | (A + D) 或 (F + U) | 评估计划。`origin` 由凭证推导：A + D ⇒ `agent`，F + U ⇒ `app_form` |
| POST | `/plans/{plan_id}/execute` | (A + D) 或 (F + U) | 复核后执行。A 只能执行 `origin=agent` 的计划，F 只能执行 `origin=app_form` 的计划，否则 `ORIGIN_NOT_ALLOWED` |
| GET | `/approvals/{approval_id}` | F + U | 确认面板数据：绑定的计划、`plan_hash`、预演、风险项、收款人展示信息 |
| GET | `/approvals/{approval_id}/disclosure` | F + U | 风险揭示全文 + `disclosure_token`（记录查看时间） |
| GET | `/approvals/{approval_id}/status` | (A + D) 或 (F + U) | 只返回 `{status}` |
| GET | `/approvals?status=PENDING` | F + U | 首页「待确认」 |
| POST | `/approvals/{approval_id}/confirm` | **只接受 F + U** | `{plan_hash, otp\|null, disclosure_token\|null}` |
| POST | `/approvals/{approval_id}/reject` | **只接受 F + U** | |
| GET | `/notifications` | F + U | App 消息中心：定时执行、代扣拦截、模拟短信 OTP |
| GET | `/audit/sessions/{sid}`、`/audit/plans/{plan_id}` | F + U | 审计链（只返回 `sub` 本人的） |
| POST | `/internal/scheduled/authorize` | S | 定期指令执行前授权（§7.12） |
| POST | `/demo/clock/advance`、`/demo/inject`、`/demo/reset`、`/demo/scenarios/{id}/load` | F + U，且 `DEMO_MODE=true` | 演示控制台；L2 转调 L3 `/internal/demo/*` |
| GET | `/demo/otp` | F + U，且 `DEMO_MODE=true` | 当前客户最近的模拟短信 |

- 所有 F + U 接口都校验资源归属：`sub` 必须是资源所有者；`/audit/sessions/{sid}` 还要求 `sid` 属于 `sub`。
- 所有 A + D 接口都校验：`D.sid` 与请求涉及的会话一致；资源属于 `D.sub`。
- 审批确认 / 拒绝、登录、新会话、原话登记、通知、演示接口**都不接受** A 或 D：即使 L1 被完全攻破，也无法替用户点确认、读 OTP、伪造原话或换身份。
- 授权矩阵在 `gateway/auth/matrix.py` 中静态声明；`tests/test_auth_matrix.py` 遍历「路径 × 凭证组合 × 跨客户」断言结果。

### 7.2 处理流水线（`/plans/evaluate`）

```text
ActionPlanDraft + 调用方凭证
  │
  ├─ ① 契约       Pydantic；工具已登记；origin 推导并检查 allowed_origins；步骤顺序与 $ref；app_form 只能单步
  ├─ ② 快照       L3 /internal/snapshot：BankState + state_version + sim_now（一次，后续共用）
  ├─ ③ 实体与原话 ID 归属；ID 在读取账本中出现过或为 $ref；utterance_ids 合法（§7.4）；按快照计算 ActionKind 与方向
  ├─ ④ 污点       敏感参数的值来源（§7.6）
  ├─ ⑤ 意图范围   计划是否超出原话授权（§7.7）
  ├─ ⑥ 金融预演   不可变状态上按步骤顺序执行 bankcore（§7.8）
  ├─ ⑦ 工作流     基于快照计数与 L3 活动日志（§7.9）
  ├─ ⑧ 决策       聚合 ①–⑦（§7.10）；需要时创建审批
  └─ ⑨ 审计       gateway.audit_events，追加写 + 哈希链（§7.13）
```

- ① 失败时不拉快照，直接 DENY 并写审计；其他步骤失败也继续跑完后续检查（③④⑤ 的 DENY 除外），以便确认面板与评委视图展示完整的 findings。
- `plan_id` 由 L2 在 ① 通过后分配（`pln_` + ULID；回放模式下确定性生成），L1 的 `client_ref` 只用于关联。L2 把规范化后的计划存入 `gateway.plans`；之后的确认与执行**只用 L2 存的这一份**，不接受调用方再传步骤。

### 7.3 身份与令牌

| 令牌 | 签发 | 持有方 | 内容 | 有效期 |
|---|---|---|---|---|
| 会话 JWT | L2 `/auth/login`、`/sessions` | 浏览器 httpOnly + SameSite=Strict cookie；BFF | EdDSA；`aud=bff`、`sub=customer_id`、`sid`、`scope=["app","approve"]`、`jti` | 2h |
| 委托令牌 | L2 `/sessions/{sid}/utterances` | BFF（httpOnly cookie `dlg`）→ L1 | EdDSA；`aud=gateway`、`sub`、`sid`、`utterance_id`、`scope=["assistant"]`、`jti` | 10 分钟 |
| CSRF 令牌 | BFF 登录时生成 | 浏览器（双提交：cookie + 请求头 `X-CSRF-Token`） | 随机 32 字节 | 同会话 |
| 调用方令牌 F / A / S | `make keys` | 各自容器的环境变量 | 随机 32 字节，常量时间比较 | 长期 |

- 私钥 `JWT_SIGNING_KEY` 只在 gateway；公钥 `JWT_PUBLIC_KEY` 注入 agent 与 frontend。
- 登录：`{customer_id, pin}`，PIN 只以 `argon2` 哈希存在 gateway 库（种子 §2.2）；agent 容器没有 PIN。
- BFF 对所有非 GET 路由校验 CSRF 双提交令牌，并要求 `Origin` 为本站、`Sec-Fetch-Site: same-origin`。
- L2 校验 `aud`：F + U 接口只接受 `aud=bff`；A + D 接口只接受 `aud=gateway`。`approve` scope 只存在于会话 JWT 中。

### 7.4 用户原话与助手消息

- 表 `gateway.utterances(id, session_id, customer_id, text, text_sha256, suspicious, received_at_wall)`，只有 BFF 能写入；写入时用 `injection_patterns.yaml` 打 `suspicious`。
- 表 `gateway.assistant_messages(id, session_id, text, sent_at_wall)`，由 BFF 在收到 L1 的 `clarify` / `final` 事件后写入。
- **原话授权窗口**（③ 校验，失败为 `UTTERANCE_INVALID`）：
  1. `utterance_ids` 必须包含本会话**最新**的一条原话；
  2. 所有被引用的原话都属于本会话、在 `utterance_window_s`（600 秒，真实时间）内，并且晚于本会话上一份已执行、已拒绝或已 DENY 的计划；
  3. 委托令牌中的 `utterance_id` 必须等于最新原话。
- **一个会话最多一个 PENDING 审批**：新审批创建时，同会话的旧 PENDING 审批变为 `VOIDED`（`APPROVAL_SUPERSEDED`）。
- 撤销词（`intent_lexicon.yaml` 中的「算了、取消、不要了、不转了」）出现在最新原话中且其后没有新的动作词 → 意图范围为空（§7.7）。
- `origin=app_form` 的计划没有原话；参数来源记为 `user`，意图范围检查跳过（用户亲手填写的单步表单即授权范围）。

### 7.5 读工具代理与读取账本

- L2 从令牌取 `customer_id` 注入请求，并校验参数中每个 ID 属于该客户；L3 `/internal/read/*` 也按 `customer_id` 过滤（双重保证）。
- A + D 调用的结果写入 `gateway.read_records(session_id, source_ref, tool, field_path, value_norm, trust, suspicious, created_at_wall)`；`source_ref = <tool>#<n>:<json_path>`，返回给 L1 供 `arg_sources` 引用。F + U 调用（App 页面）不写读取账本。
- 字段可信度（`policies/trust.yaml`）：

| trust | 字段 |
|---|---|
| `trusted` | 账户、余额、卡、产品、持仓、定期指令、商户账单金额；`provenance ∈ {seed, user_form, agent_user}` 的收款人 |
| `echo` | `provenance = agent_echo` 的收款人（§7.6） |
| `untrusted` | 交易备注、商户名、对方户名、对方账号、AA 参与人留言、外部入账（`code=7`）的金额，以及一切从图片抽取的内容 |

- `suspicious`：untrusted 字段命中 `injection_patterns.yaml`。

### 7.6 污点判定（Prompt Injection 的结构性防御）

**规范化**（`gateway/taint/normalize.py`，原话、读取记录、语料用同一个函数）：中文数字与全角数字 → 半角阿拉伯数字（`cn2an`）；账号、手机号 → 去掉空格、连字符后的纯数字；姓名 → 去空白、全角转半角；金额 → 分。

**不可信语料**：对每个待判定的值，L2 同时查：
1. 本会话 `read_records` 中 `trust=untrusted` 的记录；
2. L3 `POST /internal/taint/lookup {customer_id, values[]}`：该客户近 90 个模拟日的全部 untrusted 字段（`transactions.memo / merchant_name / counterparty_name / counterparty_account`、`split_items.message`）；
3. 本会话 `assistant_messages` 中、早于最新原话的文本。

这样即使 L1 从未经 L2 读过注入内容（例如在别的会话里看到、自己记住），也能判定出来。

**匹配方式**：账号与手机号做片段匹配：值中任意连续 ≥ 6 位数字出现在语料中即算命中；姓名精确匹配规范化结果；ID 类参数判定的是实体的**关键属性**（收款人的账号与户名；产品名；指令的对方），本计划 `$ref` 新建的实体取对应 `add_payee` 步骤的参数。

**标签**（对每个敏感参数值 v）：

| 标签 | 条件 |
|---|---|
| `trusted` | v 属于 `trust=trusted` 的实体（例如 `provenance=user_form` 的已存收款人），**此条优先** |
| `user` | v 出现在本计划引用的原话中，且不在不可信语料 1、2 中，也不在 3（早于原话的助手消息）中 |
| `echo` | v 出现在原话中，但同时出现在不可信语料或更早的助手消息中（用户在复述注入内容或助手的诱导）；或 v 属于 `agent_echo` 收款人 |
| `user_derived` | `arg_sources.kind=derived`，L2 重算一致，所有输入都是 `trusted` 或 `user`；只适用于金额与份额 |
| `tainted` | v 不在原话中，但出现在不可信语料中 |
| `unknown` | 以上都不是 |

- 实体的标签取各关键属性中**最差**的一级：`tainted` > `echo` > `unknown` > `user_derived` > `user` > `trusted`。
- **金额与份额**只能是 `user`、`user_derived`，或 `trusted` 且来源字段在白名单内（`upcoming_charges[].amount_cents`、`list_subscriptions[].amount_cents`、`get_holdings[].units`、`get_accounts[].available_cents`）；其他一律视为 `unknown`。外部入账金额不能作为金额来源。

**规则**（效果见 §7.15）：

| 代码 | 条件 | 效果 |
|---|---|---|
| `TAINTED_SENSITIVE_ARG` | `outflow=external` 的工具、`add_payee`、`schedule_transfer`、`subscribe_product` 的敏感参数为 `tainted` | DENY |
| `TAINTED_CONTROL_ARG` | `CARD_LOOSEN` 类步骤的参数为 `tainted` | DENY |
| `TAINTED_OTHER_ARG` | 其他写工具的敏感参数为 `tainted` | 升红 |
| `ECHO_NEW_PAYEE_TRANSFER` | 同一计划内 `add_payee` 的账号为 `echo`，且后续步骤向它转账 | DENY |
| `ECHO_SENSITIVE_ARG` | 其他情况下收款人属性为 `echo` | 升红；确认面板显示「该账号首次出现在：交易备注『…』」或「该账号来自小安的提问」 |
| `UNKNOWN_PAYEE_ARG` | `add_payee` 或对外转账的收款人属性为 `unknown` | DENY，提示「请在转账页手动输入收款信息」 |
| `UNKNOWN_AMOUNT` | 金额或份额为 `unknown` | REPLAN（ask_user） |
| `SUSPICIOUS_CONTEXT` | 计划中任一敏感值与 `suspicious` 字段有片段重叠；或本会话读取过 `suspicious` 字段 / 原话被标 `suspicious`，且计划含对外转出或 `CARD_LOOSEN` | 升红；确认面板展示可疑原文片段 |

- 收款人的 `provenance` 由 L2 在执行 `add_payee` 时决定并传给 L3：`origin=app_form` → `user_form`；`origin=agent` 且账号与户名都为 `user` → `agent_user`；为 `echo`（升红并经 OTP 确认后）→ `agent_echo`。向 `agent_echo` 收款人的每次转账都升红（`ECHO_PAYEE_TRANSFER`）。

`injection_patterns.yaml` 示例：`忽略(之前|以上|所有).{0,6}(指令|要求)`、`(转|汇)(到|给|至).{0,8}\d{12,19}`、`顺便(开启|打开|调高|解冻)`、`(系统|管理员|客服)(通知|要求)`、`核验.{0,6}(账户|余额)`。只用于升级，永不用于放行。

### 7.7 意图范围

`gateway/intent/extract.py` 用确定性规则从原话中提取：

```python
class IntentScope(BaseModel):
    actions: set[ActionKind]                    # TRANSFER、SELF_TRANSFER、ADD_PAYEE、SCHEDULE、SCHEDULE_CANCEL、
                                                # CARD_TIGHTEN、CARD_LOOSEN、CARD_FREEZE、CARD_LOST、SUB_CANCEL、
                                                # FUND_BUY、FUND_SELL、SPLIT
    amount_caps: dict[str, int]                 # 规范化人名 / 别名 / "self" → 该对象的金额上限（分）
    amount_total_cap: int | None                # 原话中所有明确金额之和
    open_amount: bool                           # 出现「剩下 / 省下 / 全部 / 一半」
    counterparties: set[str]                    # 规范化人名与别名
    periodic: bool                              # 出现「每月 / 每周 / 每个月 / 每星期」
    cancelled: bool                             # 最新原话中出现撤销词且其后没有新动作
```

- 词表在 `policies/intent_lexicon.yaml`：「转、汇、打给」→ TRANSFER；「转到我的储蓄 / 活期、存到」→ SELF_TRANSFER；「加 / 添加收款人」或原话中出现完整账号（≥ 12 位）且有 TRANSFER → ADD_PAYEE；「调低、降低、关闭、关掉」→ CARD_TIGHTEN；「调高、提高、开通、打开、解冻」→ CARD_LOOSEN；「冻结、锁卡」→ CARD_FREEZE；「挂失」→ CARD_LOST；「取消 / 退订 + 订阅 / 会员」→ SUB_CANCEL；「取消 + 定时」→ SCHEDULE_CANCEL；「定时、每月 / 每周 + 转」→ SCHEDULE；「买、申购」→ FUND_BUY；「卖、赎回」→ FUND_SELL；「AA、分摊、收款」→ SPLIT。
- 金额与对象的关联：每个金额归给它前面最近的人名（「给王五转 3000，再给赵六转 6000」→ `{王五: 300000, 赵六: 600000}`）；没有人名的金额归 `"self"` 或总额。
- 多条原话：`actions`、`counterparties` 取并集，`amount_caps` 按对象相加，`open_amount`、`periodic` 取或；`cancelled` 只看最新原话。
- 判定（第一个命中即停）：

| 代码 | 条件 | 效果 |
|---|---|---|
| `INTENT_SCOPE_EXCEEDED` | `cancelled`；或 `actions` 为空而计划有非保护性写步骤；或某步的 ActionKind 不在 `actions` 中（保护性的 CARD_TIGHTEN、CARD_FREEZE 除外）；或 `schedule_transfer.period ≠ ONCE` 但 `periodic=false` | REPLAN（ask_user） |
| `INTENT_COUNTERPARTY_MISMATCH` | 对外转账的收款人户名 / 别名不在 `counterparties` 中（原话中没有任何人名时，只允许本人互转）；或本计划新增的收款人，其户名与账号没有都出现在原话中 | REPLAN（ask_user） |
| `INTENT_AMOUNT_EXCEEDED` | 某对象的金额合计 > `amount_caps[对象]`；或全部金额合计 > `amount_total_cap`（`open_amount=true` 且金额为 `user_derived` 时不检查） | REPLAN（ask_user） |
| `INTENT_OPEN_AMOUNT` | `open_amount=true` | 至少为黄，确认面板高亮金额与算式 |
| `INTENT_PROTECTIVE_UNASKED` | 保护性步骤不在 `actions` 中 | 至少为黄 |

### 7.8 金融预演器（Financial Preflight Simulator）

**原则**（BL-1、BL-2、BL-3）：预演**永不写** L3；与执行**共用** `bankcore`；按步骤数组顺序**累积**执行；结果绑定 `state_version` 与 `sim_now`，执行时在新快照上重跑。

**`bankcore` 接口**（无 IO、无全局状态、无 float）

```python
@dataclass(frozen=True)
class StepContext:
    plan_id: str
    step_id: str
    now: datetime                                     # 模拟时钟

@dataclass(frozen=True)
class BankState:
    customer_id: str
    now: datetime
    accounts: Mapping[str, Account]                   # 本客户账户：TB-1 四个累计量 + flags + normal_side
    cards: Mapping[str, Card]                         # 状态、限额、开关、当日已用
    payees: Mapping[str, Payee]                       # 含 provenance、created_at_sim、outflow_count_24h、outflow_sum_24h_cents、first_outflow_at_sim
    instructions: Mapping[str, StandingInstruction]   # FN-1~4
    merchant_bills: Mapping[str, MerchantBill]        # 未来 shortfall_days 天内的商户账单
    products: Mapping[str, Product]
    holdings: Mapping[str, Holding]
    fund_orders: Mapping[str, FundOrder]
    risk_profile: RiskProfile | None                  # 含 level、assessed_at、previous_level
    counters: Counters                                # outflow_24h_cents（对外，滚动 24h）
    activity: tuple[ActivityEvent, ...]               # 近 7 日：payee_added、card_loosened、transfer_out、schedule_created、plan_executed(plan_hash)

@dataclass(frozen=True)
class StepOutcome:
    state: BankState
    effects: tuple[Effect, ...]        # TransferLeg、CardUpdate、PayeeCreate、InstructionUpdate、OrderCreate、ActivityEvent…
    violations: tuple[Violation, ...]  # 非空即该步失败
    outputs: Mapping[str, str]         # 供 $ref 引用：payee_id、instruction_id、order_id…

def apply_step(state: BankState, ctx: StepContext, tool: str, args: BaseModel) -> StepOutcome: ...
# 按 tool 分发到 apply_transfer / apply_add_payee / apply_schedule_transfer / apply_cancel_instruction /
# apply_split_request / apply_card_limits / apply_card_controls / apply_card_freeze / apply_card_lost /
# apply_risk_assessment / apply_subscribe / apply_redeem
def run_standing(state: BankState, until: datetime) -> tuple[BankState, list[StandingRun]]: ...
def classify(state: BankState, tool: str, args: BaseModel) -> ActionKind: ...   # 方向判定（§5.3）
```

- 状态字段用 `types.MappingProxyType` 包装；每步通过 `dataclasses.replace` 加浅拷贝字典生成新状态（数据规模小，开销可忽略）。
- `BankState` 只含本客户自己的行。对手方账户（本行收款人的活期）与内部账户（清算、托管）的效果只做累计量加法、不做余额校验，由 L3 在执行事务中加锁并写入。
- **快照**：L3 `POST /internal/snapshot {customer_id}` 返回整个 `BankState`（本期全量，超过 50ms 再按工具裁剪）与 `state_version`、`sim_now`。

**检查项**（`l3=是` 的代码在 L3 执行时由同一函数产生，BL-4）

| 代码 | 检查 | 效果 | l3 |
|---|---|---|---|
| `INSUFFICIENT_FUNDS` | TB-2：`debits_pending + debits_posted + amount ≤ credits_posted` | REPLAN ask_user | 是 |
| `SAME_ACCOUNT_TRANSFER` | 转出与转入为同一账户 | REPLAN ask_user | 是 |
| `CARD_SINGLE_LIMIT` / `CARD_DAILY_LIMIT` | 卡单笔 / 当日累计（本期只有演示注入的刷卡会触发） | REPLAN ask_user | 是 |
| `DAILY_OUTFLOW_LIMIT` | `counters.outflow_24h_cents + 本步对外金额 > outflow_24h_hard_cents` | DENY | 是 |
| `CARD_STATE_CONFLICT` | 对 FROZEN / LOST 卡做非解冻操作；对 LOST 卡解冻 | REPLAN ask_user | 是 |
| `INSTRUCTION_NOT_ACTIVE` | 取消的指令不存在或已 DELETED | REPLAN ask_user | 是 |
| `SUITABILITY_MISMATCH` | 产品风险等级 > 测评等级 | DENY | 是 |
| `ASSESSMENT_MISSING` | 无测评或超过 12 个月 | DENY | 是 |
| `ASSESSMENT_JUST_CHANGED` | 24h 内测评等级上调，且产品等级 > `previous_level` | DENY | 是 |
| `BELOW_MIN_AMOUNT` | 申购金额 < 起购金额 | REPLAN ask_user | 是 |
| `INSUFFICIENT_UNITS` | 赎回份额 > 可用份额（`units − units_frozen`） | REPLAN ask_user | 是 |
| `DEPENDENCY_UNMET` | 依赖步骤失败；或用到尚未到账的资金（赎回在途是 `credits_pending`，不计入可用余额） | REPLAN ask_user | 是 |
| `SCHEDULED_SHORTFALL` | 计划执行后用 `run_standing` 前推 `shortfall_days`，本客户有定期指令会因余额不足失败。DUES 金额取 `merchant_bills`，没有账单时取 `min(max_amount_cents, 最近 3 次实扣的最大值)`；不计任何他人入账（保守估计） | REPLAN ask_user | 否 |
| `IRREVERSIBLE_STEP` | 含挂失或申购 | 升红（标注） | 否 |

**输出**：`Decision.preflight = {state_version, sim_now, would_apply, steps:[{step_id, ok, violations, balances:[{account_id, available_before_cents, available_after_cents}], counters:{outflow_24h_after_cents, card_today_after_cents}}], future:{window_days, shortfalls:[{instruction_id, run_at, amount_cents}]}}`，以及 `replan_hints`（例如 `{"code":"INSUFFICIENT_FUNDS","step_id":"s2","max_amount_cents":500000}`）。

**一致性保证**：`tests/integration/test_preview_equals_execute.py` 用 hypothesis（`max_examples=200`）随机生成种子状态与计划，每个样例在测试库中开一个事务：断言「预演的最终状态与违规」=「L3 真实执行后的状态与失败码」，然后回滚。改 `bankcore` 或 L3 写路径时必须跑。

### 7.9 工作流风险监控（Workflow Risk Monitor）

**数据来源**：
- 计数类与序列类规则只读 L3 快照中的 `counters`、`payees` 统计与 `activity`（L3 在每个写事务内追加 `activity_log`，§8.3）。这样「执行了但 L2 没来得及记」的情况不存在，种子历史与演示注入也都算进去。
- L2 表 `gateway.workflow_events(customer_id, session_id, plan_id, kind ∈ {REJECTED, DENIED}, tool, counterparty_key, plan_hash, sim_at)` 只记录 L3 中不存在的事件，供 `REJECTED_RETRY` 使用。
- 所有窗口按模拟时间计算。
- 「转账类」匹配集合 = `{transfer(external), schedule_transfer(external)}`，加上 activity 中的 `transfer_out`（含定期指令执行）。`schedule_transfer` 首次执行在 30 个模拟分钟内的，按即时转账计入本次累计。

**规则**（`policies/workflow.yaml`）

| 代码 | 规则 | 效果 |
|---|---|---|
| `CUMULATIVE_OUTFLOW` | `counters.outflow_24h_cents + 本计划对外金额 > outflow_24h_red_cents` | 升红 |
| `SPLIT_TRANSFER` | 24h 内对同一收款人的对外转出（历史 + 本计划）≥ `split_count` 笔且合计 > `split_sum_cents` | 升红 |
| `NEW_PAYEE_FIRST_TRANSFER` | 收款人 `created_at_sim` 在 30 分钟内（含同一计划内先加后转） | 升红 |
| `RISKY_SEQUENCE` | 120 分钟内出现「新增收款人 P → CARD_LOOSEN → 向 P 转账」，三步可分布在不同计划、不同会话 | 升红 |
| `DUPLICATE_PLAN` | activity 中 10 分钟内有相同 `plan_hash` 的 `plan_executed` | 至少为黄，面板提示「疑似重复」 |
| `REJECTED_RETRY` | 10 分钟内有同一工具、同一对象（收款人 / 产品 / 卡）的 REJECTED 或 DENIED 计划，不论金额 | 升红 |
| `ECHO_PAYEE_TRANSFER` | 向 `provenance=agent_echo` 的收款人转账 | 升红 |

**序列匹配器**（`gateway/workflow/sequence.py`）：

```python
@dataclass(frozen=True)
class SeqPattern:
    code: str
    steps: tuple[StepMatcher, ...]                    # 每步：事件种类集合 + 参数谓词
    bind: tuple[tuple[int, str, int, str], ...]       # 跨步变量约束，如 (0, "payee_id", 2, "payee_id")
    window: timedelta

def match(events: Sequence[ActivityEvent], pattern: SeqPattern) -> list[Match]: ...
```

把「快照 activity + 本计划步骤（视为在 `sim_now` 按顺序发生）」合并成按时间排序的序列，做带变量绑定的子序列匹配（回溯即可，每客户每天几十条）。`RISKY_SEQUENCE` 写在 `workflow.yaml` 中。

### 7.10 策略决策与规则引擎

```python
class Rule(Protocol):
    code: str
    stage: Literal["contract", "entity", "taint", "intent", "preflight", "workflow", "base"]
    def evaluate(self, ctx: EvalContext) -> list[Finding]: ...

class Finding(BaseModel):
    code: str
    severity: Literal["info", "low", "high", "critical"]
    effect: Literal["deny", "replan", "raise_red", "raise_yellow", "none"]
    replan_mode: Literal["auto", "ask_user"] | None
    step_id: str | None
    message: str          # 中文模板，来自 contracts/codes.py，只填结构化数据，不含 LLM 文本
    evidence: dict        # 例如被污染的备注原文、命中的活动事件
```

- 规则注册在 `gateway/policy/registry.py`；阈值读 YAML；启动时计算 `policy_version`（YAML 内容哈希 + 日期），写入每条 Decision 与审计。
- 基础等级（stage=`base`）来自 `tools.yaml`：对外转出单笔 > `single_red_cents` 记 `BASE_RED_AMOUNT`（升红）；工具未登记 → `TOOL_NOT_REGISTERED`（DENY，PG-1）。
- **决策**：任一 `deny` → DENY；否则任一 `replan` → REPLAN（`replan_mode` 取各 finding 中最严格的：有 `ask_user` 即 `ask_user`）；否则风险等级 = max(各步基础等级, 所有 `raise_*`)，绿 → ALLOW，黄 / 红 → REQUIRE_APPROVAL。等级只升不降。
- **LLM 不参与授权（显式条款）**：L2 的任何规则不得读取计划中的 `intents`、`rationale`；`arg_sources` 只用于定位候选值，结论由 L2 独立重算。ActionKind、来源、等级全部由 L2 计算。测试 `test_llm_fields_ignored.py` 随机改写这三个字段，断言 Decision 不变（`arg_sources` 改写只允许把结论变差）。
- **评测 profile**：`GATEWAY_PROFILE=B0|B1|B2|B2-P|B2-W|B2-T|B2-I`（默认 B2），定义见 §10.1。非 B2 时必须 `EVAL_MODE=true`，且 `DEMO_MODE` 必须为 false，否则 gateway 拒绝启动。Decision 带 `profile` 字段；非 B2 时 App 顶部显示红色横幅。

Decision 示例：

```json
{
  "plan_id": "pln_01JB...",
  "plan_hash": "9f2c…",
  "decision": "REQUIRE_APPROVAL",
  "risk_level": "red",
  "profile": "B2",
  "policy_version": "2026-10-23.1",
  "approval": {"approval_id": "apv_01JB...", "expires_at": "2026-10-23T10:05:00+08:00", "requires_otp": true, "requires_disclosure": false},
  "preflight": {"state_version": 1287, "sim_now": "2026-10-15T09:20:00+08:00", "would_apply": true, "steps": [], "future": {"window_days": 7, "shortfalls": []}},
  "findings": [
    {"code": "BASE_RED_AMOUNT", "severity": "high", "effect": "raise_red", "replan_mode": null, "step_id": "s1",
     "message": "单笔转账 3,000.00 元超过 1,000.00 元，需要短信验证", "evidence": {"amount_cents": 300000}}
  ],
  "replan_hints": []
}
```

### 7.11 审批与原子执行

**审批记录**：`gateway.approvals(approval_id, plan_id, plan_hash, customer_id, session_id, risk_level, requires_otp, requires_disclosure, state_version, status, confirmed_by ∈ {user, policy}, otp_attempts, disclosure_viewed_at, created_at, expires_at, used_at)`；状态 `PENDING / CONFIRMED / REJECTED / USED / VOIDED / EXPIRED`。另存 `binding = HMAC-SHA256(APPROVAL_KEY, plan_hash ‖ customer_id ‖ session_id ‖ expires_at)`。

1. **ALLOW**：直接创建 `status=CONFIRMED, confirmed_by=policy` 的审批，Decision 返回其 `approval_id`，执行流程与其他计划相同。
2. **REQUIRE_APPROVAL**：创建 `PENDING` 审批（作废同会话旧 PENDING，§7.4）；`expires_at = wall_now + approval_ttl_s`。
3. **OTP**（红级）：`otp = HOTP(OTP_KEY, approval_id)` 取 6 位；写入 `/notifications`（模拟短信）与 `/demo/otp`；两者只对会话 cookie 持有者可见。错 3 次 → `VOIDED`（`OTP_LOCKED`）。
4. **风险揭示**（含 `subscribe_product`）：必须先 `GET /approvals/{id}/disclosure` 取得 `disclosure_token`（记录查看时间），confirm 时提交。
5. **确认**：`POST /approvals/{id}/confirm {plan_hash, otp, disclosure_token}`。校验：F + U、`sub` 与审批一致、`PENDING`、未过期、`plan_hash` 等于审批中的值、`binding` 重算一致、OTP 与揭示令牌（如需）→ `CONFIRMED`。任一失败返回对应代码（§7.15，stage=`approval`）。
6. **拒绝**：`REJECTED`，写 `workflow_events(kind=REJECTED)`。

**执行**（`/plans/{plan_id}/execute`）

1. 取客户级串行锁：`pg_advisory_xact_lock(hashtext(customer_id))`（gateway 库），覆盖以下全部步骤。
2. 复核审批：存在、`CONFIRMED`、未过期、未使用、`binding` 一致、凭证与 `origin` 匹配。
3. 拉新快照，用 L2 存的计划**重跑 ⑥⑦⑧**（不论 `state_version` 是否变化，因为模拟时钟推进也会改变结论）：
   - 决策仍可执行、风险等级不高于审批时、每步金额与对象不变 → 继续；
   - 否则审批置为 `VOIDED`，返回 `STATE_CHANGED`。
4. 原子地把审批置为 `USED`（`UPDATE … WHERE status='CONFIRMED'`，影响行数为 1 才继续）。
5. 调 L3 `POST /internal/plans/execute {customer_id, plan_id, plan_hash, approval_id, approval_risk_level, expected_state_version, steps, provenance_hints}`，`expected_state_version` 为第 3 步那份快照的版本。
6. L3 返回 `STATE_CHANGED`（第 3 步与第 5 步之间又有写入，例如 scheduler）→ 回到第 3 步重试 1 次；仍失败 → 返回 `STATE_CHANGED`。
7. 写审计；执行了 `schedule_transfer` 的，同时写 `standing_authorizations`（§7.12）。

**L3 原子执行**（TB-4、TB-5）

1. 查 `plan_executions(customer_id, plan_id)`：已有 `DONE` 或 `FAILED` → 直接返回存储的结果（幂等）。主键存在但 `customer_id` 不同 → 拒绝。
2. 开事务；按 ID 排序 `SELECT … FOR UPDATE` 锁定涉及的所有账户（含对手方与内部账户）、卡、指令行与本客户的 `state_versions` 行；校验版本 = `expected_state_version`，不等 → 回滚，返回 `STATE_CHANGED`。
3. 从锁定的行组装 `BankState`（在同一事务内重算 `counters`），按顺序调用同一批 `bankcore.apply_step`。
4. 任一步有违规 → 回滚；再用一个独立短事务写入 `plan_executions(status=FAILED, result={failed_step, code})`；其余步骤标 `LINKED_STEP_FAILED`。
5. 全部成功 → 持久化 `effects`（转账只追加）、追加 `activity_log`（含 `plan_executed(plan_hash)`）、对涉及的每个客户递增 `state_versions`、写 `plan_executions(status=DONE, result, approval_id, approval_risk_level)`，提交。

### 7.12 定期指令执行前授权

**授权记录**：`gateway.standing_authorizations(instruction_id PK, customer_id, kind, counterparty_key, amount_type, amount_cents, max_amount_cents, period, approved_risk_level, source ∈ {plan, seed}, plan_id, approval_id)`。
- `schedule_transfer` 执行成功后，L2 按自己存的计划参数写入（`source=plan`，`approved_risk_level` 为当时审批等级）。
- 种子代扣由 `services/gateway/seed.py` 写入（`source=seed`，`max_amount_cents` 为月费）。

**流程**：

1. L3 scheduler 到期时调 L2 `POST /internal/scheduled/authorize {instruction_id, run_at}`（凭证 S）。L2 不采信 L3 传来的金额，而是自己从快照读取该指令，DUES 类读取对应 `merchant_bills`。
2. 校验：指令属于授权记录中的客户；对象与授权记录一致；FIXED 金额相等，DUES 金额 ≤ `max_amount_cents`。不一致 → DENY `SCHEDULED_BYPASS`。
3. 把本次执行视为单步计划（`transfer` 或代扣），在当前快照上跑 ⑥⑦⑧：
   - 有 `deny` → DENY；
   - 有预演违规（如余额不足）→ 返回 `FAILED_INSUFFICIENT` 等对应代码；
   - 风险等级 > `approved_risk_level` → DENY `SCHEDULED_RISK_RAISED`（例如期间卡被挂失、24h 累计已被其他转账推高）；
   - 否则签发 `run_token = HMAC-SHA256(RUN_TOKEN_KEY, instruction_id ‖ run_at ‖ amount_cents ‖ counterparty_key ‖ state_version)`。
4. L3 用 `RUN_TOKEN_KEY` 重算校验，`run_tokens` 表保证一次性；在事务内执行；结果与拒绝都写通知。
5. 失败、拒绝都不重试；下一周期照常授权。

### 7.13 审计

- `gateway.audit_events(seq bigserial, at_wall, at_sim, session_id, plan_id, kind, payload jsonb, policy_version, profile, prev_hash, hash)`。
- `hash = sha256(prev_hash ‖ seq ‖ kind ‖ plan_id ‖ at_wall ‖ canonical(payload))`（BL-8）；写入时持有 `pg_advisory_xact_lock(hashtext('audit'))`，保证链不分叉。
- 记录：登录、新会话、原话登记、助手消息登记、read 代理、evaluate 输入与 Decision、审批创建 / 确认 / 拒绝 / 作废 / OTP 失败、执行请求与结果、定期授权。
- `gateway_app` 角色对该表只有 INSERT / SELECT；`make audit-verify` 重算整条链；`make reset` 用库 owner 角色 `TRUNCATE`。

### 7.14 L2 数据表（库 `gateway`）

`customers_auth(customer_id, pin_hash)`、`sessions(sid, customer_id, created_at)`、`utterances`、`assistant_messages`、`read_records`、`plans(plan_id, customer_id, session_id, origin, client_ref, plan_json, plan_hash, decision_json, created_at)`、`approvals`、`standing_authorizations`、`workflow_events`、`notifications(id, customer_id, kind, text, created_at, read_at)`、`audit_events`。

### 7.15 规则与错误代码总表

`packages/contracts/codes.py` 是唯一来源；此表与其一致。效果：`deny` / `replan` / `raise_red` / `raise_yellow` / `reject`（请求被拒，不产生 Decision）/ `label`（只是标签）。

| 代码 | 阶段 | 效果 | replan_mode | 定义处 |
|---|---|---|---|---|
| `CONTRACT_INVALID` | contract | deny | – | §6.2、§7.2 |
| `TOOL_NOT_REGISTERED` | contract | deny | – | §7.10 |
| `ORIGIN_NOT_ALLOWED` | contract | deny | – | §7.1、§5.5 |
| `DERIVATION_MISMATCH` | contract | replan | auto（→ plan） | §6.2 |
| `UTTERANCE_INVALID` | entity | deny | – | §7.4 |
| `ENTITY_NOT_OWNED` | entity | deny | – | §7.2 |
| `ENTITY_UNSEEN` | entity | replan | auto（→ gather） | §6.8 |
| `TAINTED_SENSITIVE_ARG` | taint | deny | – | §7.6 |
| `TAINTED_CONTROL_ARG` | taint | deny | – | §7.6 |
| `TAINTED_OTHER_ARG` | taint | raise_red | – | §7.6 |
| `ECHO_NEW_PAYEE_TRANSFER` | taint | deny | – | §7.6 |
| `ECHO_SENSITIVE_ARG` | taint | raise_red | – | §7.6 |
| `UNKNOWN_PAYEE_ARG` | taint | deny | – | §7.6 |
| `UNKNOWN_AMOUNT` | taint | replan | ask_user | §7.6 |
| `SUSPICIOUS_CONTEXT` | taint | raise_red | – | §7.6 |
| `INTENT_SCOPE_EXCEEDED` | intent | replan | ask_user | §7.7 |
| `INTENT_COUNTERPARTY_MISMATCH` | intent | replan | ask_user | §7.7 |
| `INTENT_AMOUNT_EXCEEDED` | intent | replan | ask_user | §7.7 |
| `INTENT_OPEN_AMOUNT` | intent | raise_yellow | – | §7.7 |
| `INTENT_PROTECTIVE_UNASKED` | intent | raise_yellow | – | §7.7 |
| `BASE_RED_AMOUNT` | base | raise_red | – | §7.10 |
| `INSUFFICIENT_FUNDS` | preflight（l3） | replan | ask_user | §7.8 |
| `SAME_ACCOUNT_TRANSFER` | preflight（l3） | replan | ask_user | §7.8 |
| `CARD_SINGLE_LIMIT`、`CARD_DAILY_LIMIT` | preflight（l3） | replan | ask_user | §7.8 |
| `DAILY_OUTFLOW_LIMIT` | preflight（l3） | deny | – | §7.8 |
| `CARD_STATE_CONFLICT` | preflight（l3） | replan | ask_user | §7.8 |
| `INSTRUCTION_NOT_ACTIVE` | preflight（l3） | replan | ask_user | §7.8 |
| `SUITABILITY_MISMATCH` | preflight（l3） | deny | – | §7.8 |
| `ASSESSMENT_MISSING` | preflight（l3） | deny | – | §7.8 |
| `ASSESSMENT_JUST_CHANGED` | preflight（l3） | deny | – | §7.8 |
| `BELOW_MIN_AMOUNT` | preflight（l3） | replan | ask_user | §7.8 |
| `INSUFFICIENT_UNITS` | preflight（l3） | replan | ask_user | §7.8 |
| `DEPENDENCY_UNMET` | preflight（l3） | replan | ask_user | §7.8 |
| `SCHEDULED_SHORTFALL` | preflight | replan | ask_user | §7.8 |
| `IRREVERSIBLE_STEP` | preflight | raise_red | – | §7.8 |
| `CUMULATIVE_OUTFLOW` | workflow | raise_red | – | §7.9 |
| `SPLIT_TRANSFER` | workflow | raise_red | – | §7.9 |
| `NEW_PAYEE_FIRST_TRANSFER` | workflow | raise_red | – | §7.9 |
| `RISKY_SEQUENCE` | workflow | raise_red | – | §7.9 |
| `DUPLICATE_PLAN` | workflow | raise_yellow | – | §7.9 |
| `REJECTED_RETRY` | workflow | raise_red | – | §7.9 |
| `ECHO_PAYEE_TRANSFER` | workflow | raise_red | – | §7.9 |
| `APPROVAL_NOT_FOUND`、`APPROVAL_EXPIRED`、`APPROVAL_NOT_CONFIRMED`、`APPROVAL_SUPERSEDED`、`PLAN_HASH_MISMATCH`、`OTP_INVALID`、`OTP_LOCKED`、`DISCLOSURE_NOT_VIEWED` | approval | reject | – | §7.11 |
| `STATE_CHANGED` | execute | reject（审批作废） | – | §7.11 |
| `LINKED_STEP_FAILED` | execute | label（附随步骤） | – | §7.11 |
| `SCHEDULED_BYPASS`、`SCHEDULED_RISK_RAISED`、`RUN_TOKEN_INVALID` | scheduled | deny | – | §7.12 |
| `MANDATE_NOT_ACTIVE`、`FAILED_INSUFFICIENT` | scheduled（L3 运行记录） | label | – | §5.4、§5.1 |
| `ANOMALY_AMOUNT_OUTLIER`、`ANOMALY_NIGHT_LARGE`、`ANOMALY_DUPLICATE`、`ANOMALY_NEW_MERCHANT_LARGE` | analytics | label（不参与决策） | – | §5.2 |
| `AUTH_INVALID`（401）、`FORBIDDEN`（403） | http | reject | – | §7.1、§7.3 |

### 7.16 L2 自身的安全约束

- L2 不调用 LLM 做授权判断；注入启发式只升级风险，永不放行（唯一的 LLM 调用是 Q6 启用后的图片抽取，其结果一律 untrusted）。
- 所有阈值与规则配置化，Decision 与审计记录 `policy_version` 与 `profile`。
- `/demo/*` 在 `DEMO_MODE=false` 时不注册路由；非 B2 profile 只在 `EVAL_MODE=true` 时可用（§7.10）。
- 所有对外 HTTP 调用（L3、DeepSeek）设 5 秒超时。
- 不为让测试通过而放宽规则；阈值调整写入 AGENTS.md 进度日志并说明原因。

---

## 8. L3 Banking Backend 设计

### 8.1 选型结论

**自研** FastAPI + PostgreSQL。账本按 TigerBeetle 的模型实现（§4.4.1），定期指令按 Fineract 的枚举实现（§4.4.3）。不 fork devbanksdk，不部署 TigerBeetle / Blnk / Fineract（理由见 §4.2）。

### 8.2 账本模型

**账户**：四个累计量（TB-1），全部为 `bigint` 分；`normal_side` 决定展示余额的方向。

| 账户类别（`accounts.code`） | 例子 | `normal_side` | `debits_must_not_exceed_credits` |
|---|---|---|---|
| 1001 客户活期 | `acc_zs_chk` | C | true |
| 1002 客户储蓄 | `acc_zs_sav` | C | true |
| 9001 他行清算（应付他行） | `acc_ext_clearing` | C | false |
| 9002 商户清算（应付商户） | `acc_merchant_clearing` | C | false |
| 9003 基金托管 | `acc_fund_custody` | C | false |
| 9009 期初资金来源 | `acc_opening_source` | D | false |

- 贷方正常账户：账面余额 = `credits_posted − debits_posted`；可用余额 = `credits_posted − debits_posted − debits_pending`。借方正常账户相反。
- 他行转出与外部入账都经 `acc_ext_clearing`：转出时贷它（应付他行增加），入账时借它。演示中它的余额可以为负，这是模拟清算的简化，在技术文档中说明。

**转账**（`transfers`，只追加，TB-6）

| 字段 | 说明 |
|---|---|
| `id uuid` | 幂等键（TB-5）：`bankcore.ids.transfer_id(plan_id, step_id, leg)`；定期执行为 `(instruction_id, run_at, leg)` |
| `debit_account_id`、`credit_account_id` | |
| `amount_cents bigint` | > 0 |
| `ledger` | `CNY` |
| `code smallint` | `1` 本行转账、`2` 他行转账、`3` 刷卡消费、`4` 代扣、`5` 基金申购、`6` 基金赎回、`7` 外部入账、`8` 冲正、`9` 期初 |
| `flags` | `PENDING` / `POST_PENDING` / `VOID_PENDING` / 无（直接过账） |
| `pending_id` | post / void 时指向原 pending；部分唯一索引 `UNIQUE(pending_id) WHERE pending_id IS NOT NULL` 保证只处理一次（TB-3） |
| `expires_at_sim` | 仅 pending：模拟时间的过期时刻 |
| `plan_id`、`step_id`、`instruction_run_id` | 来源追溯 |
| `debit_available_after_cents`、`credit_available_after_cents` | 本笔之后两侧账户按 `normal_side` 计算的**可用余额**（TB-9） |
| `sim_at` | 模拟时间 |

**记账算法**（`bankcore.ledger`）
- 直接过账：借方 `debits_posted += a`，贷方 `credits_posted += a`。
- pending：借方 `debits_pending += p`，贷方 `credits_pending += p`。
- post（金额 a ≤ p）：借方 `debits_pending −= p, debits_posted += a`；贷方 `credits_pending −= p, credits_posted += a`。
- void：借方 `debits_pending −= p`；贷方 `credits_pending −= p`。
- 全部累计量 ≥ 0（`CHECK`）；带标志的账户另有 `CHECK (NOT debits_must_not_exceed_credits OR debits_pending + debits_posted <= credits_posted)`。

**业务流水**（`transactions`）：App 与账单分析读这张表，一笔流水对应 1–2 笔转账。字段：`id, customer_id, account_id, transfer_id, pending_transfer_id, direction(IN/OUT), amount_cents, status(PENDING/POSTED/VOIDED/FAILED), category, mcc, merchant_id, merchant_name, counterparty_name, counterparty_account, memo, card_id, sim_at`。`merchant_name`、`counterparty_name`、`counterparty_account`、`memo` 是 untrusted 字段。`transactions.status` 可更新（它不是账本）；账本只看 `transfers`。

**各业务的记账方式**

| 业务 | 记账 |
|---|---|
| 本行转账 | 借付款人活期、贷收款人活期（直接过账）；付款人与收款人的 `state_versions` 都 +1 |
| 本人互转 | 借转出账户、贷转入账户 |
| 他行转账 | 借付款人活期、贷 `acc_ext_clearing`；对方户名与账号记在流水上 |
| 外部入账（演示注入） | 借 `acc_ext_clearing`、贷客户活期；备注由注入模板给出 |
| 刷卡消费（演示注入） | 借活期、贷商户清算（直接过账，不做预授权）；校验卡状态、单笔与当日限额 |
| 代扣 | 借活期、贷商户清算；必须有 ACTIVE 指令并经 L2 授权 |
| 基金申购 | T：pending（借活期、贷托管），`expires_at_sim = add_trading_days(T, confirm_lag + 1) 15:00`；确认日 `add_trading_days(T, confirm_lag) 15:00`：post 全额，按当日净值生成份额；R1（`confirm_lag=0`）在下单时直接过账并生成份额 |
| 基金赎回 | 下单：`holdings.units_frozen += units`；确认日：按当日净值算金额，创建 pending（借托管、贷活期），`units −= units, units_frozen −= units`；到账日 `add_trading_days(T, settle_lag) 15:00`：post。赎回在途体现为活期的 `credits_pending`，不计入可用余额 |
| 冲正 | 反向转账 `code=8`；若会触发余额边界则冲正失败，记 `FAILED`，不强制执行 |

**份额与净值**（`bankcore.funds`，`decimal.localcontext(prec=28)`）
- 申购份额 `units = (Decimal(amount_cents) / 100 / nav).quantize(Decimal("0.0001"), ROUND_DOWN)`；不收手续费。
- 赎回金额 `amount_cents = int((units * nav * 100).quantize(Decimal("1"), ROUND_DOWN))`。
- 预演时确认日净值未知：份额按最新净值估算，只用于展示，不参与校验。

### 8.3 数据表（库 `bank`）

| 表 | 关键字段 | 约束 |
|---|---|---|
| `customers` | id, name, phone_masked | |
| `accounts` | id, customer_id(内部账户为空), code, ledger, normal_side, debits_must_not_exceed_credits, debits_pending, debits_posted, credits_pending, credits_posted, opened_at | §8.2 的 `CHECK` |
| `transfers` | 见 §8.2 | 主键 `id`；触发器禁止 UPDATE / DELETE；`UNIQUE(pending_id) WHERE pending_id IS NOT NULL` |
| `transactions` | 见 §8.2 | |
| `payees` | id, customer_id, name, alias, phone, bank_name, account_no, is_internal, internal_account_id, provenance(seed/user_form/agent_user/agent_echo), created_at_sim | `UNIQUE(customer_id, account_no)` |
| `cards` | id, account_id, last4, status(ACTIVE/FROZEN/LOST), single_limit_cents, daily_limit_cents, overseas, online, atm, contactless, replaced_by | |
| `merchants` | id, name, mcc | |
| `merchant_bills` | id, merchant_id, customer_id, due_at_sim, amount_cents, status(PENDING/PAID/DENIED) | |
| `standing_instructions` | id, customer_id, kind(SCHEDULED_TRANSFER/MERCHANT_MANDATE), amount_type(FIXED/DUES), amount_cents, max_amount_cents, from_account_id, payee_id, to_account_id, merchant_id, recurrence(PERIODIC/AS_PER_DUES), period(ONCE/WEEKLY/MONTHLY), day_of_month, next_run_at_sim, priority(URGENT/HIGH/MEDIUM/LOW), status(ACTIVE/DISABLED/DELETED), created_at_sim | FN-1~4 |
| `instruction_runs` | id, instruction_id, run_at_sim, status(DONE/FAILED_INSUFFICIENT/DENIED), transfer_id, reason_code | `UNIQUE(instruction_id, run_at_sim)` |
| `split_requests` / `split_items` | 总额、发起人、参与人、每人金额、留言、状态（PENDING/PAID） | |
| `products` | id, name, risk_level(1–5), nav, nav_date, confirm_lag_days, settle_lag_days, min_amount_cents, risk_disclosure | |
| `nav_history` | product_id, date, nav | |
| `holdings` | customer_id, product_id, units, units_frozen, cost_cents | `CHECK (units >= units_frozen AND units_frozen >= 0)` |
| `fund_orders` | id, customer_id, product_id, type(SUB/RED), status(PENDING/CONFIRMED/SETTLED/VOIDED), amount_cents, units, pending_transfer_id, order_at_sim, confirm_at_sim, settle_at_sim | |
| `risk_assessments` | id, customer_id, answers, score, level, created_at_sim | 有效期 12 个月 |
| `activity_log` | id, customer_id, kind(payee_added/card_loosened/card_tightened/transfer_out/schedule_created/plan_executed), ref_id, counterparty_key, amount_cents, plan_hash, sim_at | 只追加；与业务写入同一事务 |
| `plan_executions` | (customer_id, plan_id) PK, plan_hash, approval_id, approval_risk_level, status(DONE/FAILED), result, created_at | 幂等（TB-5） |
| `run_tokens` | token PK, instruction_id, run_at_sim, used_at | §7.12 |
| `state_versions` | customer_id PK, version | 写事务对涉及的每个客户 +1 |
| `sim_clock` | 单行：`now` | 只有 `advance` 能改 |

### 8.4 一致性要求与测试

- 每个写操作（一份计划、一次定期执行、一次演示注入）一个数据库事务；按 ID 排序 `SELECT … FOR UPDATE`（内部账户也参与排序），避免死锁（TB-10）。
- 余额只能通过插入 `transfers` 并在同一事务内更新四个累计量改变；`CHECK` 兜底。
- 测试：
  - `test_concurrency.py`：20 个并发计划作用于同一账户（经 L2 执行，含 L2 的串行锁与重试）。断言：最终余额 = 初始余额 − 成功笔数 × 金额，不为负；成功数 + `STATE_CHANGED` 数 + `INSUFFICIENT_FUNDS` 数 = 20；`state_versions` 单调。另有一组绕过 L2、直接并发调用 L3 的测试，断言余额不为负、版本冲突被拒。
  - `test_ledger_invariants.py`：任意操作序列后，Σdebits_posted = Σcredits_posted，Σdebits_pending = Σcredits_pending（TB-1）。
  - `test_idempotency.py`：同一 `(customer_id, plan_id)` 执行 3 次只记账一次，返回相同结果；失败结果同样幂等。
  - `test_two_phase.py`：申购 → 确认 / 过期作废；赎回 → 确认 → 到账；周五下单跨周末。

### 8.5 接口（只有 `/internal/*`，只接受 `X-Caller-Token: GATEWAY_SERVICE_TOKEN`）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/internal/snapshot` | `{customer_id}` → `BankState` JSON + `state_version` + `sim_now` |
| POST | `/internal/read/{name}` | 16 个 read 工具的实现；必须带 `customer_id`，按它过滤 |
| POST | `/internal/taint/lookup` | `{customer_id, values[]}` → 每个值在该客户近 90 日 untrusted 字段中的命中（字段、流水 ID、原文片段） |
| POST | `/internal/plans/execute` | §7.11 原子执行 |
| POST | `/internal/instructions/{id}/run` | 带 `run_token` 执行一次定期指令（scheduler 内部调用同一个函数） |
| GET | `/internal/clock` | 当前模拟时间 |
| POST | `/internal/demo/clock/advance` | 仅 `DEMO_MODE` / `EVAL_MODE`：推进到某时刻 |
| POST | `/internal/demo/inject` | 仅 `DEMO_MODE` / `EVAL_MODE`：注入外部入账、刷卡、商户账单 |
| POST | `/internal/demo/reset` | 仅 `DEMO_MODE` / `EVAL_MODE`：重置种子数据与时钟 |

scheduler 回调 L2 时用 `X-Caller-Token: SCHEDULER_CALLBACK_TOKEN`。

### 8.6 模拟时钟与 scheduler

- `advance(to)`：取出 `(now, to]` 内所有到期事件，按 `bankcore.standing.order_key(run_at, priority, id)` 排序（同一时刻：基金确认 → 赎回到账 → pending 过期 → 定时转账 → 代扣；同类再按 FN-4 优先级与 id），逐个处理：
  1. 在一个短事务中把 `sim_clock.now` 推进到该事件时间并**提交**；
  2. 定时转账 / 商户账单：在事务**之外**调 L2 授权（5 秒超时）；拿到 `run_token` 后开新事务加锁执行，或记录失败 / 拒绝并写通知；
  3. 基金确认、到账、pending 过期：开事务按 §8.2 处理。
- 后一个事件看到前一个的结果；scheduler 只在 `advance` 时运行，不在后台自走，保证演示与评测可复现。
- L2 与 L3 都是 async 实现，不在事件循环中做阻塞 IO。

---

## 9. 前端：银行 App

### 9.1 形态

- **移动端优先的 Web App**（Next.js），按手机银行的信息架构组织：底部标签栏 + 页面 + 底部弹出面板。
- 手机浏览器打开即是全屏 App；桌面浏览器（演示、答辩）显示三栏演示布局：中间 375×812 手机框（App 本体）、左侧演示控制台（仅 `DEMO_MODE`）、右侧评委视图。
- 小安是 App 的一部分，不是首页。客户可以完全不用小安完成单步业务；多步、跨场景业务由小安代办。

### 9.2 信息架构

底部标签栏：**首页 · 账单 · 小安 · 财富 · 我的**（小安居中，为凸起的圆形按钮）。

| 页面 | 路由 | 内容 | 读取（BFF → L2 read） | 写操作 |
|---|---|---|---|---|
| 登录 | `/login` | 选择演示用户 + 4 位 PIN 键盘 | – | – |
| 首页 | `/` | 账户卡片（总资产、活期、储蓄，眼睛图标隐藏金额）；快捷入口宫格（转账、AA 收款、卡片、订阅、理财、定时转账）；待办（即将扣费、待确认审批）；最近 5 笔交易；安全提示条 | `get_accounts`、`upcoming_charges`、`get_transactions`、`/approvals?status=PENDING` | 宫格跳转 |
| 转账 | `/transfer` | 选收款人（常用 + 搜索）或本人账户 → 金额（数字键盘）→ 备注 → 下一步 | `list_payees`、`get_accounts` | `transfer`（app_form） |
| 收款人管理 | `/payees` | 列表（显示来源标记）、新增 | `list_payees` | `add_payee`（app_form） |
| 定时转账 | `/scheduled` | 列表、新建、取消 | `list_scheduled` | `schedule_transfer` / `cancel_scheduled`（app_form） |
| AA 收款 | `/split` | 新建、进度 | `get_split_status` | `create_split_request`（app_form） |
| 账单 | `/bills` | 月份切换；类别环形图 + 环比；异常交易卡片；流水列表与筛选 | `get_spending_summary`、`detect_anomalies`、`get_transactions` | 「问小安：分析这个月」 |
| 卡片 | `/cards` | 卡面（尾号、状态）；限额滑杆；渠道开关；冻结 / 解冻；挂失 | `get_cards` | 卡片类写操作（app_form） |
| 订阅 | `/subscriptions` | 订阅列表（下次扣费、年化花费、「闲置」标签）；取消 | `list_subscriptions`、`detect_subscriptions` | `cancel_subscription`（app_form） |
| 财富 | `/wealth` | 风险等级徽章或「去测评」；持仓与在途订单；产品列表；产品详情（净值曲线、规则、风险揭示）；比较 | `get_risk_profile`、`get_holdings`、`list_products`、`compare_products` | `subscribe_product`、`redeem_product` |
| 风险测评 | `/wealth/assessment` | 6 道题 | `get_risk_questionnaire` | `submit_risk_assessment`（只能在此提交） |
| 我的 | `/me` | 客户信息（脱敏）；收款人；定时转账；**安全中心**（近期审批与拦截、每份计划的审计链）；消息中心（含模拟短信）；新建助手会话；退出 | `/audit/*`、`/notifications` | – |
| 小安 | 全屏面板 | 见 §9.3 | L1 SSE | 计划 → 确认面板 |

### 9.3 小安助手

- 打开方式：点底部「小安」；或在页面上点上下文入口（订阅页「帮我找出没用的订阅」、账单页「分析这个月」）。上下文入口只是把一句**预设文字**作为用户原话发送（`page_context` 仅用于建议短语，不进入原话）。
- 布局：全屏面板，从底部滑出；上方是对话区，下方是输入框与随页面变化的建议短语。状态行显示 `status` 事件（「正在查询账户…」）。
- 回复以**原生卡片**呈现。卡片类型由 L1 的 `final.cards` 给出，在 `packages/contracts/ui_cards.py`（Pydantic）与 `frontend/lib/cards.ts`（Zod）中**同一份 Schema**严格校验，未知类型丢弃：

| 卡片 | 内容 | 允许的动作 |
|---|---|---|
| `AccountSummaryCard` | 余额、可用额度 | 跳转 `/` |
| `SpendingReportCard` | 月度汇总、类别占比小图、异常条目 | 跳转 `/bills?month=YYYY-MM` |
| `AnomalyListCard` | 异常交易列表 | 发送预设文字「冻结尾号 XXXX 的卡」 |
| `SubscriptionListCard` | 订阅、闲置标记、年化节省 | 发送预设文字「取消这些闲置订阅」 |
| `ProductCompareCard` | 产品对比表（客观数据） | 跳转 `/wealth/products/{id}` |
| `RiskAssessmentCard` | 测评说明 | 跳转 `/wealth/assessment` |
| `PlanCard` | 计划步骤摘要与风险色 | 打开确认面板（以 `approval_id`） |
| `BlockedCard` | DENY / REPLAN 原因（模板文案 + 规则代码 + 证据） | 跳转安全中心 |
| `ResultCard` | 执行结果（来自 L3） | 跳转对应页面 |

- **卡片动作只有三种**：跳转到白名单内的前端路由（参数逐个校验格式）、发送一条预设文字（作为新原话，`origin=agent`）、以 `approval_id` 打开确认面板。卡片**不能**构造 app_form 计划，也不能预填任何写操作表单。
- 所有卡片与消息按纯文本渲染，禁止 `dangerouslySetInnerHTML`，链接只能是站内路由。
- 卡片中的数字由 L1 代码从 `reads` / `execution` 复制，并经 §6.8 校验。

### 9.4 统一确认面板（写操作的唯一出口）

无论计划来自小安还是 App 表单，都弹出同一个底部面板。面板以 `approval_id` 为唯一键，内容**只取自** L2 `GET /approvals/{approval_id}`：

1. 标题与风险色条：黄「请确认」、红「高风险操作，需短信验证」。绿级不弹面板，直接执行并以 toast 告知。
2. 步骤列表：每步的动作、对象、金额（大号数字）。收款人显示户名、银行、账号前 6 位与后 4 位；新增或来源为 `agent_echo` 的收款人带醒目的「新收款人」或「来源存疑」标记；与已存同名收款人账号不同时提示。
3. **预演对比**：「执行前 → 执行后」两列：活期可用余额、24h 对外转出、卡当日已用、持仓；未来 7 天扣费是否足额。
4. 风险提示：每条 finding 的模板文案（不含任何 LLM 文本），例如「这位收款人 10 分钟前才添加」「本次操作前读取过可疑内容：『……』」。
5. 申购类：「查看风险揭示」按钮（调 `/disclosure`），看过后才能勾选「我已阅读并理解」。
6. 红级：6 位验证码输入（分格输入 + 数字键盘）；模拟短信以 App 顶部通知横幅出现。
7. 底部：「确认」/「取消」；审批倒计时（5 分钟）。确认请求带上面板加载时取得的 `plan_hash`。
8. 确认后显示处理动画，结果以结果页呈现（成功的对勾页或失败原因页）。

DENY 时不弹确认面板，显示「安全拦截」页：盾牌图标、原因（模板文案）、规则代码、证据。

### 9.5 BFF（`frontend/app/api/`）

所有路由都要求会话 cookie；非 GET 路由校验 CSRF 双提交与 `Origin`。浏览器只与 Next.js 同源通信，看不到 L1、L2 的地址与任何令牌。

| 路由 | 转发到 | 说明 |
|---|---|---|
| `POST /api/login` | L2 `/auth/login` | 写会话 cookie 与 CSRF cookie（此路由只校验 `Origin`） |
| `POST /api/logout` | – | 清 cookie |
| `POST /api/read/[name]` | L2 `/tools/read/{name}` | App 页面读取 |
| `POST /api/assistant/new-session` | L2 `/sessions`，L1 无需调用 | 更新会话 cookie |
| `POST /api/assistant/message` | ① 若有挂起审批：L2 reject + L1 resume `"abandoned"`；② L2 `/sessions/{sid}/utterances`；③ L1 `/v1/sessions/{sid}/turns`（SSE 透传） | 写 `dlg` cookie；透传时把 `clarify` / `final` 文字登记到 L2 `assistant_messages` |
| `POST /api/assistant/resume` | L1 `/v1/sessions/{sid}/resume` | SSE 透传 |
| `GET /api/assistant/state` | L1 `/v1/sessions/{sid}/state` | 刷新后恢复 |
| `POST /api/plans` | L2 `/plans/evaluate` | App 表单写操作（单步） |
| `POST /api/plans/[id]/execute` | L2 `/plans/{id}/execute` | 仅 app_form 计划 |
| `GET /api/approvals`、`GET /api/approvals/[id]`、`GET /api/approvals/[id]/disclosure` | L2 | |
| `POST /api/approvals/[id]/confirm`、`/reject` | L2 | |
| `GET /api/notifications`、`/api/audit/*` | L2 | |
| `POST /api/demo/*`、`GET /api/demo/otp` | L2 `/demo/*` | 仅 `DEMO_MODE` |

### 9.6 视觉与组件

- 设计令牌（`frontend/styles/tokens.css`）：主色（深蓝）、风险色（绿 / 琥珀 / 红）、圆角、阴影、间距。本期只做浅色主题。
- 金额：等宽数字字体，千分位，`¥` 前缀，两位小数；支出不用红色，避免与风险色混淆。
- 卡号、手机号、账号一律脱敏（确认面板中的收款账号按 §9.4 显示前 6 后 4）。
- 组件自写，放在 `frontend/components/`：`AmountText`、`AccountCard`、`ActionGrid`、`TxnRow`、`BottomSheet`、`ConfirmSheet`、`OtpInput`、`NumericKeypad`、`RiskBadge`、`PreflightDiff`、`ShieldBlockedPage`、`TabBar`、`AssistantPanel`、`PhoneFrame`、`DemoConsole`、`JudgePanel`，以及 §9.3 的卡片组件。
- 图表用 Recharts（环形图、折线、柱状）。
- 可访问性：所有交互元素有可读标签；颜色之外另有文字或图标表达风险。

### 9.7 演示控制台与评委视图

- **演示控制台**（左栏，或独立页 `/demo`）：当前模拟时间与「推进到下个扣费日 / +1 天 / +1 小时」；注入外部入账（含注入备注模板）、刷卡、商户账单；一键加载攻击场景（`/demo/scenarios/{id}/load`，由 L2 组合多次 `/internal/demo/inject`）；查看模拟短信 OTP；重置数据。
- **评委视图**（右栏）：当前会话的时间线（原话 → read → 计划 → Decision → 审批 → 执行）；Decision 的 JSON 与模板文案两种视图；预演前后对比表；命中的规则及其证据；审计链校验状态。计划中 L1 生成的 `rationale` 单独显示，标注「模型生成，不参与决策」。
- **指标页** `/metrics`（桌面）：读取最新评测报告的 `metrics.json`。

<!-- PRD2-PART6 -->
