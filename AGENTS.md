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

## 3. 当前状态（每次工作后更新）

**最后更新**：2026-10-23

| 模块 | 状态 |
|---|---|
| 调研 | 开源项目元数据和 DeepSeek 官方信息已核实；评分/提交已从官网公开 JS 核实（docs/acceptance/official-v1.md）；竞品「明鉴」未核实 |
| PRD | **`PRD.md` = v2.1 定稿**（1,764 行，§0–§16；三轮多 agent 审核 + 两次修订复核，决议 D1–D47 见 §0.5）。v1.2 与作废的 v2.0 草稿在 `docs/archive/`；审核报告在 `docs/reviews/` |
| 代码 | P0 已开始：已 `git init -b main`，最小脚手架已建立：8 项独立测试、lint/format、6 容器健康及真实网络/凭证/DB角色验收通过；DeepSeek 330 次实验已记录，采用关闭思考回退。尚无银行业务实现 |
| 下一步 | P0 工程基线验收完毕；私有仓库已创建，正在提交推送。随后按组件进入 P1（不得把骨架当业务完成） |

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
- 需要实际运行才能确认的能力：DeepSeek strict + 思考模式 + 强制 `tool_choice` 的组合、完整 `submit_action_plan` Schema 能否被接受（PRD §6.6 E1–E6）；LangGraph `configurable` 是否写入 checkpoint metadata（§6.1）。Invariant、assistant-ui、AgentDojo runner 已决定不用，不再验证。
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

## 6. 关键文件地图

| 文件 | 内容 |
|---|---|
| `PRD.md` | v2.1 定稿。§0.4 核实记录、§0.5 决议 D1–D47、§2.2 种子数据、§4 开源定选与移植映射、§5 场景需求 ID、§6 L1（图、路由表、LLM、P0 实验、回放）、§7 L2（流水线、原话与污点、预演、工作流、审批与执行）、§8 L3（账本、表、接口、scheduler）、§9 **锁定的接口文档**与前端页面清单、§10 评测、§11 部署、§12 演示、§13 日程与削减、§15 DoD |
| `docs/reviews/` | 第二、三轮审核与修订复核报告 |
| `docs/archive/` | PRD v1.2、作废的 v2.0 草稿 |
| `01-技术选型综合报告.md` | 调研结论，以及「已核实 / 待核实」清单 |
| `docs/third-party.md` | （待建）实际引入的第三方依赖：版本、license、用途 |
| `AGENTS.md` | 本文件 |
| `packages/contracts/` | （待建）三层共享的 Pydantic 契约。**修改它就等于修改接口，必须同步三层并更新 PRD §6.2 与 §9** |
| `packages/bankcore/` | （待建）纯函数领域规则，L2 预演与 L3 执行共用 |
| `services/{bank-backend,gateway,agent}/` | （待建）三层服务 |
| `evaluation/` | （待建）正常 / 攻击 / 绕过用例与 runner |
| `docs/decisions/ADR-*.md` | （待建）架构决策记录 |

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
make reset       # 重置种子数据与模拟时钟
make test        # 全部单元测试
make demo        # replay 模式运行演示脚本
make eval        # 运行评测，生成 evaluation/reports/<ts>/
make logs        # 查看各服务日志
```

## 10. 交接清单（结束工作前）

- [ ] 更新 §3 当前状态与 §4 进度日志
- [ ] 勾选 §5 中完成的待办，并补充新发现的待办
- [ ] 如果有设计变更，同步 PRD 对应章节，并在 `docs/decisions/` 中写 ADR
- [ ] 写明未完成、未验证、已知 bug

