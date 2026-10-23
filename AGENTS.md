# AGENTS.md：SafeBank Gateway 接手指南

> 任何 agent（Claude Code、Codex、Cursor 等）接手本项目时，**先完整读完本文件**，再读 [PRD.md](PRD.md)。
> 本文件是项目的「当前状态 + 工作规则」唯一来源。每完成一个工作单元都要更新 §4「进度日志」和 §3「当前状态」。

---

## 1. 项目一句话

参加 2026 深圳国际金融科技大赛（FinTechathon）人工智能赛道「银行 AI 智能体」赛题。我们构建一个覆盖**全部六个银行业务场景**的 AI 银行助手，并采用**三明治三层架构**：

- **L1 Banking Agent**（LangGraph）：理解意图、追问缺失参数、生成结构化计划。**不持有任何写权限。**
- **L2 Security Gateway**（核心创新）：所有工具调用的唯一入口。负责执行前**金融状态预演**、**跨步骤工作流风险监控**、污点追踪、审批绑定、审计。
- **L3 Banking Backend**（FastAPI + PostgreSQL）：模拟银行的账户、账本、卡、订阅代扣、理财、模拟时钟。

核心研究问题：金融预演 + 工作流感知安全，能否在不明显降低正常任务完成率的前提下，显著减少未授权的高风险操作？

## 2. 不可违反的约束

以下内容由团队拍板，除非用户明确要求，否则不要修改或重新讨论：

1. **三明治架构已确定**。L1 不能绕过 L2 访问 L3。这一点通过网络隔离（两个 Docker 网络）加凭证隔离（只有 L2 持有 `GATEWAY_SERVICE_TOKEN`）**强制保证**，而不是靠提示词约定。
2. **六个场景全部实现**：智能转账、账单分析、卡片管理、订阅管理、理财操作、跨场景联动。
3. **L1 使用 LangGraph**（用户已确认）。
4. **LLM 永远不决定是否授权**。授权只由 L2 的确定性规则决定。分类器（注入检测）只能升级风险，不能放行。
5. **金额一律用整数「分」**（字段名 `*_cents`），禁止使用 float。份额用 `Decimal` / `numeric(18,4)`。
6. **预演永不写 L3**；预演与执行**共用** `packages/bankcore` 中的纯函数规则。
7. **攻击是否成功以 L3 数据库状态判定**，不以 LLM 的回复判定。
8. 优先 Python 生态；不引入多 Agent、RAG、微调、K8s 等不必要的复杂度。
9. 最大化复用开源，但**不要假设开源项目实现了某功能**，必须读代码确认。没有 LICENSE 的仓库（例如 BankPilot）不能复制代码。
10. 作品提交截止为 **2026-11-02**（已确认）。遇到取舍时，按 PRD §13 的「削减顺序」处理。
11. **LLM 使用 DeepSeek V4 Pro（API 接入）**；只有需要多模态（图片输入）时才调用 **DeepSeek V4.1 Flash**。不要换用其他模型。从图片中抽取的内容一律视为不可信数据。
12. 主办方不提供 LLM、沙箱或数据：API 自备，L3 就是沙箱，演示数据全部自造（合成数据，不含真实个人信息）。
13. **前端暂停**（用户 2026-10-23 决定）：前端 App 与 BFF（PRD §9.6 F1–F13、§9.10 页面）**一律不开工**，直到用户亲自下令。在此之前只做后端；需要浏览器 / BFF 的地方，测试中直接用 F 服务令牌 + app JWT 调 gateway 代替。L2 阶段计划见 [docs/plans/L2-plan.md](docs/plans/L2-plan.md)。

## 3. 当前状态（每次工作后更新）

**最后更新**：2026-10-23

| 模块 | 状态 |
|---|---|
| 调研 | 开源项目元数据和 DeepSeek 官方信息已核实；评分/提交已从官网公开 JS 核实（docs/acceptance/official-v1.md）；竞品「明鉴」未核实 |
| PRD | **`PRD.md` = v2.1 定稿**（1,764 行，§0–§16；三轮多 agent 审核 + 两次修订复核，决议 D1–D47 见 §0.5）。v1.2 与作废的 v2.0 草稿在 `docs/archive/`；审核报告在 `docs/reviews/` |
| 代码 | **P1 完成并逐组件验收**：18读/14写契约、真实L3转账/卡片/账本/快照/执行恢复；116项单元+33项真实PG通过，契约v1已冻结。分支 feat/l3-core，P1代码已提交并推送私有仓库（功能提交7fd7a26）；CI权限修复70dcabe已推送，远端unit与p1-postgres均通过（run 39929405919） |
| L2 网关 | **W0 基础骨架已合入 main**（PR #2，merge e2e91db，2026-10-23；经 4 轮架构评审 + 1 次 CI 修复）。波次 1 已启动：W1/W3已有待联调检查点，W2/W4与共享S1按槽位推进。计划 `docs/plans/L2-plan.md`，任务书 `docs/plans/briefs/` |
| 前端 | **暂停**，用户亲自下令才开工（§2 第 13 条） |
| 下一步 | L2 波次 1：W1 身份与原话、W2 读代理与来源、W3 预演·工作流·决策、W4 审批与执行，四路并行（见 §5「L2 进度」）。2026-10-23 起工作转到 Codex，交接说明见 `docs/handoff/CODEX-HANDOFF.md` |

### 已确认事实

- 项目启动日为 2026-10-22。
- 作品提交截止：**2026-11-02**（团队确认）。
- 主办方**不提供** LLM、沙箱或数据（团队确认）。
- LLM：`deepseek-v4-pro`（主，不支持图片）；`deepseek-flash`（V4.1 Flash，仅多模态）。base_url 为 `https://api.deepseek.com`，strict 模式用 `/beta`。思考模式默认开启；带 tools 的请求必须回传 `reasoning_content`，否则返回 400。完整说明见 PRD §6.6（来源：DeepSeek 官方文档，2026-10-22 查阅、10-23 复核）。
- `langchain-deepseek` 与 `langchain-openai` 都**不回传** `reasoning_content`（读源码确认）；`bind_tools(strict=True)` 只在 `api_base` 为默认 `…/v1` 时自动切 /beta（PRD §0.4 V-LC）。
- 开源项目的定选、license、逻辑移植映射与相关工作见 PRD §4（核实记录 §0.4；v1.2 的 star 表在 `docs/archive/PRD-v1.2.md` §3.7）。要点：
  - **没有 LICENSE，不得复制代码**：BankPilot、Rasa starterpack、Formance Ledger。
  - LLM Guard 已 archived。
  - Invariant 维护基本停滞（最近一次提交在 2026-01-12）。
  - devbanksdk 源码已读：没有认证、没有行锁、没有幂等、没有测试，因此不 fork。
- 本机环境：macOS，Python 3.13（anaconda），已安装 git、gh、docker。

### 未确认（不要当成事实引用）

- 模板细则、上传大小与登录提交入口权限（评分/必交物与时长已核实）。
- 竞品「明鉴」的实际实现。
- P0/P1实测：指定tool_choice与思考模式的组合被拒，固定关闭思考；13工具生产Schema已完成3个DeepSeek实际样本并通过本地Schema/运行时校验（docs/acceptance/p1-contracts-live）。LangGraph内存检查点上下文隔离已通过；真实Postgres saver及完整银行图仍留后续阶段。Invariant、assistant-ui、AgentDojo runner 已决定不用。
- 注意：之前出现过一版技术选型报告，其中包含**编造的** L2/L3 star 数、devbanksdk 行号、明鉴的描述和官网日期。那一版已经作废。如果你在别处看到这些数字，不要相信。

## 4. 进度日志（追加写，最新在下）

| 日期 | 执行者 | 内容 |
|---|---|---|
| 2026-10-22 | Claude | 完成 L1 候选调研（克隆 Redis demo、BankPilot、MifosX MCP，并查看 Rasa starterpack）。调研 L2/L3/赛题的子 agent 因模型错误中断，没有产出。 |
| 2026-10-22 | Claude | 讨论 L1 框架后，用户确认使用 LangGraph。 |
| 2026-10-22 | Claude | 写完 PRD v1.0 与本文件；重写技术选型报告，删除未核实内容。 |
| 2026-10-22 | Claude | 按用户确认更新 PRD v1.1：截止日期 11-02；主办方不提供 LLM、沙箱、数据；LLM 改为 DeepSeek V4 Pro + V4.1 Flash（多模态）；开发计划去掉具体日期，改为阶段 P0–P10。 |
| 2026-10-22 | Claude | PRD 新增 §3.7「开源项目复用与参考」：按 L1 / L2 / L3 / 评测 / 前端分类，标明依赖、改写、参考、不用四种方式，以及每项的核实状态。只有 L1 部分和 assistant-ui 的 star 数核实过，其余标【待核实】。原因是 Bash、WebSearch、WebFetch 一直报 auto mode 安全检查模型不可用，无法联网核实。 |
| 2026-10-22 | Claude | 联网恢复后回填 PRD v1.2：§3.7 中 24 个项目的 star、license、push 时间、archived 状态（`gh api`）；阅读 devbanksdk 源码，结论为不 fork；查阅 DeepSeek 官方文档，确认 model ID、能力、价格、strict 模式限制和 `reasoning_content` 回传要求，写入 §5.5，结构化输出改为 strict tool calling。剩余【待核实】项都需要实际运行，留给 P0 POC。 |
| 2026-10-22 | Claude（会话 d8aba59b，本条于 10-23 补记） | 按用户要求把 TigerBeetle、Blnk 等「逻辑移植」项目写进 PRD：PRD v1.2 新增 §3.8 并写完 TigerBeetle 部分（TB-1~9）。读原文核实了 TigerBeetle 文档、Blnk dry-run 源码、Fineract standing instruction 枚举、langchain-deepseek / langchain-openai 源码（两者都**不回传** `reasoning_content`）、assistant-ui react-langgraph 的 interrupt API、PyPI 版本与 license（`langgraph-api` 为 Elastic-2.0）、Hugging Face 注入分类器（不支持中文）；arXiv 检索到 AEGIS、Cordon、Sapien 等相关工作（只读摘要）。Invariant POC：能本地运行，但三段 `->` 链式写法报语法错误，两两箭头写法未测完。随后开始整篇重写为 v2.0，写完 §0 后 API 额度不足（403），**PRD.md 被截断为只剩 §0**。 |
| 2026-10-23 | Claude | 从会话记录恢复上下文。把 file-history 中完整的 v1.2 备份恢复为 `PRD.md`，把截断的 v2.0 残稿另存为 `PRD-v2-draft.md`。依据 d8aba59b 记录里的核实证据（`/tmp/sbg-research` 已被清理，证据只存在会话记录中），在 `PRD-v2-draft.md` 写完 v2.0 初稿 §0–§16：新增 §0.4 核实记录、§4 定选清单与移植映射（TB-1~10、BL-1~8、FN-1~4）、§4.5 相关工作与收窄后的创新表述，以及 L1/L2/L3 的实现细节。前端按用户要求改为银行 App 形态。 |
| 2026-10-23 | Claude | 用户要求：PRD 必须闭合；初稿写完后开多个 subagent 做多轮审核。第一轮已启动 5 个只读审核：闭合与一致性、安全红队、可实现性、场景与接口覆盖、开源引用事实核查。 |
| 2026-10-23 | Claude | 用户新要求：前端先不锁定，PRD 只大致列出需要的页面；主要锁定**接口文档**，且接口要覆盖比赛全部 6 个场景需求。随后用户补充：接口文档**写在 PRD 里，不单独成文**。据此把 PRD §9 改为「接口文档（锁定）+ 前端页面清单（不锁定）」。 |
| 2026-10-23 | Claude | 写完 `PRD-v2-draft.md` §9 接口文档：§9.1 通用约定（金额 / 比例 / 份额 / 时间 / ID 前缀 / 错误格式 / HTTP 状态）；§9.2 六个场景 29 个需求 ID → 接口覆盖矩阵，以及赛题六项能力的落点；§9.3 read 工具 R1–R16 的参数、返回、untrusted 字段；§9.4 write 工具 W1–W14 的参数、约束、执行结果、基础等级、ActionKind、敏感参数；§9.5 L2 接口 G1–G14，以及 PlanView、ExecutionResult；§9.6 BFF 接口 F1–F13；§9.7 演示接口；§9.8 错误码与规则代码总表；§9.10 页面清单与 4 条安全硬约束。同时把 §3.5、§4.2 中的前端依赖改为「建议，不锁定」（P2 由前端负责人定，写 ADR-005）。 |
| 2026-10-23 | Claude（审核 subagent） | 第一轮审核「场景与接口覆盖」完成，32 条（阻塞 8）。要点：演示与阈值、种子数据数字互相矛盾（单笔 > 1,000 元即红，导致几乎全红）；A3 跨会话序列在 B1 下也会因单条规则升红，看不出增益；缺本人账户互转；计算得出的金额会被判 unknown；订阅「已拦截扣款」与「删除即不发起」矛盾，闲置判定会误判；绿级计划没有执行路径；REPLAN 自动改金额等于替用户决定；`submit_risk_assessment` 可经小安伪造答案绕过适当性；缺若干 read 工具（余额曲线、产品详情、AA 列表、客户信息、待确认列表）。待其余 4 份报告到齐后统一修改。 |
| 2026-10-23 | Claude（审核 subagent） | 第一轮审核「安全红队」完成：严重 3、高 7、中 12、低 5。严重项：(S1) L1 拿着全权限用户 JWT，可冒充浏览器调 BFF 完成原话登记、确认审批、读 OTP——需拆分浏览器令牌与助手令牌；(S2) plan 的 `origin` 由调用方自报，L1 可自称 app_form 跳过污点与意图检查——改由 L2 按调用方凭证判定；(S3) `/demo/*` 只用用户 cookie 鉴权，演示环境可读 OTP、注入资金——需独立的控制台令牌。高危项：面板 plan_id 与 approval_id 可错配、建议短语与卡片按钮可被洗成 user 原话、OTP 未绑定计划内容、测评可被伪造、trusted 数值洗白金额、原话授权窗口未定义、定时转账绕过窗口规则。 |
| 2026-10-23 | Claude（审核 subagent） | 第一轮审核「闭合与一致性」完成：阻塞 12、重要 20、次要 8。与前两份重合的有：数字推演、本人互转、REPLAN 路由、A3 无增益、种子数据缺李四与 R2 客户、订阅拦截无触发源。新增问题：`ANOMALY_*` 未定义；预演违规的效果与 DENY 冲突；评测 runner 跨网络并持有 L3 令牌，违反 §3.2；`approval_ref`、`run_token` 机制不闭合；gateway 库缺 plans、decisions、approvals、notifications 表；对照组开关有两套名称；B1 定义过弱，沦为稻草人；削减顺序与 DoD 冲突；AGENTS.md 仍引用 v1.2 的章节号。 |
| 2026-10-23 | Claude（审核 subagent） | 第一轮审核「可实现性」完成：阻塞 11、重要 18、次要 6，另有 10 条工作量简化建议。新增问题：计划内引用本计划新建的实体没有机制；跨客户转账与 per-customer `state_version`、锁范围不一致；Counters 与每日上限没有数据来源；卡限额是否约束转账未定义；scheduler 回调 L2 的事务边界问题；respond 先流式输出再校验，顺序反了；回放会被易变 ID 破坏；AgentState 每轮不重置；CHECK 写法、transfers 与 transactions 对应关系、pending 超时、NAV 来源都需定稿。结论：架构可行，但 11 天工作量约超 1.5–2 倍。 |
| 2026-10-23 | Claude | 汇总前四份报告，形成修订决议清单 D1–D52（见 PRD v2.1 §0.5），开始按章节重写 `PRD-v2-draft.md`。 |
| 2026-10-23 | Claude（审核 subagent） | 第一轮全部报告到齐。① 事实核查：未发现编造，版本号、arXiv 编号、文件路径、枚举值都属实，Blnk 行号与 HEAD 逐行吻合。需更正的有：§6.5 SSE 事件格式 4 处与 assistant-ui / langgraph-sdk 源码不符（`error` 应为 `{error, message}`、没有 `end` 事件、`custom` 需要 `onCustomEvent`、`updates` 里的 messages 会被并入聊天列表）；「不支持中文」改为「官方未在中文上评测」；strict 模式的 null 类型官方未列出，改为 P0 实验；BL-7 与 TB-2 的引用位置要校正；思考模式参数名已由官方文档确认。② 三位审核员针对新 §9 的补充报告：§8.3 表结构约 20 个字段与接口对不上；外部事件和基金生命周期需要补 `apply_*`；§7.1 与 §9.5 不一致；还缺工资入账、客户级审计、待确认列表、产品详情等接口。 |
| 2026-10-23 | Claude | **因额度暂停（用户要求）。** v2.1 重写进度：`PRD-v2.1-wip.md` 已按决议 D1–D29 重写完 §0–§6（§0.5 是决议清单）；§7–§16 还没重写。`PRD-v2-draft.md` 是完整的 v2.0 初稿（含 §9 接口文档），未改动；`PRD.md` 仍是 v1.2。**接手者下一步**：(1) 以 `PRD-v2-draft.md` 的 §7–§16 为底稿，按 §0.5 决议和第一轮审核报告（要点见上面几条日志）重写 §7 L2、§8 L3、§9 接口文档、§10–§16，追加到 `PRD-v2.1-wip.md`。§9 必须同步：read 工具增至 18 个（新增 `get_product`、`get_profile`）；R1 增加当日转出与剩余额度；R4 增加 `mom_change_cents`；R14 增加 `product_type` 与 `risk_label`；新增 G15 `POST /sessions`、客户级审计、`GET /approvals?status=`；G6 确认请求带 `view_hash`；演示接口改用 `DEMO_CONSOLE_TOKEN`，并加 `scene/prepare`、`attack/submit_plan`、`eval/assert`；§8.3 表结构按接口补齐（merchants、merchant_billing、transactions.kind、merchant_id、payee_id、balance_after_cents 等）；§7.7 补外部事件与基金生命周期的 `apply_*`；gateway 表清单（sessions、plans、evaluations、approvals、scheduled_grants、notifications 等）；演示脚本按 D1、D12、D26 的新数字重排（张三活期 6,000；第 2 幕王五 3000 + 赵六 4000，提示最多 2,955 元；第 4 幕跨会话序列 DENY；第 6 幕李四申购，孙八 R4 被拦）。(2) 全部写完后开第二轮审核 subagent。(3) 通过后把 v2.1 改名为 `PRD.md`，v1.2 移到 `docs/archive/`，删除 draft 和 wip 文件，并同步 AGENTS.md 中引用的 PRD 章节号（§5.5 → §6.6，§3.7 → §4，§11 → §13）。 |

| 2026-10-23 | Codex | 接手续写任务，读取 AGENTS.md、PRD v1.2、v2 草稿与 v2.1 工作稿。实查 v2 草稿仅 826 行，结束于 §6.8 / PRD2-PART4 标记，并不包含交接记录所称 §7–§16 与 §9 接口；v2.1 为 861 行、截至 §6.7。保留原件，以 v1.2、D1–D29 及审核日志重建后半部。用户再次明确要求完成后开多个 subagent 审核。尚无代码，未运行代码测试。 |

| 2026-10-23 | Codex | 补写 v2.1 §7.1–§7.12：调用方矩阵、gateway 表、app/assistant JWT、原话生命周期、派生金额模板、ActionKind、预演与工作流、审批/OTP、原子提交与超时恢复、定时回调、审计通知。定时回调由 L2 检查后主动提交 L3，不发可复用执行令牌；明确 L3 操作日志为跨会话事实源。待全稿完成后独立审核，未运行代码测试。 |

| 2026-10-23 | Codex | 补写 v2.1 §8 L3（账本、基金、商户账单、锁、时钟、内部接口）与 §9 完整接口：18 read、14 write、G1–G20、F1–F13、D1–D10；覆盖 29 个需求 ID，补账户曲线/AA 列表/产品详情/客户信息/审计/待确认/工资事件，明确金额与份额、工具来源、错误码、审批与前端安全边界。接口仍待第二轮审核，未宣称已实现或已跑通。 |

| 2026-10-23 | Codex | 完成 §10–§16：公平基线、L3 状态判定、分层用户模型、评测矩阵、部署/密钥/恢复、7 幕独立演示、带日期计划、削减与 DoD、待核实项。已按用户要求启动第二轮 3 个审核 subagent（安全、接口覆盖、数据一致性与评测），报告写 docs/reviews/round2-*.md；正式 PRD 仍为 v1.2，审核前不替换。 |
| 2026-10-23 | Claude（主会话 89da7fbc） | 接手时核对文件实况：`PRD-v2.1-wip.md` 1591 行、§0–§16 齐全，已含用户在 fork 中提出的「前端不锁定、接口文档写在 PRD 内」；第二轮报告 3 份在 `docs/reviews/`，其中 C05、C06 已标修复，Codex 昨晚另有部分修订。主会话自己的 `PRD-v2-draft.md`（1503 行，§0–§9，按第一轮审核改过，前端写得过细）**已被 v2.1 取代，不再修改**，保留待最终清理。决定以 v2.1 为唯一底稿，按用户要求启动第三轮多 subagent 审核（逐条复核第二轮发现 + 找新问题），报告写 `docs/reviews/round3-*.md`。 |
| 2026-10-23 | Claude（主会话 89da7fbc） | 第三轮审核已启动 4 个 subagent（只写各自报告）：round3-closure（逐条复核第二轮 + 闭合一致性）、round3-security（红队）、round3-feasibility（账本、并发、L1、计划可行性）、round3-contracts-facts（接口覆盖六场景 + 事实核对）。审核期间不改 `PRD-v2.1-wip.md`。 |
| 2026-10-23 | Claude（审核 subagent） | round3-closure 完成（`docs/reviews/round3-closure.md`）。第二轮 18 条：已修 7（S2-01、C06、consistency C1/C3/C4/C5/C7），部分修 6（S2-02、S2-03、C02、C04、C05、consistency C6），未修 5（C01、C03、C07、C08、consistency C2）。新问题 20 条（P0 0、P1 9、P2 11），要点：409 STATE_CHANGED 后 L1 重新 evaluate 会作废 L2 已复演的新审批；L1 图缺 202 UNKNOWN 与多种 409/422/503 的去向；§7.10 「语义相同保留确认」与 HMAC 绑定冲突；商户扣费缺执行时刻（第 6 幕数字依赖它，建议统一 06:00，修后第 2 幕 295,500、第 6 幕 1,195,500 复算成立）；回放正则漏 src_/evl_ 等前缀；未指定源账户时的意图比对；取消定时转账等级 T-3 与 W4 矛盾；S 回调未登记；strict 哨兵回退与 P0 实验缺通过标准。没有运行代码。 |
| 2026-10-23 | Claude（审核 subagent） | round3-feasibility 完成（`docs/reviews/round3-feasibility.md`）：P0 3、P1 9、P2 9。P0：`arg_sources` 是自由键映射，写不成 strict Schema（需改为数组、派生只按模板）；回放键使 B0/B1 必然未命中（录制需按 profile 分开，漏 src/evl/hnt 前缀，`*_wall` 进了提示）；§13 日程做不完（报告给替换表：四线并行、10-30 冻结、11-01 缓冲）。P1：写事务隔离级别未定（建议 READ COMMITTED + 锁 + 版本）；advisory lock 跨 HTTP 持有会耗尽连接池；bankcore 缺统一 `load_bank_state` 与可持久化 `LedgerEffect`；L3 强制与 L2 策略的分工矛盾；7 天预留算法与扣费时刻（建议 08:00）；助手令牌放 `configurable` 可能进 checkpoint【待核实】；STATE_CHANGED 后 L1 重复 evaluate；D6 网关层评测缺准备接口；削减顺序与 DoD 不一致。静态审核，没有运行代码。 |
| 2026-10-23 | Claude（审核 subagent） | round3-security 完成（`docs/reviews/round3-security.md`）：P0 0、P1 9、P2 7。按现规范未找到不经确认面板的资金流出路径；round2 S2-01~03 均闭合。剩余风险集中在「授权来源洗白」：复述 / 引用注入内容（需引号与疑问句不产生作用域、`echoes_untrusted` 升红或 DENY、助手回复长数字掩码）；澄清补答自动合并（需显式 `clarification_id` + L2 模板追问）；被替代的旧原话仍可引用（需 status 与 JWT `utts`）；hint 可扩权、按钮文字可错配；L1 预填洗白到 app_form；RISKY_SEQUENCE 可换序或用无卡储蓄绕过；A2/A5/A7 判定不全；「没用的订阅」等谓词需 L2 确定性求值。P2：view_hash 唯一判据、BLOCKED 事件重放、`target_fingerprint` 未定义、memo 外泄、PIN 与 evaluate 频控、DEMO/EVAL 护栏、CSP。推演所得，未实测。 |
| 2026-10-23 | Claude（审核 subagent） | round3-contracts-facts 完成（`docs/reviews/round3-contracts-facts.md`）：P0 0、P1 8、P2 11。29 个需求 ID 均有接口；第 1/2/3/5/6/7 幕数字验算自洽；§6.6 DeepSeek 官方事实当日复核一致。P1：`bind_tools(strict=True)` 只有在默认 `api_base=…/v1` 时才自动切 /beta，§6.6 的 base_url 写法会导致 strict 不生效；409 STATE_CHANGED 错误体缺新 Decision、202 UNKNOWN 无出边；app_form 的计划形状未定义（`action` 来源、`form_action_id` 命名不一致）；runner 拿不到 PIN、不该持 F 令牌；`/internal/scheduled/authorize` 未在 §9 登记；UICard 与卡片动作模型缺失；三个状态枚举缺失；§4.5「AEGIS 不模拟业务状态」超出摘要。P2 含若干字段缺口与 V-INV、FIDES、Progent、BL-7、Bank of Anthos 的表述过度。 |
| 2026-10-23 | Claude（主会话 89da7fbc） | 第三轮 4 份报告到齐，合计 76 条（P0 3，均为可实现性；安全无 P0），去重后约 55 条。开始据此修订 `PRD-v2.1-wip.md`，修订前已备份到会话 scratchpad。 |
| 2026-10-23 | Claude（主会话 89da7fbc） | 完成第三轮修订（1,591 → 1,756 行），新增决议 D30–D46 记入 PRD §0.5。主要改动：L1 路由表覆盖全部返回码，STATE_CHANGED 直接用 `details.decision`；`arg_sources` 与 slots 改数组、派生只按模板；助手令牌改 LangGraph 运行时 context；ChatDeepSeek 显式设 /beta；P0 实验 E1–E6 给通过标准；回放按 profile 录制、只跑 B1/B2 端到端；授权来源防洗白（引号与疑问句、复述检测、原话替代 + JWT `utts`、hint 只收缩、按钮文字只取 L2、禁止 L1 预填、app_form 值级污点、memo 敏感）；RISKY_SEQUENCE 顺序不限 + 无卡账户限额；谓词目标 L2 求值；7 天预留算法与 06:00 扣费时刻；bankcore `load_bank_state` / `LedgerEffect`、L3 强制与 L2 策略分表；READ COMMITTED + 锁连接规则 + 审计锁命名空间；grant 缺失与失效区分、事件终态防重放；授权证据判别联合；补 AppFormPlanRequest、UICard、状态枚举、G22/G22a、S1、D0/D6a/D11/D12；日程改四线并行 + 10-30 冻结 + 缓冲；削减表与 DoD 豁免对应；事实措辞按核对报告收窄。没有运行任何代码。随后启动修订复核 2 个 subagent（核对 76 条是否修复、查修订引入的新矛盾），报告写 `docs/reviews/round3-verify-*.md`。 |
| 2026-10-23 | Claude（审核 subagent） | round3-verify-fixes 完成：第三轮 87 条中已修复 77、部分修复 4、未修复 0、修复引入新问题 6（均非 P0）。要点：§7.12 执行路径与非执行路径审计不互斥，哈希链会分叉；§15 仍要求 B2 对 B0 的端到端 TSR 对比，但 B0 已不跑端到端；PIN 只给 EVAL runner 而 DEMO/EVAL 互斥，`make demo` 拿不到 PIN；削减表第 2 级等于没削、第 3 级「频控」会误削 PIN 锁定；`instruction_runs` 缺 SKIPPED_REAPPROVAL；§9.9 D 分组计数错；secrets 生成的 hnt/act/clr 与 UUIDv5 派生 ID 的回放正则；F4 仍用 G18 令牌恢复执行；取消词后的计划/审批状态；§4.3 残留无证据说法。静态核对，没有运行代码。 |
| 2026-10-23 | Claude（审核 subagent） | round3-verify-regressions 完成：修订新引入 18 条（P0 3、P1 7、P2 8）。P0：复述检测把收款人名称与金额也纳入比对，几乎所有点名转账都会升红，且 L2 拿不到助手回复；授权证据「逐字节相同」使同一 plan_id 下的复演与重新审批必然 IDEMPOTENCY_CONFLICT；「这笔可疑交易对应的卡」在 G3 时会话无异常结果且张三有 3 条异常，X-4 与第 7 幕走不通。其余见报告。演示算式复算无误。 |
| 2026-10-23 | Claude（主会话 89da7fbc） | 按两份复核报告修订约 22 条：复述检测只比对 ≥8 位账号与 untrusted 自由文本、L2 不读助手回复；收款人匹配精确优先；`execution_intents` 加 `attempt` 与 `evidence_history`，复演 / 重新审批以 attempt+1 重新 prepare；「可疑卡」由 L2 自跑异常检测按卡集合判定；产品表加 `risk_label`，X-3 预期补 clarification；F4 用 BFF 保存的 G3 令牌恢复；§15 / G5 的 TSR 对比改为网关层 B2 vs B0 + 端到端 B2 vs B1；削减第 2、3 级修正；审计全部取同一把锁 + UNIQUE 兜底；证据联合加 DEMO_EVENT；SKIPPED_REAPPROVAL / RETRYING 同步；§6.1.1 补 POLICY_CHANGED、ALLOW、401/403/404 与兜底行；`load_bank_state` / `persist` 归 L3；澄清字段与 `parse_status`；`arg_sources` 线上格式为数组；回放正则覆盖 UUIDv5 与 secrets ID；取消词后的状态；`make demo` 取得 PIN；若干计数与措辞。没有运行代码。启动最后一次核对 subagent（`docs/reviews/round3-verify-final.md`）。 |
| 2026-10-23 | Claude（审核 subagent） | round3-verify-final 完成：两份复核报告 28 条中已修复 27、部分修复 1，无 P0；剩 3 条 P1 阻塞（重新审批的 deadline 只能缩短导致过期；「可疑卡」30 天窗口使第 7 幕的 3 条异常落在窗口外；复述检测只看本会话读取记录，可经新会话或 App 详情页复制绕过）与 5 条 P2；结论「改完这 3 处后定点复核即可替换」。 |
| 2026-10-23 | Claude（主会话 89da7fbc） | 修完上述 3 条 P1 与 5 条 P2（`deadline_cap_wall` 与 attempt 规则、UNKNOWN 重试沿用原 attempt；可疑卡窗口改为上一自然月起 + 种子断言；复述检测比对客户 L3 近 90 天不可信数据；X-3 clarification 只在 G3；令牌按 (sid, utterance_id) 保存；TSR 分层；`execution_jobs.l3_attempt`；补决议 D47），grep 定点确认已落盘。**替换定稿**：`PRD.md`（v1.2）→ `docs/archive/PRD-v1.2.md`；主会话作废草稿 `PRD-v2-draft.md` → `docs/archive/PRD-v2.0-draft-superseded.md`；`PRD-v2.1-wip.md` → `PRD.md`（1,764 行，状态改为定稿）。只移动、未删除任何文件。同步本文件 §2.10、§3、§5、§6、§7、§8.1、§8.3 中的 PRD 章节号与现状。没有运行任何代码。 |

| 2026-10-23 | Codex | 用户授权启动 P0，并要求每个组件完成即独立测试验收；已新增 §8.2 组件验收门禁，初始化 main Git 仓库。用户随后授权 P0 完成后创建 SJeffZhang 私有仓库并提交推送（默认 SafeBank-Gateway）。真实检查：GitHub 在沙箱外登录有效；Docker daemon 暂不可用；未发现项目 .env 或 DEEPSEEK_API_KEY，已请求用户在本地配置。 |

| 2026-10-23 | Codex | P0 组件 C01/C02 独立验收：Python 3.12.15 + uv.lock 安装成功；HTTP 骨架 2 测试通过（内部路由隔离、缺/错凭证拒绝、业务接口不存在）；密钥组件 2 测试通过（Ed25519 签验、0600 权限、角色凭证隔离、幂等与残缺目录拒绝）。本地 .env 已按用户提供凭证配置并忽略。Docker 报错复现为 Homebrew md5 不支持 -q，备份设置到 /private/tmp/sbg-docker-settings-backup.json 并关闭不需的 RequireVmnetd；后台引擎现可用（29.8.1）。用户要求不要再启动 Desktop 界面，后续仅后台 CLI。 |

| 2026-10-23 | Codex | P0 组件 C03–C06 验收：8 个 pytest 全通过、ruff check/format 通过；6 容器 healthy；真实网络/服务令牌/DB CONNECT 隔离全通过。首次构建 DNS 失败，改锁文件URL+哈希校验离线 wheel；DB默认库CONNECT越权测试失败后已修复并复跑通过。DeepSeek 原始300次：E1 180/180、E3/E4/E5各30/30通过，E2开启思考+指定工具30/30 HTTP400；独立关闭思考回退30/30通过，LangChain适配器live一次成功。内存LangGraph中断/恢复与runtime context不写checkpoint测试通过，Postgres saver待对应组件验收。创建ADR-001/002/003、third-party、服务README与验收证据。V1官网JS核实40/30/30评分、必交物、PPT≤10分钟/视频可选≤5分钟，已同步PRD。未运行银行业务/并发/ASR测试（尚未实现）。 |

| 2026-10-23 | Codex | P0 最终复验 make test/lint 通过（8 passed），make up + make test-integration 重跑通过。提交前65个暂存文件检查通过：未含实际API key、私钥、.env/.secrets/.wheels/.venv。已按用户授权创建 https://github.com/SJeffZhang/SafeBank-Gateway ，创建参数明确 --private；准备首次提交推送。P0证据见 docs/acceptance/P0.md，业务实现仍未开始。 |

| 2026-10-23 | Codex | 首次提交 ffcfe23 已推送 origin/main；GitHub 查询 visibility=PRIVATE。用户要求仓库名简化且首字母大写，已将 SafeBank-Gateway 改名 Safebank，本地 origin 同步 https://github.com/SJeffZhang/Safebank.git 。首次 CI 已完成且 conclusion=success（run 39912891423）；本地组件与集成验收也已通过。 |

| 2026-10-23 | Codex | 用户明确授权实施完整 P1；创建 feat/l3-core 分支，将计划写入 §5。按 P1.1→P1.8 顺序实现，组件验收前不铺后续实现；尚未通过 P1 测试，不提交或推送本轮更改。 |

| 2026-10-23 | Codex | P1.1：基础金融类型、不可变额外字段拒绝模型、枚举和PRD代码全集已落盘；.venv/bin/pytest tests/contracts/test_base.py -q：15 passed。覆盖bool/float/string金额拒绝、bigint边界、Decimal特殊值/精度、时间缺时区、JSON往返。尚未实现业务/DB。 |

| 2026-10-23 | Codex | P1.2：18读/14写参数和结果、计划/来源/DAG、不可变影子状态、领域效果与执行协议已定义。tests/contracts：82 passed。最终生产13工具strict Schema→DeepSeek V4 Pro三样本HTTP200，Schema及Pydantic校验全部通过（docs/acceptance/p1-contracts-live）。首次date-time注解被API拒绝，已在转换器去除；首次模型返回无效span被运行时拒绝后固定探针来源重测；未放宽源字段检查。实体kind/空值及渠道哨兵有双向转换。 |

| 2026-10-23 | Codex | P1.3：纯函数账本TB-1/2/3/5/9及确定性ID已实现；tests/bankcore/test_ledger.py：6 passed（含3种部分/全部确认参数）。守恒、原状态不变、不透支、幂等冲突、pending终结唯一/超时/释放均通过。已读TigerBeetle官方account/two-phase/linked文档，docstring记来源，不复制代码。DB尚未实现。 |

| 2026-10-23 | Codex | P1.4：SQLAlchemy[asyncio]2.1.6/asyncpg0.32.0/Alembic1.20.1精确锁定。独立bank_p1_test首次及重复upgrade head成功；真实Postgres tests/integration/test_database.py：4 passed，验证DDL/TRUNCATE拒绝、账户不透支/同账号约束、append-only触发器、readiness/隔离级别/连接失败。迁移凭证仅测试驱动持有，运行角色不是表owner。测试镜像白名单漏pyproject导致首次build失败已修复。未清空生产bank数据。 |

| 2026-10-23 | Codex | P1.5：唯一scope/load_bank_state/persist及独立p1_core_v1数据已落盘；tests/integration/test_state.py真实Postgres：2 passed，验证READ ONLY拒写、完整JSON往返、接收方联系方式脱敏、pending SQL与影子账户/账本一致及事务回滚无残留。seed用同一纯函数账本开户入金，未声称完整baseline_v1。底层ledger算术提取为共享account_deltas后6项账本回归通过；并发最终由P1.7/P1.8门禁验收。 |

| 2026-10-23 | Codex | P1.6：转账/新增收款人/卡限额渠道冻结解冻挂失补卡纯函数已实现，tests/bankcore：19 passed；真实Postgres test_state：3 passed（补卡/收款人/操作事件/outbox提交后重载一致）。新增/已有payee引用解析实际ID；外转结果不泄露接收方余额；本人互转不计累计。Docker后台socket中断，--help意外启动默认前端，已停止该次进程并显式 -with-frontend=false -unattended 启动后台；恢复后真实验收通过。 |

| 2026-10-23 | Codex | P1.7：prepare/execute/query/reconcile与原子结果/操作/outbox已实现。真实Postgres test_execution.py：6 passed，覆盖重试只记一次、提交后服务重建恢复、第二步失败/SQL故障全回滚、prepare前取消挡迟到、attempt/证据/不可变cap、版本/时钟冲突、锁等待过期与execute/reconcile竞争。修订prepare增加deadline_cap_wall：原PRD仅传min后的deadline无法实现重新审批上限；已同步PRD与ADR-006。并发20笔与HTTP阶段验收进入P1.8。 |

| 2026-10-23 | Codex | P1.8 进行中：真实 PostgreSQL 集成已 30 passed（新/复用收款人用例最初误传 strict 格式被拒，已修正测试为内部引用并通过）。新增真实 L3 HTTP 应用、后台迁移/独立测试凭证、核心 CLI 种子、并发/一致性 Make 目标和 CI 配置；锁超时、实际进程重启和网络最终门禁仍待跑，未标记 P1 完成。 |

| 2026-10-23 | Codex | P1.8 修复与补测：从空 bank_p1_test 迁移/重复升级及完整集成 32 passed；真实 HTTP、凭证/网络边界、实际 L3 restart 恢复已通过。修复首建触发器 f-string 与 bank owner 切换后的 CONNECT 授权；区分解析后 steps_digest 与原请求摘要（0002_identity、ADR-007）；固化历史迁移 DDL。重复 make up 暴露固定 IP 的临时种子容器冲突，改用动态 IP 维护容器；修复后最终回归待跑。 |

| 2026-10-23 | Codex | P1.8：完整P1最终门禁通过：make test=116 passed/33 skipped（集成仅由真实PG目标验收）；make lint通过；空bank_p1_test迁移+重复upgrade head通过；make test-p1-db=33 passed；make test-p1-concurrency=5 passed；make test-p1-consistency=17 passed；make test-integration真实网络/凭证/角色、HTTP转账及实际L3重启恢复通过；make contracts-check通过，冻结contracts v1。P1.8证据与限制见docs/acceptance/P1.8.md。CI已配置但本轮未提交/推送、未在GitHub实际运行；不进入P2。 |

| 2026-10-23 | Codex | P1 收尾文档：更新根/后端/共享包README、第三方6个新增依赖的wheel许可、PRD执行摘要与outbox消费约定、ADR-007、冻结v1 Schema与SHA256及CI漂移检查。扩展一致性测试首次8项失败因UTC与+08:00字符串表示差异，改为比较同一datetime业务时间点后全部通过；未忽略金额、状态、流水或操作事件。元数据脚本首次误用系统Python找不到asyncpg，已在.venv重跑。实际本地API key扫描未进入Git候选文件，.env/.secrets保持忽略。 |

| 2026-10-23 | Codex | P1最终总入口复跑：make test-p1 从空独立库重建、两次upgrade head、33项真实PG全部通过（14.88s）；116单元、lint和冻结契约复核通过，最终原始输出已落盘docs/acceptance/p1-final/。同步关键文件地图与常用命令，未运行远端CI，未提交或推送；P1结束。 |

| 2026-10-23 | Codex | 用户明确要求推送P1；提交前git diff --check与实际本地API/service key、数据库口令的Git候选文件扫描通过，.env/.secrets未混入。按功能分支feat/l3-core交付到SJeffZhang/Safebank；原有验收结果保持，未改变业务代码。 |

| 2026-10-23 | Codex | 暂存后git diff --cached --check发现新验收日志含Docker输出尾随空格，首次提交前检查因此终止，尚未产生提交；仅清理p1-final文本行尾空格后重试，未修改测试结果或业务代码。 |

| 2026-10-23 | Codex | P1功能提交7fd7a26已成功推送到私有仓库SJeffZhang/Safebank的origin/feat/l3-core，并建立upstream；未合并main、未创建PR。仅日志行尾空格规范化，未改变业务代码或既有验收结论。本条以文档提交同步交付状态，CI结果以GitHub实际运行记录为准。 |

| 2026-10-23 | Codex | 推送后只读检查远端CI：unit通过，p1-postgres在make up失败，两个Postgres容器均报告/docker-entrypoint-initdb.d/01-roles.sql: Permission denied；真实PG/并发/一致性后续CI步骤被跳过，不能宣称远端通过。运行记录：https://github.com/SJeffZhang/Safebank/actions/runs/39925600593。这是Linux容器读取宿主0600初始化脚本的权限问题，待后续修复；本次按用户指令完成推送，未改业务代码。 |

| 2026-10-23 | Codex | 用户要求查看CI报错；按gh-fix-ci技能只读复查3次push运行，unit/ruff/format均通过，p1-postgres均在make up因两库init SQL Permission denied失败，其余5个验收步骤跳过。读取现有后台官方entrypoint确认先降权再psql -f；诊断与保留0600的tmpfs权限适配方案写入docs/acceptance/CI-P1-diagnosis.md，尚未实施修复、提交或推送；不启动Docker Desktop。 |

| 2026-10-23 | Codex | 用户明确接受CI权限修复方案并授权推送；开始为双Postgres加入root启动阶段的私有tmpfs复制与postgres所有者0600适配，继续使用官方entrypoint。宿主.secrets权限不变、SQL不进镜像；将先用独立全新卷复现/验收，再跑P1门禁并检查远端CI，不操作现有银行卷。 |

| 2026-10-23 | Codex | CI权限修复独立验收通过：scripts.postgres_bootstrap_acceptance在临时原生Linux卷为bank/agent均复现旧0600外部owner错误；新wrapper的全新初始化、角色/CONNECT隔离、tmpfs内postgres-owner 0600/目录0700、重启保留哨兵数据均通过。只清理测试专属随机命名卷。新增make test-postgres-bootstrap并接入CI；普通P1回归和远端CI仍待跑。 |

| 2026-10-23 | Codex | CI权限修复本地总验收：make up全部healthy；make test-p1空测试库迁移+重复upgrade+33 passed；并发5 passed、一致性17 passed、网络/HTTP/L3重启恢复通过；116单元、lint、冻结契约检查通过。独立Linux卷测试同时复现旧失败、验证新启动与重启；宿主SQL/口令仍0600。README/后端说明及CI修复验收文档已更新，下一步提交并等待实际GitHub CI。 |

| 2026-10-23 | Codex | 用户授权的CI修复提交70dcabe已推送，GitHub运行https://github.com/SJeffZhang/Safebank/actions/runs/39929405919的unit/p1-postgres均success；远端实际通过全新Linux卷初始化回归、33项PG、5项并发、17项一致性、网络/HTTP/L3重启和冻结契约。机器可读步骤与日志摘录保存docs/acceptance/ci-bootstrap/github-result.json，Linux首次部署权限问题关闭。最后仅同步交付文档，使用[skip ci]避免重复跑相同代码；不合并main、不创建PR、不进入P2。 |

| 2026-10-23 | Codex | 按用户要求核对记录并统计P1规模：截至已推送54c2f58，相对P0基线c3bd537，核心Python3933行（contracts1718、bankcore668、L3含迁移1547），测试/样本1836行，Python工具脚本新增587行及Shell14行；合计新增代码6370行（含注释/空行，脚本另删1行）。107个变更文件，共新增30415/删除40行，主要额外体积来自生成Schema/文档/证据23659行，不能当业务代码量。§3/§4/§5及验收目录已覆盖P1各组件、失败修复、推送和最终CI通过；本次只补充统计说明，未修改业务、未新提交推送。 |

| 2026-10-23 | Claude（主会话 89da7fbc） | 验收 P1 分支 `feat/l3-core`（用户要求以 CI 为准）。核实：GitHub 上此前**没有 PR**，只有已推送分支；CI 运行 39929405919（提交 70dcabe）unit 与 p1-postgres 两个 job 全部 success，6 个验收步骤（bootstrap、make up、test-p1、并发、一致性、integration、contracts-check）均实际执行；HEAD 54c2f58 为 [skip ci]，只改 5 个文档文件。抽查 PRD 约束：bankcore 无 IO；`load_bank_state` / `persist` 仅在 L3 `app/state.py`；写事务 READ COMMITTED、快照 REPEATABLE READ；`execution_intents` 含 attempt / deadline_cap_wall 与 CHECK；客户账户余额边界与累计量非负 CHECK；20 笔并发、同计划 20 次重试、双向互转与冻结竞争、超额并发、pending 竞争测试齐全；金额代码无 float。本机未重跑测试。注意：P1 种子为测试用 `cus_a/cus_b`，PRD §2.2 的完整演示种子留待后续阶段。验收通过，提交本日志后开 PR 并合并入 main。 |
| 2026-10-23 | Claude（主会话 89da7fbc） | 日志提交 3b3badb 推送到 `feat/l3-core`；创建 PR https://github.com/SJeffZhang/Safebank/pull/1 并以 merge commit 合入 main（d22b855，2026-10-23T10:11:43Z）；本地切到 main 并 fast-forward。`feat/l3-core` 分支保留未删。本条日志尚未提交。 |
| 2026-10-23 | Claude（主会话 89da7fbc） | 用户要求：L2 阶段由主会话只做指挥，具体开发交给本机 Claude Code CLI（已在 `~/.claude/settings.json` 接入 DeepSeek）并行完成。核实：`claude` 2.1.307 可用；主会话环境继承了桌面 App 的 `ANTHROPIC_BASE_URL=api.anthropic.com` 与凭证，直接启动会走 Anthropic 账户，故启动 worker 前必须 `env -u` 清掉 `ANTHROPIC_*`；清掉后无头调用实测 `modelUsage=deepseek-v4-pro[1m]`（CLI 提示 unrecognized_model、自报费用按 Claude 价估算，不代表 DeepSeek 实际费用）。用户决定前端暂停：写入本文件 §2 第 13 条与 PRD §13。新建 `docs/plans/L2-plan.md`（W0 骨架 → 波次 1 四路并行 W1–W4 → 波次 2 W5/W6；worktree 隔离、命令白名单、进度写 `docs/progress/`、并发测试隔离、合入 main 前请用户确认）。尚未启动任何 worker。 |
| 2026-10-23 | Claude（主会话 89da7fbc） | 用户确认「先骨架后并行」，并强调**稳健与架构合理优先于并行**：写入 L2-plan §0 最高原则，并在 W0 完成后增加架构评审关卡（指挥审查 + 1 个只读评审 subagent，报告 `docs/reviews/L2-W0-architecture.md`，通过后才开波次 1）。从 main 建集成分支 `feat/l2-gateway`，提交 5104dab（计划、前端暂停、P1 合并日志）与 251bcd0（W0 任务书 `docs/plans/briefs/W0-foundation.md`）。建 worktree `../Fintech2026-wt/W0-foundation`（分支 `feat/l2-foundation`），后台启动 W0 worker（`claude -p`，清除继承的 ANTHROPIC_* 变量走 DeepSeek V4 Pro，acceptEdits + 命令白名单，不可 push），日志在会话 scratchpad `workers/W0.*`。进程已确认运行。 |
| 2026-10-23 | Claude（主会话 89da7fbc） | 应用户要求新增 `scripts/watch-worker.sh`：跟踪 worker 在 `~/.claude/projects/…-Fintech2026-wt-<名>/*.jsonl` 的实时记录并用 jq 转成可读行（`--output-format json` 的日志要到结束才写出）。观察到 W0 有几条带 `;` 的组合命令被白名单拒绝，worker 已改为单条命令继续。脚本未提交。 |
| 2026-10-23 | W0 worker（DeepSeek V4 Pro，无头 Claude Code） | 完成 W0 骨架：本地提交 378ba67（分支 `feat/l2-foundation`，50 文件 +2952/−6，265 轮、30 分钟）。自报 lint、test-gateway 40、test-gateway-db 4、全量 156 passed / 37 skipped。待决 4 条：gateway 迁移引导需仿 `scripts/p1_database.py`（超出 W0 可改范围）；requirements / .wheels 未重生成，镜像缺新依赖；包名改为 `services.gateway.app` 以免与 L3 的 `app` 冲突；PRD 未列枚举的 status 字段未加 CHECK。进度见 worktree 中 `docs/progress/W0-foundation.md`。 |
| 2026-10-23 | Claude（主会话 89da7fbc） | 在 W0 worktree 复跑退出命令，结果与自报一致（lint 通过；40 / 4 / 156 passed，37 skipped）。启动架构评审关卡：派只读评审 subagent，报告写 W0 worktree 的 `docs/reviews/L2-W0-architecture.md`。评审通过前 W0 不合并、波次 1 不启动。 |
| 2026-10-23 | Claude（审核 subagent） | W0 架构评审**不通过**（报告：W0 worktree `docs/reviews/L2-W0-architecture.md`，P0 7、P1 7、P2 4）。实测发现：fake L3 的 timeout 实际返回 200、drop 异常未转换、fake 不校验令牌；框架 404/405 不是统一错误体；双 aud 的 JWT 能冒充 assistant 令牌、scope 字符串被拆成字符；requirements.txt 缺 PyJWT。P0：EvalContext 缺阶段间数据位；DENY 短路跳过决策阶段；Decision / PlanView / scope 等共享模型未定义；路由集中在 main.py、矩阵按字面路径匹配且未接入应用；状态迁移与审批 / 复演接口缺占位，W3 与 W4 无法并行；幂等只在内存；运行镜像缺依赖，`make up` 会失败。 |
| 2026-10-23 | Claude（主会话 89da7fbc） | 按「稳健优先」拍板：P0-5 在 W0 里补接口占位（不靠推迟 W4 规避）；波次 1 期间 worker 不写迁移，由指挥串行加 `0002_wave1`；阶段归属 ①–④ → W2，⑤–⑨ 与 G4 → W3；② 所有权用 EvalContext 上惰性缓存的 L3 状态访问器；`ExecutionJobStatus` = EXECUTING/UNKNOWN/EXECUTED/FAILED/CANCELLED，`WorkflowEventStatus` 用评审建议值（待补进 PRD §7.2）；允许 W0 改 `scripts/` 做 gateway 库引导并接入 `make up`。写入 W0 worktree `docs/plans/briefs/W0-round2.md`，用 `--resume` 续接原会话启动第二轮（日志 `workers/W0r2.*`）。注意：compose 项目名固定为 `safebank`，worker 的 `make up` 会重建本机正在运行的 P1 栈并在 `safebank_pg-bank` 卷中新建 gateway 表与角色；任务书禁止删卷。 |
| 2026-10-23 | W0 worker（第二轮） | 提交 3059b53（累计 81 文件 +5256/−14，第二轮 204 轮、26 分钟）。自报 P0-1–P0-7、P1-8–P1-14 全部处理：EvalContext 类型化结果槽 + 惰性 L3 状态访问器；DENY 只短路 ⑤⑥⑦、⑧⑨ 总是运行；`app/schemas/` 共享模型与枚举；`app/api/*` 路由占位 + 矩阵全表与覆盖检查；`state.py` 状态迁移唯一入口与交叉接口占位；`request_dedup` 持久幂等；防漂移测试；有状态 fake L3 与三分类客户端异常；统一 404/405/500；JWT 严格校验；`scripts/gateway_database.py` 引导接入 `make up`；审计哈希覆盖时间戳。工具注册表 `tools.yaml` / policy registry 未建，自称留给 W2。 |
| 2026-10-23 | Claude（主会话 89da7fbc） | 复跑第二轮：lint 通过；test-gateway 52、test-gateway-db 14、全量 170 passed / 45 skipped；6 服务 healthy。**发现**：`safebank_pg-bank`、`safebank_pg-agent` 两个卷创建于 2026-10-23T11:29Z，即 worker 删除并重建了本机 P1 栈的数据卷（仅合成测试数据，`make up` 会重新 seed；任务书「不要删除任何数据卷以外的东西」措辞有歧义，属指挥疏忽，后续任务书改为明确禁止删卷）。worker 在本 worktree 生成了新 `.secrets`，主 checkout 的旧 `.secrets` 与新卷不匹配，集成分支需 `make keys && make up`。已用 SendMessage 请原评审 subagent 复审，并要求判断工具注册表缺失是否阻塞。 |
| 2026-10-23 | Claude（主会话 89da7fbc） | 用户决定合并流程：**每完成一个完整组件、测试通过就开 PR（base `main`），逐步审核，CI 通过即合并**；作为长期授权，满足「审核通过 + CI 全绿」不再逐个询问。写入 L2-plan §2.5；集成分支 `feat/l2-gateway` 只承载计划文档，随 W0 的 PR 合入后不再使用，后续 worktree 从最新 `main` 拉出。 |
| 2026-10-23 | Claude（审核 subagent） | W0 第二轮复审**仍不通过**（追加于 W0 worktree `docs/reviews/L2-W0-architecture.md`「第二轮复审」）。已修复 P0-2/6/7、P1-9/10/11/12；部分修复 P0-1/3/4/5、P1-13。阻塞：工具注册表必须在 W0 补（W1/W3/W4 都依赖）；公共路由未接鉴权（探针 A：无凭证调 `/plans/evaluate` 得 503 而非 401）；`transition` 不执行合法迁移表；交叉接口占位只有文字没有代码。P1：`bank_state()` 一调用就 AttributeError（探针 B）；+08:00 时钟下审计链误报篡改（探针 C）；`request_dedup` 异常后永久 409、错误码误用；阶段结果类型归属与 `steps` 字段。 |
| 2026-10-23 | Claude（主会话 89da7fbc） | 写 W0 第三轮任务书（W0 worktree `docs/plans/briefs/W0-round3.md`），要求修完上述阻塞与 P1、把探针 A–D 写成正式测试、**禁止删除或重建任何数据卷**；`--resume` 续接 W0 会话启动第三轮（日志 `workers/W0r3.*`）。本轮白名单收窄 docker 命令，并用 `--disallowedTools` 屏蔽 `docker volume`、`docker compose down -v`、`docker system prune`。 |
| 2026-10-23 | W0 worker（第三轮） | 提交 c691e15（32 文件 +937/−49，69 轮、8 分钟）。自报：工具注册表 `policies/tools.yaml` + `app/policy/registry.py`（14 个写工具、卡类 TIGHTEN/LOOSEN）；public_app 全局接入 `caller_dependency`；`transition` 执行合法迁移表并补齐复演 / 重新审批状态边；交叉接口占位 `approval/{service,planview}.py`、`evaluation/repo.py`、`locks.py`、`execution/replay.py`；`bank_state()` 修复；审计时间统一 UTC；幂等异常清理与 `EXECUTION_IN_PROGRESS`；`EvalContext.steps`；探针 A–D 写成测试。未删卷。 |
| 2026-10-23 | Claude（主会话 89da7fbc） | 复跑第三轮：lint 通过；test-gateway 59、test-gateway-db 17、全量 177 passed / 48 skipped。`safebank_pg-bank` 卷创建时间仍为 11:29Z，确认本轮未重建。已请原评审 subagent 做第三轮定点复核。 |
| 2026-10-23 | Claude（审核 subagent） | 第三轮定点复核：**有条件通过**。探针 A–D 重跑通过，第二轮阻塞项与 P1 全部修复。开工前须修：R1 `serve()` 未加载 JWT 公钥；R2 pyyaml 只在 dev 依赖，运行镜像会缺；R3 `action_kind` 找不到卡时默认 TIGHTEN（fail-open，实测限额调到 99,999,999 分也判收紧）。P2：R4 outflow 口径、R5 app_form 快照步骤与 replay 用新 EvalContext、R6 幂等清理掩盖原始异常。并给出更新后的 10 条接口约定（写入 W0 worktree 评审报告）。 |
| 2026-10-23 | Claude（主会话 89da7fbc） | 裁定 R4（依 PRD §5.0）：只有 `TRANSFER_EXTERNAL` 与目标为他人的 `schedule_transfer` 计为对外转出，本人互转、申购、赎回不计入。写 W0 第四轮任务书（R1–R4），`--resume` 启动（日志 `workers/W0r4.*`），删卷命令继续屏蔽。R5、R6 写入波次 1 任务书。 |
| 2026-10-23 | W0 worker（第四轮） | 提交 7271e27（15 文件 +323/−29）：Settings 增加 `jwt_public_key_path`，缺公钥拒绝启动；pyyaml 6.0.3 移入主依赖并重导 requirements 与 wheels；`action_kind` 找不到卡抛 `LookupError`；新增 `registry.outflow()`，只有 TRANSFER_EXTERNAL 与目标为他人的 schedule_transfer 计对外转出。未删卷。 |
| 2026-10-23 | Claude（主会话 89da7fbc） | 复跑第四轮：lint 通过；test-gateway 64、test-gateway-db 17、全量 182 passed / 48 skipped；6 服务 healthy；卷未重建。W0 判定可开 PR。在 W0 分支提交 7a3b39a：PRD §7.2 补 `execution_jobs.status`（EXECUTING/UNKNOWN/EXECUTED/FAILED/CANCELLED）与 `workflow_events.status`（PENDING/COMMITTED/BLOCKED），§11.1 补 `JWT_PUBLIC_KEY_PATH`、`BANK_BACKEND_URL`；把 `feat/l2-gateway` 合入 W0 分支（bf0c1dc）。 |
| 2026-10-23 | Claude（主会话 89da7fbc） | 扫描改动无 `.env` / 私钥 / 硬编码密钥后推送 `feat/l2-foundation`，开 PR https://github.com/SJeffZhang/Safebank/pull/2 并绑定本会话。CI（run 39949731463）：unit 通过，**p1-postgres 在 `make up` 失败**——gateway 容器退出：`cannot read JWT public key at /run/secrets/jwt-public.pem: Permission denied`。原因同 P1 CI 故障：`scripts/keys.py` 以 0600 写全部密钥，Linux 容器非 root 用户读不到只读挂载；macOS Docker Desktop 权限宽松故本地未复现。由 W0 第四轮新增的「启动加载公钥」引出。按流程不合并；`--resume` 让 W0 worker 定点修复（公钥改 0644、私钥保持 0600、加权限断言），日志 `workers/W0ci.*`。同时在会话 scratchpad `briefs/` 起草了波次 1 公共约定与 W1–W4 四份任务书，待 W0 合并后放入各 worktree。 |
| 2026-10-23 | W0 worker（CI 修复） | 提交 b8836b5（2 文件 +30/−3）：`scripts/keys.py` 公钥以 0644 写入，已存在的公钥在 `make keys` 时修正为 0644，其余密钥保持 0600；`tests/test_keys.py` 断言权限。确认 agent 与 gateway 挂载同一公钥，无需改 compose。 |
| 2026-10-23 | Claude（主会话 89da7fbc） | 复跑 lint、test-gateway 64、全量 183 passed / 48 skipped；推送 b8836b5 触发 PR #2 的 CI 重跑。本机 `.secrets/jwt-public.pem` 仍为 0600（未重跑 `make keys`），CI 每次新生成密钥不受影响。 |
| 2026-10-23 | Claude（主会话 89da7fbc） | 应用户要求为 PR #2 开启 Auto-fix（CI 失败、合并冲突、评审意见会唤醒本会话；CI 通过不会唤醒）。重跑中：unit 2 项通过，p1-postgres 2 项进行中，暂无失败。 |
| 2026-10-23 | Claude（主会话 89da7fbc） | 应用户要求为 PR #2 开 GitHub 自动合并（merge commit），失败：仓库未启用「Allow auto-merge」。改仓库设置需用户同意，已询问。 |
| 2026-10-23 | Claude（主会话 89da7fbc） | 经用户同意，`gh api -X PATCH repos/SJeffZhang/Safebank -F allow_auto_merge=true` 打开仓库「Allow auto-merge」；随后为 PR #2 开自动合并仍被拒（PR 处于 unstable：`main` 无分支保护 / 必需检查，检查未跑完时 GitHub 不接受）。复查时 CI 已全绿（4 项通过，CLEAN，head b8836b5），按用户「CI 全绿即合并」规则直接合并 PR #2（merge commit e2e91db，2026-10-23T12:07:45Z），本地切到 main 并 fast-forward。**W0 基础骨架合入 main。** 用户要求做完此 PR 后停止工作：波次 1 未启动；四份任务书草稿（`wave1-common.md`、`W1-identity.md`、`W2-provenance.md`、`W3-decision.md`、`W4-execution.md`）已复制到 `docs/plans/briefs/`，未提交；恢复时随各 W 分支提交。W0 worktree `../Fintech2026-wt/W0-foundation` 与分支 `feat/l2-foundation`、`feat/l2-gateway` 保留未删。 |
| 2026-10-23 | Claude（主会话 89da7fbc） | 用户决定把后续工作转到 Codex（只有 GitHub 仓库，在 Codex 内自开子 agent）。写交接文档 `docs/handoff/CODEX-HANDOFF.md`；更新本文件 §3（新增 L2 网关、前端两行，下一步改为波次 1）与 §5（新增「L2 进度」表）；与波次 1 公共约定及 W1–W4 四份任务书一起，经分支 `docs/codex-handoff` 开 PR https://github.com/SJeffZhang/Safebank/pull/3 。主会话就此停止工作。本机遗留：W0 worktree、P1/W0 的 Docker 栈仍在运行（用的是 W0 worktree 生成的 `.secrets`）。 |

| 2026-10-23 | Codex（协调者） | **Codex 接手**：按用户指令克隆唯一来源 SJeffZhang/Safebank 到 `/Users/jet/Downloads/WeBank`；GitHub 连接器返回 404，改用本机 gh 实查 PR #3 已 MERGED（merge `8555bf0c82860a11765d73f2a59f8e72d6a5de7a`），unit 与 p1-postgres 全部 SUCCESS，无需再次合并。已按序完整阅读交接文档、AGENTS.md、L2-plan、wave1-common、W1–W4 任务书、gateway README 与 W0 架构评审；继续核对 PRD。初次克隆因 `.git` 写保护失败，权限审核后成功。`make setup` 首次因沙箱 DNS 限制失败，正在联网重试，未宣称基线通过。本机 Docker 链接失效且 Docker.app 缺失；用户已选择安装 Docker Desktop 后仅后台运行。未启动业务 worker、未操作 Docker 数据卷。 |

| 2026-10-23 | Codex（协调者） | 接手准备继续：`make setup` 联网重试成功（78 个锁定包），`make keys` 成功且 .secrets/.venv 已忽略；亲自跑 `make test`=183 passed/48 skipped、`make test-gateway`=64 passed、`make lint` 通过（145 files）。fetch 后 main 仍为 8555bf0。通过原生工具建立并附加四个独立 worktree，创建 feat/l2-identity、feat/l2-provenance、feat/l2-decision、feat/l2-execution。启动计划、退出命令、接口核查和合并顺序写入 docs/plans/briefs/wave1-codex-launch.md。Docker Desktop 正在按用户选择安装；make up / 真实 PG 基线尚未运行，业务 worker 未启动。运行时最多 3 个子 agent 同时执行，四任务分批派发；不使用旧任务书的虚假 DeepSeek/Claude 提交署名。 |

| 2026-10-23 | Codex（协调者） | 环境准备：Homebrew 下载 Docker Desktop 4.96.3 并将 Docker.app 放到 /Applications；系统级 CLI 链接步骤因需要交互式 sudo 失败，`--no-binaries` 重试因应用已存在结束。应用内 CLI 实测可用（Docker 29.9.2、Compose 5.6.1），已以 `-with-frontend=false -unattended` 启动后台引擎，docker info 返回 29.9.2；未打开 Desktop 窗口。四个 worktree 分别从缓存离线安装 78 个锁定包，`make wheels` 校验 44 个 Linux wheel 成功。初始 docker volume ls 为空，未删除/重建数据卷。正在串行运行用户要求的完整 main 基线命令链；当前镜像构建及首次 PostgreSQL 镜像下载中，尚未宣称全栈/PG 通过。 |

| 2026-10-23 | Codex（协调者） | **main 完整基线验收通过**（代码 8555bf0，只有协调文档未提交）：按顺序实际运行 make setup、make keys、make up、make test、make test-gateway、make test-gateway-db，命令链 exit 0。全量 183 passed/48 skipped，gateway 64 passed，真实临时 PostgreSQL 17 passed（4.95s）；lint 此前已通过。六个既有服务 healthy；首次创建 safebank_pg-bank / safebank_pg-agent 于 2026-10-23T12:26:36Z，未删除/重建任何数据卷。准备派发 W1/W2/W3，W4 按槽位分批接入。静态发现 bankcore 尚无 reserve/shortfall 实现，交 W3 核对并登记共享依赖，不允许在 gateway 重写业务算术。 |

| 2026-10-23 | Codex（协调者） | 已派发独立子 agent W1/W2/W3（各用其 feat/l2-* worktree，仅本地提交），W4 排队；共同确认 app.state.db=AsyncEngine、l3=L3Client、wall_now=带时区时钟函数，不在各处理器重复创建连接池。W1/W2 对齐 NormalizedScope.fields 中普通参数、predicates 和 derived_template；no_op 保留在 bankcore 预演结果，不塞入写入参数。W3 获准按原有所有权仅给 PreflightResult/WorkflowResult 新增字段，EvalContext 不变。核实并登记共享缺口：撤销检查未接全局依赖、pending_clarifications 缺状态边、运行资源/私钥/PIN 未接线；bankcore 仅实现 7/14 写工具，reserve/projected_shortfall 与原始账单快照缺失。各 worker 不改冻结区，替身和503不计完整组件验收。正在编写具体补缺提案，尚未扩大实现范围、未提交/推送/开PR。 |

| 2026-10-23 | Codex（协调者） | 已按实际代码与 PRD §7.7 编写 docs/plans/briefs/wave1-shared-gaps.md：列出全局撤销、澄清状态、运行资源/密钥、预留与内部快照、7 个尚未实现的领域写工具及验收门禁。用户明确冻结与完整退出条件存在冲突，准备请求一次范围受控例外；当前仅文档提案，未改冻结区、未放宽验收。W3另发现FIRST_TRANSFER历史与拒绝发生时间语义，优先复用已有数据，缺字段仍进0002待决。 |

| 2026-10-23 | Codex（协调者） | 共享依赖提案已通过异步问题送用户选择，等待明确冻结例外决定，不阻断白名单内工作。W3复核确认L3 transactions加载全历史，撤回新增lifetime字段提议；拒绝窗口复用evaluations与APPROVAL_REJECTED审计时间。append_evaluation全结论构建view并存hash，固定签名不变；挂失/申购的IRREVERSIBLE_STEP升红与工具展示irreversible分开核对。交叉约定已补入启动记录供W4接入。未新增业务变更、未声称组件通过。 |

| 2026-10-23 | Codex（协调者） | 继续静态核验发现 P1 是核心子集：L3仅5/18读，生产load_bank_state未填指令/代扣/产品/持仓/订单（模型字段默认空）；已提醒W1/W2修正自报，不能以FakeL3全样本当生产齐备。reads.py有效风险测评分支引用未导入date，已登记冻结区缺陷。初次rg查询含不存在api.py返回exit2，随后读实际reads.py成功；补充共享缺口提案，未修改业务文件。 |

| 2026-10-23 | Codex（协调者） | 用户明确批准「允许受控补缺，按提案逐组件推进」。按wave1-shared-gaps实行仅共享实现agent可改的受控例外，W1–W4边界不变；安排W3在当前小组件稳定点本地提交并报告阻塞，腾槽优先共享网关运行/撤销/状态/密钥接线，后续预留与L3领域组件逐项独立评审验收。已建立本次计划专属忽略目录.superpowers/sdd/wave1-codex-launch作恢复账本，不替代§4正式日志。 |

| 2026-10-23 | Codex（协调者） | 建立并附加共享补缺worktree safebank-shared-runtime，分支feat/l2-shared-runtime（基线8555bf0），离线安装78包。编写S1-shared-runtime任务书，限定状态/生命周期/撤销接线/Linux私钥读取，不改W1业务或银行领域。按已批准方案应用brainstorming/writing-plans技能整理，无需重复确认。另裁定W2保护性动作任务书与PRD冲突以§7.6为准：越出授权范围仍REPLAN，不能借“保护”放行；memo/alias黄提示缺码单独处理。 |

| 2026-10-23 | Codex（协调者） | W3以PARTIAL/BLOCKED提交检查点3bcbe8e后结束本轮，已派独立S1共享实现agent。协调者亲自复跑W3全部五条退出命令：lint通过151 files；gateway76 passed（0.93s）；既有真实PG17 passed（2.76s）；全量195 passed/48 skipped（1.34s）；test-w3 12 passed（0.35s）。这些只证明现有测试，W3无自身PG测试、G4/repo/replay未独立验收，reserve/hint/shortfall等仍阻塞，禁止当完整W3开PR。W3更正先前“改用拒绝实际时间”仅方案，当前代码仍用plans创建时间，已列未完成。另rg shell通配不存在导致exit1，改用rg --files确认freeze_contracts包含BankState，后续内部契约增量须同步冻结快照。 |

| 2026-10-23 | Codex（协调者） | S1实现agent已报告澄清状态小组件本地提交d2726bb与3项PG自测（协调者尚未复跑，不计验收）；确认S1不重复W1 PIN业务，缺W1模块保持未配置并拒绝客户路径，W1存在时集中加载。编写下一串行组件S2-reserve任务书：仅bankcore纯函数与精确场景测试，生产真实义务数据另组件补齐，不以空默认字段冒充完成。W1新定时缺首次时刻裁定FORM_REQUIRED，不套种子06:00；W2读时钟按PRD现有versions/observed_at和reset撤销上下文约束，不擅加协议字段。 |

| 2026-10-23 | Codex（协调者） | 为保留未提交协调文档、便于后续main同步，准备在docs/codex-wave1-launch仅本地提交启动/共享缺口/S1/S2任务书与日志；diff --check已通过，无业务变更。后续随首个共享组件接受独立评审后推送，不直接推main。G2夹读版本竞争裁定DEPENDENCY_UNAVAILABLE 503，不能伪造无plan的STATE_CHANGED Decision；失败不得存source_ref。 |

| 2026-10-23 | Codex（协调者） | 协调文档已本地提交28a0c21（docs/codex-wave1-launch），尚未推送。补W4接入清单：取消后的原话CANCELLED不得非法转CONSUMED；执行中/UNKNOWN claims持久保留；W3 metadata重建和锁初始化需联测。S1收到独立镜像tag要求，不覆盖共享栈镜像。所有进行中的worker自测均待协调者复跑，不以消息数字直接计验收。 |

| 2026-10-23 | Codex（协调者） | S1组件2自报本地提交8e1874a（待协调者复跑）。S1核查原tests/gateway/db/conftest.py使用postgres镜像隐含VOLUME：即使不传-v也可能创建匿名卷并由--rm自动清理，故纠正此前“未删除任何卷”的过宽表述；已证实保留的是safebank_pg-bank/pg-agent两个持久卷。已让W1/W2暂停新PG启动，S1给fixture加显式tmpfs后由协调者同步；不主动清理任何已有匿名卷。W2报告TAINTED新目标24h冷静期在DENY短路时仍须由G4持久化，已登记W3联调清单。 |

| 2026-10-23 | Codex（协调者） | S1给出tmpfs修正5b67a76，协调者检查diff后提取仅fixture两行为独立提交b514274，再cherry-pick到W1/W2形成614ec9b；其未提交业务改动未动，已通知恢复PG/git操作。S1原提交含依赖前序的报告，未整批搬入。正在亲自复跑tmpfs夹具既有17项PG门禁。W2自报检查点有1项memo敏感内容xfail，明确不算完整退出；将于修正fixture后复验。 |

| 2026-10-23 | Codex（协调者） | S1本地交付060e0f4，协调者亲自复跑五条全部exit0：lint153 files；gateway95 passed/3 skipped（1.00s）；既有PG17 passed（2.19s）；全量214 passed/51 skipped（1.28s）；S1专项34 passed零跳过（4.40s，真实PG及7种独立Linux权限情形）。另root tmpfs夹具复验17 passed（2.76s）。已派独立只读review_s1对照PRD评审；初报P1双listener异常时cancel可能不执行Uvicorn shutdown，待完整报告后修，不开PR。W1交付本地检查点d9a1b56，协调者正在复跑；W4已派独立agent，原worktree先补tmpfs提交0705452。 |

| 2026-10-23 | Codex（协调者） | S1独立评审FAIL/REQUEST_CHANGES：R1异常cancel跳过Uvicorn shutdown（P1），R2healthcheck随Compose root配置继续特权运行（P2）；报告记录docs/reviews/L2-S1-runtime.md。按receiving-code-review核对源码；另只构造DB池实测size5/overflow10，低于PRD≥18；W4发现execution_jobs主键plan_id与state硬编码id不符，claims也需CLAIMED→CONSUMED登记。进入S1修复轮1，不开PR。W1协调者复跑全部通过：lint163、gateway1510/11skip、PG17、全量1629/59skip、W1 1457零skip（6.44s），仍保留共享联调限制。W2检查点1699091自报含1xfail，待亲自复跑。 |

| 2026-10-23 | Codex（协调者） | W4提出并核实必要持久字段：evaluations.view_json、otp_challenges.code_ciphertext、execution_jobs.authorization_evidence/deadline_cap_wall，汇总到wave1-migration-requirements.md，尚未改表/迁移。OTP按D5模拟短信同事务密文持久化，不为不存在的真实短信服务引入outbox；AEAD域分离与无歧义AAD，明文不进A/F/日志。W4发现EXECUTING禁止回退与L3明确STATE_CHANGED后的重新审批冲突，要求其停在独立组件检查点，待独立架构审查再实现G8。必要迁移将于四路待决汇总后、完整联调前串行实施，避免缺列下伪造通过。已派独立review_w1，S1原agent在修复轮1。 |

| 2026-10-23 | Codex（协调者） | W2检查点1699091亲自复跑：lint161文件；gateway119 passed/3 skipped/1 xfail（1.65s）；PG17 passed（2.71s）；全量238 passed/51 skipped/1 xfail（1.95s）；W2 58 passed/1 xfail（2.81s）。memo黄码仍缺，xfail不算验收，不开PR。W1独立评审7项P1、FAIL/REQUEST_CHANGES，记录L2-W1-identity.md；协调者亲自复现否定/条件/事实句错误scope、分单位放大、来源默认错误、收款人子串错误，安排原agent修复轮1。查找fix-loop首次路径错误exit1，随后按references路径完整读取。W4报告过时probe仍断言审批NotImplementedError，交共享接线组件替换真实failclosed验收；未为过绿保留假实现。 |

| 2026-10-23 | Codex（协调者） | 阅读真实L3 prepare/execute/reconcile与L2状态表，记录执行状态独立架构审查问题L2-execution-state-question.md：单个STATE_CHANGED不能直接证明无其他迟到提交，需核查attempt fence与重新审批证据，禁止给UNKNOWN通用回退边。核查CI实际文件checks.yml（初按ci.yml读取失败exit1后定位），要求S1增加gateway真实PG及shared-runtime/Linux目标，避免CI绿但漏组件门禁。 |

| 2026-10-23 | Codex（协调者） | W4检查点3856a4c（实现8abc82b）亲自复跑五条命令：lint151文件通过；真实PG17 passed（4.76s）；W4 26 passed零skip（8.47s）；gateway 1 failed/81 passed/8 skipped（0.89s）；全量1 failed/200 passed/56 skipped（1.67s）。两失败均test_create_for_evaluation_is_not_implemented旧占位断言，当前缺依赖正确抛503而非NotImplementedError；需共享接线组件更新测试。G8/G5/UNKNOWN未实现，未开PR。已派独立review_execution_state审查迟到请求与重新审批协议，W4暂停实施。 |

| 2026-10-23 | Codex（协调者） | 协调文档本地提交3d8d6c4。S1修复轮1亲自复跑HEAD db8a00c（代码80fc3bf）：lint156；gateway108 passed/6 skipped（3.54s）；PG17 passed（2.59s）；全量227 passed/54 skipped（4.25s）；shared50 passed零skip（9.01s），含真实socket、18个同时PG连接、Linux权限/healthcheck；超时案例预期打印CancelledError但测试通过。已派原review_s1定点复审060e0f4..db8a00c。S1全栈准备复用.secrets symlink，起初.wheels也symlink导致Docker COPY上下文缺文件、make up exit2（未变更运行栈）；只移除该临时链接并复制44个既有wheel后重试。两个持久卷创建时间均2026-10-23T12:26:36Z，未删重建。架构任务参考路径经评审纠正为W4-execution.md、evaluation/repo.py。 |

| 2026-10-23 | Codex（协调者） | S1在db8a00c完成实际make up（exit0，既有dataset preserved，六服务healthy）；make test-integration exit0，真实网络/凭证/DB角色隔离、HTTP→PG→prepare/transfer/outbox、L3进程重启与幂等回放通过；两持久卷创建时间仍2026-10-23T12:26:36Z。S1定点复审初报新P2：force_exit使Uvicorn等待提前返回，随后无cancel的gather可能无限等待；协调者读main.py确认，待完整报告后原agent修复轮2，未开PR。 |

| 2026-10-23 | Codex（协调者） | fetch核实远端main仍8555bf0。按已批准S2任务书预建并附加safebank-reserve worktree、feat/l3-reserve分支，离线安装环境；尚未派实现或改代码，启动前再同步最新main，共享实施仍逐组件串行。阅读现有ScheduledView有anchor_day/weekday/time_of_day、SubscriptionView只有next_due_at，S2不得猜测缺失月度锚点或把未加载生产义务当空。 |

| 2026-10-23 | Codex（协调者） | 补附加已核查的PR #3到当前任务。S1发现.secrets/目录忽略不覆盖本地symlink，补.git/info/exclude的/.secrets（不读取/提交秘密）；check-ignore确认秘密与wheels均忽略。修复轮2 HEAD0a95872（业务e99406d）亲自五退出全部exit0：lint156；gateway109/6skip（3.49s）；PG17（2.01s）；all228/54skip（3.92s）；shared51零skip（7.87s）。记录docs/acceptance/L2-S1-runtime.md，原review_s1定点复审中；finishing-a-development-branch流程按用户已有push/PR/merge授权执行，不重复询问。 |

| 2026-10-23 | Codex（协调者） | S1修复轮2独立定点复审PASS/APPROVE：N1 ADDRESSED，无新增问题；原评审亲自定向回归1 passed（0.21s），R1–R4/CI/N1全部关闭。准备将纯协调文档提交28a0c21、3d8d6c4及后续记录按顺序纳入S1分支（fixture已存在不重复cherry-pick），然后推送base main的组件PR；远端CI尚未运行，不预称合并。 |

## 5. 下一步待办（按顺序）

P0 准备阶段（决定后续一切）：
- [x] **V1 核实提交物格式与评分标准**（官网 https://fintechathon.g-ican.com/#/ 是 SPA，可抓取 JS bundle 或接口）。结果写入 §3「已确认事实」。
- [x] `git init`，并按 PRD §3.6 建立目录骨架；添加 `.gitignore`（含 `.env`、`.secrets/`）、`.env.example`、`Makefile`、`docker-compose.yml`（两个网络、gateway 双端口，PRD §3.3、§11.1）。
- [x] 按官方文档核实 DeepSeek 的 model ID、base_url、能力与价格（结果见 PRD §6.6）。
- [x] DeepSeek 接入实测（330 次原配置/回退 + LangChain live，详见 ADR-002；生产Schema语义与Postgres saver留P1/P2验收）：按 PRD §6.6 的 E1–E6（时间盒 4 小时，每项有通过标准与回退），结论与原始日志写 `docs/decisions/ADR-002-llm.md`。API key 只放进 `.env`。
- [x] 核实开源项目的 license、维护状态与关键源码行为（PRD §0.4、§4）。
- [x] 建立 `docs/third-party.md`（实际引入依赖已锁定；未引入依赖在对应组件开始前安装核实）：锁定版本并核对 license 原文（PRD §4.2 中标「P0 锁定」的依赖，含 psycopg 的 LGPL-3.0 复核）。
- [x] Invariant：已决定不用（PRD §4.3，V-INV），不再做 POC；已写 `docs/decisions/ADR-003-policy-engine.md`，引用历史 V-INV，没有重复 POC。
- [x] 阅读 devbanksdk 源码：结论是不 fork，L3 自研（证据见 PRD §4.2）。已写入 `docs/decisions/ADR-001-bank-backend.md`。

之后按 PRD §13 的日程推进（四条并行流，10-30 功能冻结，11-01 上午缓冲）。先打通一条贯穿三层的细线（P2：在 App 里让小安完成一笔需确认的转账），再横向铺开场景。

### P1 实施计划（2026-10-23，用户授权）

目标：PRD §13 的 L3 核心，为 P2 提供真实后端。公共契约18读/14写齐全，P1业务仅转账/卡片；其他工具明确拒绝，不伪造成功。

| 状态 | 组件 | 交付与独立验收 |
|---|---|---|
| [x] | P1.1 基础契约 | 严格整数分、4位Decimal、带时区时间、枚举/错误码；合法/边界/非法/额外字段/JSON往返 |
| [x] | P1.2 接口契约 | 18读/14写、PlanDraft/BankState/LedgerEffect/ExecutionResult/执行协议；每工具正常及非法样本、DAG/引用/来源重复/目标互斥、13工具strict生产Schema+DeepSeek小样本 |
| [x] | P1.3 纯函数账本 | 四累计量、确定性ID、posted/pending/部分确认/释放；守恒、不透支、状态不变、终结唯一/超时 |
| [x] | P1.4 数据库迁移 | 精确锁SQLAlchemy/asyncpg/Alembic，核心表/约束/索引/只追加；真实Postgres迁移、权限、readiness及失败路径 |
| [x] | P1.5 状态与持久化 | scope/load_bank_state/persist唯一实现，核心测试数据独立标识；只读快照、往返、回滚、effects一致、对方信息脱敏 |
| [x] | P1.6 转账与卡 | 本人/本行/他行、新收款人、限额/渠道/冻结/解冻/挂失补卡；全边界、复用引用、归属、补卡不清累计 |
| [x] | P1.7 原子执行恢复 | prepare/execute/query/reconcile；attempt/deadline/证据/锁序/版本/幂等/墓碑；回滚、重试、丢包、迟到竞争 |
| [x] | P1.8 L3集成验收 | 真实L3替换探针、服务凭证、核心5读/snapshot/执行/outbox；真实HTTP→DB、20并发/双向转账/冻结竞争、预演一致性、P0回归与CI |

### L2 进度（Security Gateway 后端，计划 `docs/plans/L2-plan.md`）

| 状态 | 组件 | 分支 / PR | 任务书 |
|---|---|---|---|
| [x] | W0 基础骨架 | `feat/l2-foundation` / PR #2 已合并 | `docs/plans/briefs/W0-*.md`，评审 `docs/reviews/L2-W0-architecture.md` |
| [ ] | W1 身份、会话与原话 | `feat/l2-identity`（检查点d9a1b56，待共享联调） | `docs/plans/briefs/W1-identity.md` |
| [ ] | W2 读代理、参数来源与污点 | `feat/l2-provenance`（实现中，未验收） | `docs/plans/briefs/W2-provenance.md` |
| [ ] | W3 快照、预演、工作流、决策与 G4 | `feat/l2-decision`（检查点3bcbe8e，共享依赖阻塞） | `docs/plans/briefs/W3-decision.md` |
| [ ] | W4 审批与执行 | `feat/l2-execution`（实现中，未验收） | `docs/plans/briefs/W4-execution.md` |
| [ ] | 0002_wave1 迁移（波次 1 各任务提出的表 / 字段需求，由协调者串行追加） | — | 汇总各 `docs/progress/W*.md` 的「待决」 |
| [ ] | W5 定时授权、通知、审计接口、D 接口 | 波次 2，任务书未写 | L2-plan §3 W5 |
| [ ] | W6 网关层安全验收（A8、A1/A2/A3/A5） | 波次 2，任务书未写 | L2-plan §3 W6 |

波次 1 公共约定：`docs/plans/briefs/wave1-common.md`（四个任务都必须遵守）。

门禁：每组件证据写 docs/acceptance/P1.N.md，并追加 §4。任何失败先修复；没有实际运行不得标通过。P1.8全部通过后冻结契约v1，再进入P2；不实现L2审批/OTP/业务Agent/UI/调度器。保留指定DeepSeek V4 Pro、beta、strict、关闭思考。数据库测试不使用SQLite；容器只走后台CLI，不启动Desktop窗口。保留 make test/lint/test-integration，新增P1并发和一致性目标。

## 6. 关键文件地图

| 文件 | 内容 |
|---|---|
| `PRD.md` | v2.1 定稿。§0.4 核实记录、§0.5 决议 D1–D47、§2.2 种子数据、§4 开源定选与移植映射、§5 场景需求 ID、§6 L1（图、路由表、LLM、P0 实验、回放）、§7 L2（流水线、原话与污点、预演、工作流、审批与执行）、§8 L3（账本、表、接口、scheduler）、§9 **锁定的接口文档**与前端页面清单、§10 评测、§11 部署、§12 演示、§13 日程与削减、§15 DoD |
| `docs/reviews/` | 第二、三轮审核与修订复核报告 |
| `docs/archive/` | PRD v1.2、作废的 v2.0 草稿 |
| `01-技术选型综合报告.md` | 调研结论，以及「已核实 / 待核实」清单 |
| `docs/third-party.md` | 实际引入的第三方依赖：版本、license、用途 |
| `AGENTS.md` | 本文件 |
| `packages/contracts/` | 已实现并冻结v1的三层共享 Pydantic 契约。**修改它就等于修改接口，必须同步三层并更新 PRD §6.2 与 §9** |
| `packages/bankcore/` | 已实现P1的纯函数领域规则，L2 预演与 L3 执行共用 |
| `services/{bank-backend,gateway,agent}/` | L3为真实P1服务；L1/L2仍是探针骨架 |
| `evaluation/` | 目录骨架，正常 / 攻击 / 绕过runner尚未实现 |
| `docs/decisions/ADR-*.md` | 架构决策记录（ADR-001–007） |

调研时的临时克隆位于 `/tmp/sbg-research/`（可能已被系统清理，不要依赖它）。

## 7. 架构速查

```text
浏览器 ──app cookie──▶ frontend(App+BFF :3000) ──助手令牌──▶ agent(L1 :8100) ──▶ gateway(L2 :8200) ──服务令牌──▶ bank-backend(L3 :8300) ──▶ postgres-bank
                            └──── 原话登记 / 审批 / OTP 直连 L2（不经过 LLM）────┘
网络：agent-net = {frontend, agent, gateway:8200, postgres-agent}；bank-net = {gateway:8201, bank-backend, postgres-bank}
```

- 写操作链路（PRD §3.4）：BFF 先向 L2 登记原话 → L1 `plan` → L2 `/plans/evaluate`（契约 → 实体 → 来源与污点 → 意图范围 → 快照 → 累积预演 → 跨会话工作流 → 决策 → 审计）→ App 按 `approval_id` 取确认面板，确认时带 `view_hash` → L1 resume → L2 `/plans/{id}/execute`（客户锁 → 新快照复演 → L3 prepare / execute）→ L3 单事务执行，幂等键 `(customer_id, plan_id)`。
- 决策取值：`ALLOW` / `REQUIRE_APPROVAL` / `REPLAN` / `DENY`；风险等级为绿 / 黄 / 红（红需 OTP）。origin 由调用方凭证决定，L1 拿不到浏览器令牌。
- 定时任务与代扣到期时由 L3 scheduler 回调 L2 `/internal/scheduled/authorize`（:8201，PRD §9.5a S1），由 L2 检查后主动调 L3 执行。

## 8. 工作规则

### 8.0 记录（用户特别强调）
- **所有操作都要写进本文件 §4 进度日志**：包括文件的创建、改名、删除、恢复，文档改版，调研与核实结论，subagent 审核结果，以及失败或中断的工作。不要等到收尾才补记，每完成一步就记一步。

### 8.1 开发
- Python 3.12（容器内）；依赖在 `pyproject.toml` 中**锁定精确版本**。新增依赖前确认它是知名、活跃维护的包。
- 风格：`ruff` + `ruff format`；类型标注齐全；Pydantic v2。
- 每个服务的 README 写清启动、测试、环境变量。
- 新增工具时：先在 `packages/contracts` 中定义 → L3 实现 → L2 注册基础风险等级与规则 → L1 绑定 → 写正常用例与至少 1 条攻击用例。
- 新增风险规则时：在 `services/gateway/app/policy` 中实现 `Rule.evaluate`，阈值放进 YAML，配套单元测试，并在 PRD §7.7 / §7.8 的表格与 §9.8 代码总表中登记 code。

### 8.2 验证（每次改动后）
- **组件级验收门禁（用户明确要求，2026-10-23）**：每次只实现一个职责清晰、可独立运行的小组件。开始前写明输入/输出、依赖、验收命令与失败条件；完成后立刻独立测试正常路径、非法输入和边界/故障路径，再接入下一组件。禁止先堆积大量实现再集中补测。
- 每个组件在 `docs/acceptance/` 保存实际命令、结果、日期与未覆盖项；§4 同步追加。未运行、跳过、外部阻塞分别记录，不等于通过。验收不通过时先修当前组件；不依赖失败组件的独立工作可以继续。
- 集成后再验证组件间边界；容器健康不等于业务验收，mock 通过不等于真实 API 实测。P0 不实现银行业务，不用成功占位响应冒充已实现接口。

- 运行受影响服务的 `pytest`；改了 bankcore 或 L3 写路径时，必须同时运行并发测试和预演/执行一致性测试。
- 改了 L1/L2 交互时，运行 `make demo`（replay 模式）确认演示脚本未被破坏。
- 结果写入 §4 进度日志，包括失败项。**不要把没跑过的测试写成通过。**

### 8.3 安全
- `.env` 永不提交；密钥只走环境变量。
- `/demo/*` 只在 DEMO_MODE / EVAL_MODE 注册（两者互斥）；非 B2 的 `GATEWAY_PROFILE` 只在 EVAL_MODE 下生效；`/internal/*` 只监听 bank-net（PRD §7.1、§11.1）。
- 不要为了让测试通过而放宽 L2 规则；如果确实需要调整阈值，在进度日志中写明原因。

### 8.4 事实与引用
- 对开源项目、赛题、竞品的任何断言都要附带来源（URL、文件路径:行号、命令输出）。没有核实的内容标注【待核实】。
- 对外材料（PPT、技术文档）中，相关工作（CaMeL、FIDES、Progent、AgentDojo、Invariant）要如实引用，不夸大创新。

### 8.5 Git
- 主分支 `main`；功能分支 `feat/<层>-<功能>`。
- 只有在用户要求时才提交；提交前检查是否混入 `.env` 等敏感文件。

## 9. 常用命令（脚手架建好后生效）

```bash
make up          # 启动全部服务
make reset       # P1仍明确拒绝；正式种子/时钟重置在后续实现
make test        # 单元测试；无PG配置时集成项显示skipped
make test-p1     # 独立空库迁移+全部真实PG验收
make test-p1-concurrency # 并发门禁（先test-p1构建）
make test-p1-consistency # 预演/持久化一致性
make test-integration # 真实网络、HTTP及进程重启恢复
make contracts-check # 冻结契约v1漂移检查
make demo        # replay 模式运行演示脚本
make eval        # 运行评测，生成 evaluation/reports/<ts>/
make logs        # 查看各服务日志
```

## 10. 交接清单（结束工作前）

- [ ] 更新 §3 当前状态与 §4 进度日志
- [ ] 勾选 §5 中完成的待办，并补充新发现的待办
- [ ] 如果有设计变更，同步 PRD 对应章节，并在 `docs/decisions/` 中写 ADR
- [ ] 写明未完成、未验证、已知 bug

