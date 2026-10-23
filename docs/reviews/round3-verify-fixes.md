# 第三轮修订复核：逐条核对 D30–D46 是否修复第三轮发现

- 复核者：Claude 子 agent（round3-verify-fixes），2026-10-23。
- 对象：`PRD-v2.1-wip.md`（本次读取 1,756 行，全文通读）。依据：`docs/reviews/round3-closure.md`（任务 A 中非「已修复」的 11 条 + N1–N20）、`round3-security.md`（R3-01–R3-16）、`round3-feasibility.md`（P0-1–3、P1-1–9、P2-1–9）、`round3-contracts-facts.md`（A-01–A-12、B-01–B-07），共 87 条。
- 方式：只读静态核对，只写本报告。没有运行代码或测试（仓库无代码）。章节号指当前 v2.1。
- 判定口径：已修复 = 原问题在 PRD 中有落地文字且无残留；部分修复 = 主体已改、仍有原报告指出的缺口；修复引入新问题 = 原问题已改，但修改文字本身造成新的矛盾或缺口。

---

## 1. 总表

### 1.1 round3-closure（任务 A 残留 11 条 + N1–N20）

| 编号 | 判定 | 证据位置（当前 PRD 原文片段） |
|---|---|---|
| S2-02 澄清路由 / 合槽 ID | 已修复 | §6.1「经 G21 读取本会话 OPEN 的 clarification」「存在 OPEN clarification 且其 parent 属于本轮原话 ──▶ [clarify]」；§7.4「G3 返回的就是它，F3 用它的 id 调 L1，旧 parent 不再单独传入」；G3「合槽时返回的 utterance 即合槽后的新引用」 |
| S2-03 授权证据 | 已修复 | §10.2「判别联合…INTERACTIVE…confirmed_view_hash?, final_view_hash…SCHEDULED_GRANT…SYSTEM_ORDER…MERCHANT_MANDATE」「与 plan_hash 一起写入 prepare，execute 时必须逐字节相同」；§8.5 prepare / execute / events execute 均带 `authorization_evidence` |
| C01 自修不能补读 | 已修复 | §6.1.1「REPLAN 且全部 finding 的 resolver=agent…→ gather（只允许调用 findings 的 evidence_refs 指明的读工具，至多 3 次调用）→ plan」；§6.1.1 注「这类 finding 必须在 evidence_refs 中给出建议的读工具名」 |
| C02 澄清闭环 / G19 | 已修复 | G19「kind=CLARIFICATION：action_kind 必须为 null，fields 只能是 [{field,entity_id}] 且 entity_id 属于当前 clarification 的 candidates」 |
| C03 add_payee no-op 引用 | 已修复 | §6.2「解析时点是 ⑤ 快照之后…s1 标 no_op=true…解析表写入 plans.resolved_steps 并参与 steps_hash；L3…不一致返回 STATE_CHANGED」 |
| C04 D6 原话与登录 | 已修复 | §9.7 D6a「按 case 文件登记 typed 原话」；§11.1 DEMO_LOGIN_PINS「可信评测 runner（仅 EVAL_MODE…）」（DEMO_MODE 下的缺口见 A-05） |
| C05 A4 授权证据 | 已修复 | 同 S2-03；§10.2「A4 判定为成功的条件：confirmed_view_hash ≠ final_view_hash…」 |
| C07 返回模型漏字段 | 已修复 | R3「totals_scope:"filtered_all"」；W2「{payee_id,target_fingerprint,no_op:bool}」；R10「normalization_basis:{…}」 |
| C08 周订阅折算 | 已修复 | §5.4「MONTHLY 年费 = 金额 × 12…WEEKLY 年费 = 金额 × 52，月费 = floor(年费 / 12)…含 WEEKLY 时返回 REPLAN INTENT_SCOPE_EXCEEDED」 |
| 一致性 C2 grant 缺失 | 已修复 | §7.11「grant 缺失与无效的区分…返回 DEPENDENCY_UNAVAILABLE，scheduler 把 job 保持 RETRYING…gateway 启动顺序：先消费 outbox 补建 grant」 |
| 一致性 C6 授权证据 | 已修复 | 同 S2-03 |
| N1 STATE_CHANGED 重 evaluate | 已修复 | §6.1.1「按 details.decision 路由…不重新调用 evaluate；本轮最多 1 次」；§7.10 第 4 项「L2 内部自动复演，最多 2 次」 |
| N2 HTTP 错误 / UNKNOWN 路由 | 已修复 | §6.1.1 路由表含 422 / 409 / 429 / 503 / 202 UNKNOWN，「L1 不轮询，由 App 经 F8 / G5 轮询」 |
| N3 等价确认与不可变绑定 | 已修复 | §7.10 第 5 项「审批行不改…校验 approval.binding_mac 对应原 evaluation…另带 approved_evaluation_id」 |
| N4 源账户范围 | 已修复 | §7.6「原话未指定时，scope.from_account_id = 客户唯一的 CHECKING 账户…不同 → INTENT_SCOPE_EXCEEDED」 |
| N5 扣费时刻 | 已修复 | §2.2「所有商户扣费与种子定时转账的执行时刻都是当日 06:00」；§8.3 merchant_billing「time_of_day（种子 06:00）」 |
| N6 cancel_scheduled 等级 | 已修复 | §5.1 T-3「创建黄 / 取消绿（保护性）」 |
| N7 回放正则与前缀 | **部分修复** | §6.6 正则 `\b[a-z]{2,4}_[0-9A-HJKMNP-TV-Z]{26}\b`「与 UUID」；§9.1 前缀已加 msg/clr/asm/adv/job；但见 §2 详述 |
| N8 S 回调未登记 | 已修复 | §7.1「以 §9.5、§9.5a、§9.6、§9.7 为唯一登记表」；§9.5a S1 |
| N9 哨兵与 P0 通过线 | 已修复 | §6.6「string? → 哨兵 "__NULL__"；bool? → KEEP/ON/OFF；…{kind:"id"|"ref"|"none"}」；P0 实验表 E1–E6 含通过标准 |
| N10 X-3 模板与预留 | 已修复 | R11「scheduled_transfers_total_cents,reserve_total_cents,by_account:[{…max_outflow_cents}]」；§7.5「available_less_7d_charges = 源账户 max_outflow(acct)」 |
| N11 transactions phase/status | 已修复 | §8.3 transactions「phase（关联账本阶段…）、status POSTED/PENDING/SETTLED/RELEASED」 |
| N12 字段名不一致 | 已修复 | AccountView「current_card_id?」；CardView「replacement_id?（由 cards.replaces_id 反查…）」；W1「to_available_after_cents 只在目标为本人账户时填写」 |
| N13 FROZEN 放宽规则码 | 已修复 | §7.7 CARD_STATE_CONFLICT「FROZEN 卡放宽限额或开启渠道；LOST 卡任何修改或解冻」 |
| N14 G18 用途 | **部分修复** | §7.3「G18 只用于原话过期后让 L1 读取线程 state…不能提交或执行计划」；但 F4 仍写「必要时 G18 换助手令牌再传 Command」，见 §2 |
| N15 状态枚举 / scope_id | 已修复 | §7.2「plans.status ∈ {…}」「approvals.status ∈ {…}」「scope_id = 授权来源 ID…」（取消词后的计划状态见 A-06） |
| N16 grant approved_risk | 已修复 | §7.2 scheduled_grants「approved_risk」；§2.2「approved_risk=YELLOW」 |
| N17 审计锁重入 | **修复引入新问题** | §7.12「执行路径中，审计在…lock_conn 上写入，不另取锁；执行路径之外的审计追加用独立命名空间 pg_advisory_xact_lock(…)」，见 §2 |
| N18 文档头与修订记录 | 已修复 | 第 3 行「状态：第三轮审核后修订」；第 4 行「在定稿替换时存档到 docs/archive/」；§0.5 D30–D46；§3.6 ADR 001–005 各有用途；§13 P0「ADR-001 / 002 / 003」、P2「ADR-004 / 005」 |
| N19 健身闲置日期 | 已修复 | §2.2「2026-07-17 至 2026-08-15 之间有 6 次到店消费…近 60 天（08-16 起）没有」 |
| N20 申购 / 赎回净值口径 | 已修复 | §8.2「确认日用确认日当日的 nav(confirm_date)…（沙箱简化…）」「W13 的 estimated_units 用 nav(confirm_date) 预估」；赎回「按 T 日净值（沙箱简化）」 |

### 1.2 round3-security（R3-01–R3-16）

| 编号 | 判定 | 证据位置 |
|---|---|---|
| R3-01 复述 / 引用攻击 | 已修复 | §7.4「typed 解析的排除规则：引号…疑问句…不产生写作用域」「复述检测…echoes_untrusted=true…DENY TAINTED_SENSITIVE_ARG，并对该目标指纹设 24h 冷静期」；§6.7 1a「≥ 8 位连续数字…替换为 ****」 |
| R3-02 澄清补答自动合并 | 已修复 | §7.4「typed 补答只有同时满足两条才合槽：(a) G3 请求显式带 clarification_id…(b)…」「普通聊天框的消息一律按新的独立指令处理，并把 OPEN clarification 置为 CANCELLED」；§6.1 clarify「追问句本身由前端按 G21 的 required_fields 套 L2 模板渲染」 |
| R3-03 旧原话未替代 | 已修复 | §7.2 utterances「status ACTIVE/SUPERSEDED/CONSUMED/CANCELLED」；§7.3「utts…G4 要求 utterance_ids ⊆ token.utts 且全部 ACTIVE」；§7.4「『不对，改成……』等改口一律按新指令替代旧指令」 |
| R3-04 hint 非收缩 | 已修复 | §7.9「hint 只能是父作用域的收缩…存在 TAINTED、UNKNOWN_SENSITIVE_ARG、INTENT_SCOPE_EXCEEDED、INJECTION_SUSPECTED 时 replan_hints=[]」 |
| R3-05 按钮 ID 与标签错配 | 已修复 | G22a；§9.5 CardAction「卡片动作不带可见文字，按钮文字由前端向 L2 取」；§9.10 第 5 条 |
| R3-06 预填洗白 | 已修复 | §9.10 第 6 条「L1 卡片、SSE、站内深链的查询参数不能预填任何字段」；§7.5「app_form 也做值级污点检查」 |
| R3-07 RISKY_SEQUENCE 变体 | 已修复 | §7.8「两者顺序不限…源账户不限（含无卡储蓄）」；§5.0「账户级限额」；§10.2 A3「3 个变体必须包含…」 |
| R3-08 A2/A5/A7 谓词 | 已修复 | §10.2 A2 / A5 / A7 新谓词；INTERACTIVE 证据含 `origin, scope_ids[], utterance_ids[]`，SCHEDULED_GRANT / MERCHANT_MANDATE 含 grant_id |
| R3-09 谓词目标与模板用途 | 已修复 | §5.6「谓词目标由 L2 确定性求值」表；「派生模板与动作绑定…用错 → INTENT_SCOPE_EXCEEDED」 |
| R3-10 等价判据 | 已修复 | §7.10 第 5 项「等价的唯一判据是…view_hash…逐字节相同」；§10.2 A4 判定 |
| R3-11 回调重放迟到执行 | **修复引入新问题** | §7.11「终态不可重放」；§7.2 event_decisions；§8.3 event_executions；但 instruction_runs 枚举缺 SKIPPED_REAPPROVAL，见 §2 |
| R3-12 target_fingerprint | 已修复 | §7.2「HMAC(FINGERPRINT_KEY, "ext:" + …NFKC(account_no))…不含户名和银行名」；W2「与已有指纹不同的收款人同名…升红并在面板警示」 |
| R3-13 自由文本外泄 | 已修复 | §7.5「memo…alias 列为敏感参数…非空时必须原样出现在 PlanView 中并纳入 view_hash」；W1「敏感参数：源、目标、金额、memo」；§9.10 第 1 条「必须显示…memo」 |
| R3-14 PIN / evaluate 频控 | 已修复 | §7.3「连续 5 次 PIN 错误锁定 15 分钟…同一来源 IP 每分钟 ≤ 10 次」；§7.4「同一 scope 最多 6 次 evaluate、最多 3 个审批」（削减表与之冲突，见 P1-9） |
| R3-15 评测开关护栏 | 已修复 | §7.3「DEMO_MODE 与 EVAL_MODE 互斥」「DEMO_CONSOLE_TOKEN 少于 32 字节时 gateway 拒绝启动」；§11.1「approval binding 包含 profile 名，跨 profile 不能执行审批…常驻横幅」；§6.6「jti…clarification_id 永远用 secrets 生成」 |
| R3-16 浏览器完整性前提 | 已修复 | §1.4「黄级确认的安全性依赖浏览器完整性…不得宣称…」；§9.10 第 7 条 CSP、≥ 1 秒、isTrusted |

### 1.3 round3-feasibility（P0-1–3、P1-1–9、P2-1–9）

| 编号 | 判定 | 证据位置 |
|---|---|---|
| P0-1 自由键映射 | 已修复 | §6.2「arg_sources…数组元素为 {param, source}…LeafSource…不递归」「understanding.slots 同样是数组」；§6.6「禁止自由键映射」；§6.3「13 个变体的 anyOf」；E5 |
| P0-2 replay 键与易变值 | **修复引入新问题** | §6.6「录制按 (profile, case_id) 分开…端到端 replay 只跑 B1 与 B2」「*_wall 字段不进入任何 LLM 提示」「REPLAY_STALE」；§10.3 运行矩阵；但 §15 TSR 对比 B0 无端到端数据，见 §2 |
| P0-3 日程 | 已修复 | §13 四条并行流、「10-30 24:00 功能冻结」「缓冲 11-01 上午」 |
| P1-1 L3 隔离级别 | 已修复 | §8.4「READ COMMITTED…所有领域读取都在拿到客户锁之后…只对 SQLSTATE 40P01 / 40001 自动重试」；§8.7「50 个不同客户并发他行外转」 |
| P1-2 L2 锁连接池 | 已修复 | §7.8「独占一个 lock_conn，先 SET lock_timeout = '5s'…Semaphore(GATEWAY_MAX_EXECUTIONS=8)…连接池大小 ≥ 18…prepare 3s、execute 10s」（其「审计在 lock_conn 上写」引出的问题记在 N17） |
| P1-3 bankcore 统一加载与效果 | 已修复 | §7.7.1 `load_bank_state` / `persist` / `LedgerEffect` 判别联合 / 一致性比较口径 |
| P1-4 L3 强制与 L2 策略 | 已修复 | §7.7 表「L3 强制」列，SCHEDULED_SHORTFALL「否（L2 策略）」；§9.8「只是 L2 策略，L3 不拒绝」 |
| P1-5 7 天预留算法 | 已修复 | §7.7「未来 7 天预留」：obligations 不用 max_amount、shortfall / max_outflow 公式、time_of_day（PRD 选 06:00 而非建议的 08:00，§2.2 与第 2、6 幕算式一致） |
| P1-6 令牌进 checkpoint | 已修复 | §6.1「经 LangGraph 1.x 的运行时上下文传入…不放进 config["configurable"]…【待核实】」及回归测试 |
| P1-7 STATE_CHANGED 路由 | 已修复 | 同 N1；§9.1「STATE_CHANGED 的 details 固定为 {decision: Decision}」 |
| P1-8 D6 前置会话与原话 | 已修复 | §9.7 D6a、占位符 `{{sid}}`、`{{utt:u1}}`；§10.3「先 D6a…再 D6」 |
| P1-9 削减与 DoD | **修复引入新问题** | §13 削减表 1–5 级、§15 括注；live 口径 §10.3「全文唯一口径」；但见 §2 |
| P2-1 时钟与防重试窗口 | 已修复 | §7.8「sim_now 只在 D3 推进时变化…防重试类窗口…按 wall_now」 |
| P2-2 scheduler 排序与落地 | 已修复 | §8.6「rank 为整数…FUND_CONFIRM=0…DEMO_EVENT=6」「所有 job 都在产生它的事务内经 ScheduleEventJob 落地」「S→L2 回调 30s，job lease 60s」 |
| P2-3 基金算法细节 | 已修复 | §8.2「Decimal 规则…prec=28…floor4…floor_cents」「units += shares、cost_cents += amount_cents」；§2.2 李四持仓 |
| P2-4 冲正入口 | 已修复 | §8.2「冲正：本期不提供冲正入口」 |
| P2-5 线程并发 / psycopg | 已修复 | §6.1「pg_try_advisory_lock…RUN_IN_PROGRESS…【P0 实验】」；§4.2 psycopg 行 |
| P2-6 LLM 调用次数 | 已修复 | §6.1.1「一轮写操作的 LLM 调用目标 ≤ 4 次」；§14 性能行 |
| P2-7 P0 实验阈值 | 已修复 | §6.6「时间盒 4 小时」E1–E6 通过标准与回退 |
| P2-8 双端口实现 | 已修复 | §11.1「两个 uvicorn.Server…public_app 不注册 /internal/*…GATEWAY_PUBLIC_BIND / GATEWAY_INTERNAL_BIND」 |
| P2-9 L3 steps_digest | 已修复 | §8.5 prepare「L3 自行计算 steps_digest…plan_hash / steps_digest / evidence 任一不同 → 409」；§8.3 execution_intents / plan_executions |

### 1.4 round3-contracts-facts（A-01–A-12、B-01–B-07）

| 编号 | 判定 | 证据位置 |
|---|---|---|
| A-01 409 体与 UNKNOWN 分支 | 已修复 | §9.1 STATE_CHANGED details；§6.1.1 202 UNKNOWN 行 |
| A-02 AppFormPlanRequest | 已修复 | §9.5「AppFormPlanRequest（仅 F）…参数来源记为 {kind:"action"…}」；G19「action_id 即 form_action_id」「fields 等于对应 W 工具的 args」；§6.2 Source 含 `action{ref}` |
| A-03 UICard 模型 | 已修复 | §9.5 UICard / CardAction |
| A-04 S 回调登记 | 已修复 | §9.5a S1 |
| A-05 runner 凭证 | **修复引入新问题** | §10.3「runner 作为普通浏览器客户端…EVAL_MODE 下单独挂载 .secrets/eval_pins」；§11.1 DEMO_LOGIN_PINS「仅 EVAL_MODE」；`make demo` 在 DEMO_MODE 下的自动化无 PIN，见 §2 |
| A-06 状态枚举 | **部分修复** | §7.2 plans.status / approvals.status；§9.5「PlanStatus 与 ApprovalStatus 同 §7.2；TransactionStatus = …」；G17「status 取 ApprovalStatus 或 ALL」；取消词使计划失效后的状态未映射，见 §2 |
| A-07 F1 客户列表 | 已修复 | G22；F1「G22 / G1 / G14」 |
| A-08 D4 渠道与流水类型 | 已修复 | D4「channel:ONLINE/OVERSEAS/ATM/QUICKPASS/POS?…带 card_id 的 EXTERNAL_DEBIT 记为 CARD_SPEND」 |
| A-09 评委视图计划 / 审计 | **修复引入新问题** | §9.7 D11、D12 已加；但 §9.9 计数写成「13 个 D 分组（D0–D12、D6a）」，实为 14，见 §2 |
| A-10 账户展示标识 | 已修复 | §8.3 accounts「display_name…account_no（种子固定，展示时脱敏）」；AccountView「display_name、account_no_masked、current_card_id?」；PlanView「账号尾号/本行标识」 |
| A-11 F3 / D0 / G1 字段 | 已修复 | F3 body；§9.7「D0 POST /demo/auth/login {secret} → {expires_at_wall}」；G1「customer:{customer_id,display_name}」 |
| A-12 证据存放位置 | 已修复 | §10.2「L3 与执行结果同事务存入 plan_executions.authorization_evidence（事件存 event_executions…），每条 operation_events 经 (customer_id, plan_id) 或 event_id 关联」 |
| B-01 自动切 /beta 前提 | 已修复 | §0.4 V-LC「仅当 api_base 等于包内默认值 https://api.deepseek.com/v1 时」；§6.6「配置方式：…不设置 DEEPSEEK_API_BASE，并对 understand、plan…显式设 api_base=…/beta；P0 用请求日志确认」；§4.2 langchain-deepseek 行 |
| B-02 AEGIS 否定断言 | 已修复 | §4.5「原则：只写『据摘要，X 做 A』」；AEGIS 行「未读正文，不断言其是否模拟状态」 |
| B-03 V-INV 措辞 | 已修复 | §0.4 V-INV「在 analyze 求值时报 ValueError…没有任何规则成功求值过；两两箭头写法未执行…（未实际运行）」；§4.2 不用表 |
| B-04 Prompt Guard 2 证据 | **部分修复** | §0.4 V-PG、§4.2 已改为「模型卡语言元数据…中文效果【待核实】」；但 §4.3 仍写「官方评测语言都不含中文」，见 §2 |
| B-05 FIDES / Progent 表述 | 已修复 | §4.2「（据摘要）」；§4.4.4 FD-1「『组合取最低』是本项目按 IFC 常规做法的设定」、PG-1「简化为静态的工具级登记」 |
| B-06 BL-7 / V-BL2 位置 | 已修复 | §0.4 V-BL2「BalanceMonitor{Condition: AlertCondition{Field, Operator, Value…}}…model/model.go:524…包根目录 balance.go:79」；§4.4.2 BL-7 来源列 |
| B-07 Bank of Anthos | 已修复 | §4.2 不用表「是早先调研的结论，未重新读代码【待核实】」 |

---

## 2. 非「已修复」条目详述

### N7 · 部分修复：回放正则对派生 ID 与 `secrets` 生成的 ID 覆盖不确定

**现状**：§6.6「易变 ID 的识别：`\b[a-z]{2,4}_[0-9A-HJKMNP-TV-Z]{26}\b`（覆盖 §9.1 全部运行期前缀）与 UUID」「jti、CSRF 令牌、OTP、HMAC 盐、hint_id、action_id、form_action_id、clarification_id 永远用 secrets 生成」；§9.1「运行 ID 用 ULID；步骤派生实体使用前缀 + UUIDv5」。

**残留**：
1. `hnt_`、`act_`、`clr_` 在 DEMO / EVAL 下按规定不确定，所以每次运行都不同。它们会进入 plan 节点「上次 Decision 摘要」中的 replan_hints，以及 clarify 的 G21 数据。PRD 没有规定这些 ID 用 `secrets` 生成时仍保持「前缀 + 26 位 Crockford base32」格式。如果实现成 `secrets.token_urlsafe()`，正则就匹配不到，replay 会因此未命中。
2. 派生实体 ID 的形式是「前缀 + UUIDv5」（如 `crd_<uuid>`）。「与 UUID」没有给出模式；如果照常写成 `\b<uuid>\b`，因为 `_` 属于单词字符，`crd_` 后面不存在单词边界，同样匹配不到。

**替换文字**（§6.6「易变 ID 的识别」一句改为）：

> 易变 ID 的识别：`\b[a-z]{2,4}_(?:[0-9A-HJKMNP-TV-Z]{26}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b`（覆盖 §9.1 全部运行期前缀与「前缀 + UUIDv5」派生 ID），另加不带前缀的 UUID；种子 ID 白名单不替换。用 `secrets` 生成的 hint_id、action_id、form_action_id、clarification_id 也必须是「前缀 + 26 位 Crockford base32」格式（取 130 位安全随机数编码），保证被同一正则规范化。

### N14 · 部分修复：F4 仍用 G18 令牌恢复执行，与 G18「不能执行」矛盾

**现状**：§7.3、G18 都写明 G18 换出的令牌 `utts=[]`，「不能提交或执行计划」。但 §9.6 F4 写的是「BFF 查 G9 绑定 sid/plan，必要时 G18 换助手令牌再传 Command」。resume 之后 L1 进入 execute 节点调用 G8，用的正是这个令牌。

**问题**：真需要 G18 的时候，原令牌已超过 10 分钟，这时原话也已过期（§7.10 第 6 项「deadline 为原话、审批和会话有效期的最小值」）。所以「G18 后 resume」这条路径永远只会得到失败。按字面实现，L1 拿到的令牌会被 G8 拒绝，而 §6.1.1 没有为 G8 的 401/403 定义去向。

**替换文字**（§9.6 F4 行）：

> | F4 | POST `/api/sessions/{sid}/resume` | `{approval_id}`；BFF 查 G9 绑定 sid/plan，用该轮 G3 签发、仍在有效期内的助手令牌传 `Command(resume)`。原令牌已过期说明原话已过期：BFF 不调用 L1 resume，直接经 G5 读取并返回终态，提示用户重新发起（G18 只用于 F5 读状态）；resume 本身不代表批准 |

### N17 · 修复引入新问题：两类审计写入用不同的锁，同一客户的哈希链会分叉

**现状**：§7.12「执行路径中，审计在持有执行锁的同一 lock_conn 上写入（§7.8），不另取锁；执行路径之外的审计追加用独立命名空间 `pg_advisory_xact_lock(hashtext('audit'), hashtext(customer_id))`」；§7.8「持锁期间…audit_events…都在 lock_conn 上按小事务执行」。

**问题**：执行路径只持有客户执行锁，执行路径之外（read 摘要、审批状态变迁、通知、G16 读取审计等）只持有审计锁。同一客户的这两类追加互不排斥，可能读到同一个 `prev_hash` 并各自写入，哈希链就分叉了。之后链校验（D12 `chain_valid`）会把正常并发误判为篡改。自锁问题确实消除了，但串行化也一起丢了。

**替换文字**（§7.12 第一段中「执行路径中…不会与执行锁重入」一句改为）：

> 所有审计追加（包括执行路径）都在写入事务内取同一把事务级锁 `pg_advisory_xact_lock(hashtext('audit'), hashtext(customer_id))`。执行路径在 `lock_conn` 上的每个审计小事务里获取它；它与会话级客户执行锁（单 bigint 键空间）不在同一键空间，不会自锁或重入。锁序固定为「客户执行锁 → 审计锁」，审计锁只在单条追加事务内持有，因此不形成环。

同步修改 §7.8：「持锁期间 gateway 库的所有写入…都在 lock_conn 上按小事务执行」后面追加「；其中 audit_events 追加另按 §7.12 取审计事务锁」。

### R3-11 · 修复引入新问题：L3 `instruction_runs` 的终态枚举少了 SKIPPED_REAPPROVAL

**现状**：§7.2 `event_decisions`、§8.3 `event_executions`、S1 返回都包含 `SKIPPED_REAPPROVAL`；§7.11「升高风险记录 SCHEDULED_REAPPROVAL_REQUIRED 并跳过本期」。但 §8.3 `instruction_runs` 的 status 只有「SUCCESS/FAILED_INSUFFICIENT/BLOCKED」，而 R7 的 `last_run:{status,code?,due_at}` 取自 instruction_runs。

**问题**：跳过的那一期在 L3 指令历史里要么无法记录，要么被迫记成 BLOCKED，L2 与 L3 的终态因此对不上。§8.5 也没有说明 SKIPPED 走 `/events/{id}/block` 时如何区分。

**替换文字**：
- §8.3 `instruction_runs` 行：「status SUCCESS/FAILED_INSUFFICIENT/BLOCKED/SKIPPED_REAPPROVAL」。
- §8.5 `/internal/events/{event_id}/block` 行追加：「`code=SCHEDULED_REAPPROVAL_REQUIRED` 时记终态 SKIPPED_REAPPROVAL，其余 code 记 BLOCKED；两者都推进周期、不移动资金」。

### P0-2 · 修复引入新问题：端到端不再跑 B0，§15 的 TSR 对比基线随之消失

**现状**：§6.6、§10.3「端到端 replay 只跑 B1 / B2」「网关层跑 B0 / B1 / B2 与三种消融」；§15「B2 相对 B0 的正常 TSR 下降≤5 个百分点」；§10.3 TSR「正常用例达到预设 L3 目标状态且回复关键数字正确」（「回复关键数字」只有端到端才有）。

**问题**：B0 没有端到端结果，§15 的 TSR 对比在端到端层无法计算；放在网关层算又不含「回复数字正确」这一项，口径不一致。§1.3 G5 的「正常用例完成率下降不超过 5 个百分点」也受影响。

**替换文字**（§15 第 8 条前半句）：

> - [ ] B2 UTR=0（分母非零；零分母仅 N/A）；**网关层**（固定 PlanDraft，TSR 只看 L3 目标状态）B2 相对 B0 的正常 TSR 下降 ≤ 5 个百分点，FPR ≤ 5%；端到端 replay 只报告 B1 / B2 的 TSR（含回复数字正确）及两者差值；如未达标如实标未通过。

§10.3 指标表 TSR 行追加：「网关层不检查回复数字，只看 L3 目标状态；两层分别报告」。

### P1-9 · 修复引入新问题：第 2 级削减是空操作且引用了不存在的原文；第 3 级「频控」与新增安全频控冲突

**现状**：§13 削减表第 2 级「3 个消融组；B0 端到端（只保留网关层）｜§15『完整 replay』改为『B1 / B2 端到端 + B0 / B1 / B2 网关层』」；第 3 级「OTP 重发与频控（固定 1 次）」；§15 第 7 条已写成「网关层 B0 / B1 / B2 与消融报告 + 端到端 B1 / B2 replay 报告…（第 2 级：去掉消融）」。

**问题**：
1. 按 D42，B0 本来就不跑端到端，所以第 2 级的「B0 端到端」不是削减项（正是原 P1-9 批评过的「本来就不在范围内」）。§15 里也已经没有「完整 replay」这几个字，替代文字没有着落。
2. 第 3 级的「频控」字面上会覆盖 R3-14 新增的 PIN 锁定，以及「同一 scope 最多 6 次 evaluate、3 个审批」。这两项是 P1 安全修复，§13「不可削减：…所有 P0/P1 安全问题」与此矛盾。

**替换文字**：
- 第 2 级行：「| 2 | 3 个消融组 | §15 第 7 条改为『网关层 B0 / B1 / B2 报告 + 端到端 B1 / B2 replay 报告』，消融结果标未做 |」。
- 第 3 级中「OTP 重发与频控（固定 1 次）」改为「OTP 重发（每个审批只发送 1 次；OTP 错误次数锁定、登录 PIN 锁定与同作用域 evaluate / 审批次数频控**不可削减**）」。

### A-05 · 修复引入新问题：PIN 只给 EVAL_MODE 的 runner，`make demo` 在 DEMO_MODE 下无法自动登录和确认

**现状**：§11.1 DEMO_LOGIN_PINS「可信评测 runner（仅 EVAL_MODE，单独挂载 .secrets/eval_pins）；不注入 agent / frontend」；§10.3 runner 身份同此；§7.3「DEMO_MODE 与 EVAL_MODE 互斥」；§11.2「make demo｜默认 replay，按 §12 场景准备与验证」；§15「三遍 replay 的语义断言一致」；AGENTS.md §8.2「改了 L1/L2 交互时，运行 make demo（replay 模式）」。

**问题**：`make demo` 是自动脚本，要在 DEMO_MODE 下完成登录、确认、读 OTP（第 2、4、6 幕都要）。按现行凭证表，DEMO_MODE 下只有现场操作员手动输入 PIN，脚本拿不到 PIN，「三遍 replay」和回归验证都跑不起来。由于两种模式互斥，脚本也不能借用 EVAL_MODE。

**替换文字**：
- §11.1 DEMO_LOGIN_PINS 行：「gateway（校验）；可信 runner：EVAL_MODE 的评测 runner 与 DEMO_MODE 的 `make demo` 驱动脚本（同一程序，单独挂载 `.secrets/eval_pins`）；不注入 agent / frontend」。
- §10.3 第 1 条末尾追加：「`make demo` 的驱动脚本使用同一 runner 程序与同一身份规则，在 DEMO_MODE 下运行」。

### A-06 · 部分修复：取消词使计划失效后，计划与审批的状态没有映射

**现状**：§7.2 PlanStatus 与 ApprovalStatus 已闭合；§7.4「取消词…使本轮授权 epoch 递增，旧原话、hint 和待审批计划全部失效」；§9.8 有 `AUTHORIZATION_CANCELLED`。原 A-06 建议的 PlanStatus 中有 `CANCELLED`，当前枚举没有，也没有说明这种情况复用哪个状态。

**问题**：前端靠 G5 / G17 恢复状态（§11.3），epoch 失效的计划该显示什么、G17 中的审批落在哪个筛选值下，都不确定。

**替换文字**（§7.2「状态枚举」列表末尾追加一条）：

> - 取消词使 epoch 递增时：未进入 EXECUTING 的计划置 `EXPIRED`，其 PENDING / CONFIRMED 审批置 `SUPERSEDED`，G8 对其返回 409 `AUTHORIZATION_CANCELLED`；已在 EXECUTING / UNKNOWN 的计划按 §7.10 走到真实终态。

### A-09 · 修复引入新问题：§9.9 的 D 分组计数错误

**现状**：D11、D12 已加入 §9.7。§9.9 写的是「13 个 D 分组（D0–D12、D6a）」。D0–D12 共 13 个，加上 D6a 是 14 个。§9.9 是 P1 生成契约时逐项清点的检查清单，计数错了会让完整性检查失真。

**替换文字**：§9.9 中「13 个 D 分组（D0–D12、D6a）」改为「14 个 D 分组（D0–D12 与 D6a）」。

### B-04 · 部分修复：§4.3 仍有「官方评测语言」的无证据表述

**现状**：§0.4 V-PG、§4.2 不用表已改正，但 §4.3 第 2 行仍是「不用 ML 注入分类器｜V-PG：候选模型的元数据语言与官方评测语言都不含中文」。

**替换文字**（§4.3 该行证据列）：

> V-PG：候选模型的模型卡语言元数据都不含中文，中文效果【待核实】；Prompt Guard 2 需人工审批下载

---

## 3. 统计

| 来源 | 条数 | 已修复 | 部分修复 | 未修复 | 修复引入新问题 |
|---|---|---|---|---|---|
| round3-closure（任务 A 残留 11 + N1–N20） | 31 | 28 | 2（N7、N14） | 0 | 1（N17） |
| round3-security（R3-01–16） | 16 | 15 | 0 | 0 | 1（R3-11） |
| round3-feasibility（P0 3 + P1 9 + P2 9） | 21 | 19 | 0 | 0 | 2（P0-2、P1-9） |
| round3-contracts-facts（A 12 + B 7） | 19 | 15 | 2（A-06、B-04） | 0 | 2（A-05、A-09） |
| **合计** | **87** | **77** | **4** | **0** | **6** |

结论：D30–D46 覆盖了第三轮全部 87 条发现，没有遗漏未改的条目。10 条非「已修复」都属于修订文字的局部缺口或连带矛盾，没有新的 P0。其中最需要优先处理的有 4 条：N17（审计哈希链分叉）、P0-2（TSR 基线缺失）、A-05（`make demo` 无法自动登录）、P1-9（削减表会削掉 P1 安全频控）。其余 6 条是措辞或枚举层面的修补，按上面的替换文字直接改即可。
