# L2 阶段执行计划：Security Gateway 后端

> 2026-10-23 制定。范围、分工与验收以本文件为准；设计细节以 [PRD.md](../../PRD.md) 为准，两者冲突时以 PRD 为准并在本文件更正。
> **本阶段不做前端**（含 BFF / App 页面），直到用户亲自下令（AGENTS.md §2 第 13 条）。

## 0. 最高原则（用户 2026-10-23）

**项目稳健与架构合理优先于并行。** 并行只是手段：任何一步只要会带来接口冲突、重复造轮子或架构漂移，就减少并行路数，必要时改串行。具体做法：

1. 先骨架（W0），后并行；W0 的接口经过**架构评审关卡**（§5）通过后，才开波次 1。
2. 每个 worker 只改分配给它的文件；共享文件只有指定的人能改（§4）。
3. 每次合入集成分支前，指挥复跑退出命令并检查架构边界（层次隔离、bankcore 共用、无 L1 授权路径）；发现偏离就打回，不靠后续修补。

## 1. 目标与范围

把 PRD §7 的 L2 Security Gateway 做成可运行、可测试的后端服务，接在 P1 已交付的 L3 之上。

**做**

| 模块 | PRD 依据 |
|---|---|
| 双端口进程（公共 :8200 / 内部 :8201）、配置、错误格式、gateway 库迁移（§7.2 全部表） | §3.3、§7.1、§7.2、§9.1、§11.1 |
| 身份与令牌：app / assistant JWT（Ed25519）、F/A/S/D 服务令牌、授权矩阵、PIN 登录与频控 | §7.3、§9.5 G1/G14/G15/G18/G22 |
| 原话与授权生命周期：typed / hint / action、确定性意图解析、复述检测、替代与取消、澄清闭环、谓词求值 | §7.4、§7.6、§5.6、§9.5 G3/G19/G20/G21/G22a |
| 读代理与读取账本、参数来源与派生模板、污点规则 | §7.5、§9.3、§9.5 G2 |
| 评估流水线 ①–⑨：契约、实体、来源、意图、快照、累积预演、工作流、决策、审计；hint 生成；shadow B1；GATEWAY_PROFILE | §7.2、§7.7–§7.9、§10.1、§9.5 G4 |
| 审批与执行：PlanView / view_hash、OTP、风险揭示、claims、execution_jobs、L3 prepare / execute / reconcile、attempt、STATE_CHANGED 复演、UNKNOWN 恢复 | §7.10、§9.5 G5–G10/G17 |
| 审计哈希链、通知、客户审计接口 | §7.12、§9.5 G11–G13/G16 |
| 定时授权 S1、演示 / 评测 D 接口的 gateway 侧 | §7.11、§9.5a、§9.7 |
| 网关层安全验收：授权矩阵、A8 绕过、A1/A2/A3/A5 网关层用例 | §3.2、§10.2、§10.3 |

**不做**（各自在后续阶段）

- 前端与 BFF（F1–F13）：用户下令前不开工。测试中直接用 F 服务令牌 + app JWT 调 gateway，代替 BFF。
- L1 LangGraph 图（P2 的 L1 部分）：测试中用 A 服务令牌 + assistant JWT 直接调 gateway，代替 L1。
- L3 新业务（定时、AA、订阅、基金、时钟推进、事件 job、演示注入）：属于 PRD P4–P6。gateway 侧照契约实现，用**契约测试替身**（fake L3）验证；真实联调在对应 L3 能力交付后补做，并在本文件 §7 登记。

## 2. 工作方式

### 2.1 角色

- **指挥（主会话 Claude）**：拆任务、写任务书、启动 worker、验收、合并、记日志。不写业务代码。
- **worker**：本机 Claude Code CLI 无头模式（`claude -p`），模型走 `~/.claude/settings.json` 中的 DeepSeek（`deepseek-v4-pro`；Haiku 档为 `deepseek-flash`）。每个 worker 一个 git worktree、一个分支，互不共享工作目录。

### 2.2 启动方式（指挥执行）

```bash
git worktree add ../Fintech2026-wt/<任务名> -b feat/l2-<任务名> <基线>
cd ../Fintech2026-wt/<任务名>
env -u ANTHROPIC_BASE_URL -u ANTHROPIC_AUTH_TOKEN -u ANTHROPIC_API_KEY \
    -u ANTHROPIC_MODEL -u ANTHROPIC_DEFAULT_HAIKU_MODEL -u ANTHROPIC_DEFAULT_SONNET_MODEL \
    -u ANTHROPIC_DEFAULT_OPUS_MODEL -u CLAUDECODE -u CLAUDE_CODE_ENTRYPOINT \
  claude -p "$(cat docs/plans/briefs/<任务名>.md)" \
    --permission-mode acceptEdits \
    --allowedTools "Read,Edit,Write,Glob,Grep,Bash(git status*),Bash(git diff*),Bash(git add*),Bash(git commit*),Bash(git log*),Bash(.venv/bin/*),Bash(uv *),Bash(make *),Bash(docker *),Bash(ls*),Bash(cat*)" \
    --max-turns 400 --output-format json > <日志目录>/<任务名>.json
```

- 清掉继承的 `ANTHROPIC_*` 变量，确保走 DeepSeek 而不是 Anthropic 账户（2026-10-23 已实测：`modelUsage` 为 `deepseek-v4-pro[1m]`）。
- 用白名单放行命令，不用 `--dangerously-skip-permissions`；worker 不能 `git push`、不能改 `~/.claude`、不能删工作区外文件。
- 指挥在后台运行，worker 结束时自动收到通知。

### 2.3 任务书（`docs/plans/briefs/<任务名>.md`）固定包含

1. 必读：AGENTS.md、本文件、PRD 指定章节。
2. 负责的文件 / 目录（**只能改这些**）；需要改共享文件（contracts、pyproject、compose、Makefile）时，只按任务书写明的方式追加，不重排。
3. 退出条件与必须通过的命令。
4. 交付要求：在分支上本地提交（不 push），提交信息按 AGENTS.md §8.5；进度写入 `docs/progress/<任务名>.md`（**不改 AGENTS.md**，避免多分支冲突，由指挥汇总）；最终回复给出：完成项、实际跑过的命令与结果摘要、未完成项、需要指挥决定的问题。
5. 遇到 PRD 不闭合或互相矛盾：不自行发明设计，写进 `docs/progress/<任务名>.md` 的「待决」并停在可交付点。

### 2.4 并发测试隔离

多个 worker 同时跑 `make up` 会争用固定子网与容器名，所以：

- worker 只跑：单元测试、契约测试，以及 **W0 提供的 `make test-gateway-db`**（启动唯一命名、随机端口、无固定子网的临时 Postgres，测完销毁）。
- 全栈 `make up` / `make test-integration` 只由指挥在集成分支上串行跑。

### 2.5 合并与验收

1. worker 完成 → 指挥在其 worktree 里复跑退出条件命令，看 diff 规模与关键文件，必要时让同一 worker 续改（`claude -p --resume <session>`）。
2. 通过 → 合入集成分支 `feat/l2-gateway`（`--no-ff`），跑全量门禁。
3. 一个波次全部合入后，指挥推送集成分支、开 PR、等 CI；**合入 main 前请用户确认**。

## 3. 任务拆分与依赖

```text
波次 0（串行，约 0.5–1 天）： W0 基础骨架
波次 1（4 路并行）：          W1 身份与原话   W2 读代理与来源   W3 预演·工作流·决策   W4 审批与执行
波次 2（2 路并行）：          W5 定时·通知·审计·D 接口   W6 网关层安全验收（依赖波次 1 全部合入）
```

### W0 基础骨架（`feat/l2-foundation`）

- 双应用单进程：`public_app`（:8200，不注册 `/internal/*`）、`internal_app`（:8201，只有 `/internal/*`），绑定地址取 `GATEWAY_PUBLIC_BIND` / `GATEWAY_INTERNAL_BIND`。
- 配置与启动护栏：DEMO_MODE / EVAL_MODE 互斥；非 B2 profile 只在 EVAL_MODE；`DEMO_CONSOLE_TOKEN` ≥ 32 字节。
- gateway 库 Alembic 迁移：§7.2 表清单全部建表（含状态枚举 CHECK、`UNIQUE(customer_id, prev_hash)`）；运行角色 `gateway_app` 对 `audit_events` 只有 INSERT / SELECT。
- 统一错误 `{error:{code,message,details,trace_id}}`、`X-Request-ID`、`contracts.codes` 接入；request_id 幂等工具函数。
- 凭证中间件骨架（F/A/S/D + JWT 校验接口，具体签发留给 W1）与 `auth/matrix.py` 静态声明格式。
- L3 客户端（httpx，超时：prepare 3s、execute 10s、其余 5s）与 **fake L3**（契约测试替身，按 `docs/contracts/v1.json` 校验请求 / 响应）。
- 审计追加库函数（§7.12：审计锁 + prev_hash）。
- **流水线插件接口**：`gateway/pipeline/` 定义阶段协议与 `EvalContext`，每个阶段是独立模块；W2、W3 只往各自模块里填实现，避免改同一文件。
- `make test-gateway-db`（隔离临时 Postgres）、`make test-gateway`。
- 退出：迁移空库 upgrade 两次成功；双端口隔离单测；错误格式、幂等、审计链单测；fake L3 契约测试通过；`make lint` 通过。

### W1 身份、会话与原话（`feat/l2-identity`）

- JWT 签发 / 校验（Ed25519，app 与 assistant 两种 aud；assistant 带 `utts`）、G1 PIN 登录（argon2 哈希、5 次锁 15 分钟、IP 频控）、G14、G15、G18（`utts=[]`）、G22（仅 DEMO/EVAL）。
- 授权矩阵 `auth/matrix.py` 全量登记 + `test_auth_matrix.py`（路径 × 凭证 × 跨客户）。
- G3 原话登记：kind 互斥校验；确定性意图解析（`policies/intent_lexicon.yaml`：动作词、金额与对象关联、撤销词、周期词、拼音近音、谓词）；引号 / 疑问句排除；收款人精确优先匹配；复述检测（≥ 8 位账号 vs 客户 L3 近 90 天不可信数据 + 会话 untrusted 自由文本）；替代（SUPERSEDED）与取消（epoch）；澄清闭环（pending_clarifications、`clarification_id` 补答合槽、`parse_status`）。
- G19、G20、G21、G22a。
- 退出：意图解析表驱动测试（覆盖 §12 七幕原话、X-1~X-4、A2 两个复述变体、引号 / 疑问句、改口、取消）；澄清合槽与串接攻击测试；矩阵测试全绿。

### W2 读代理、参数来源与污点（`feat/l2-provenance`）

- G2 读代理：注入 customer_id、ID 归属校验、调 L3 `/internal/read/{tool}`、ReadEnvelope 包装、`untrusted_paths` 标注（§9.3 不可信字段清单）、写 `read_records`。
- 流水线阶段 ②实体与所有权、③来源与污点、④意图范围（读 W1 产出的 normalized_scope；W1 未合入前用 fixture）：`arg_sources` 数组校验、派生模板 `cancelled_monthly_fees` / `available_less_7d_charges` 重算、金额来源白名单、TAINTED / UNKNOWN / INJECTION_SUSPECTED / memo 敏感规则、app_form 值级污点、(对方, 金额) 多重集合比对、源账户默认规则、谓词集合等于 / 属于。
- `policies/trust.yaml`、`injection_patterns.yaml`、`tools.yaml`（基础等级、ActionKind、敏感参数、allowed_origins、outflow）。
- 退出：每条规则代码至少 1 个正例 1 个反例；A2 网关层变体（备注注入、复述）在单测层面被拒；`test_llm_fields_ignored.py`（改写 intents / rationale 不改变结论）。

### W3 预演、工作流与决策（`feat/l2-decision`）

- 阶段 ⑤快照（调 L3 `/internal/snapshot`）、⑥累积预演（调 bankcore `apply_plan`，**不另写业务算术**）、7 天预留算法（§7.7，`max_outflow`、SCHEDULED_SHORTFALL）、⑦工作流规则（§7.8 全表，序列匹配器 `workflow/sequence.py`，窗口 sim / wall 区分，冷静期表）、⑧决策聚合与 hint 生成（只收缩）、shadow B1、GATEWAY_PROFILE 各档开关、⑨评估审计。
- 组装 G4：两种请求体（PlanDraft / AppFormPlanRequest）按调用方判别；plan_id 分配；request 幂等；`plans` / `evaluations` 落库。
- 退出：A1（拆分升红）、A3（含换序与无卡储蓄变体）DENY、第 2 幕 REPLAN 与 hint「赵六最多 2,955 元」、SCHEDULED_SHORTFALL、预演与 L3 一致（复用 P1 一致性测试思路，对 fake / 真实 snapshot）；各 profile 开关的结论差异测试。

### W4 审批与执行（`feat/l2-execution`）

- PlanView 生成与 view_hash（覆盖逐步余额、风险、揭示版本、memo）、G9、G17、G6 确认（view_hash、OTP、risk_ack + disclosure_token）、G7、G10（OTP：HMAC 存储、2 分钟、重发 3 次、错 5 次锁）、每会话 1 个 PENDING / 新审批作废旧审批、ALLOW 的策略审批。
- G8 执行编排：客户锁（独占 `lock_conn`、`lock_timeout`、Semaphore 8）、authorization_claims、新快照复演（等价唯一判据 view_hash）、authorization_evidence 判别联合、L3 prepare / execute（attempt、deadline_cap）、L3 STATE_CHANGED 内部复演 2 次与 attempt+1、409 `details.decision`、202 UNKNOWN 与恢复工作器（reconcile 墓碑）、G5。
- 退出：并发双花同一作用域只成功一次；确认后篡改 / 错配 approval 失败；STATE_CHANGED 重新审批路径；UNKNOWN（丢回包、L3 重启、延迟 execute）恢复不重复记账（对真实 L3 测试库）；OTP 状态机测试。

### W5 定时授权、通知、审计接口与 D 接口（`feat/l2-ops`，波次 2）

- S1（§9.5a）：终态防重放（event_decisions）、grant 缺失 vs 失效、approved_risk 比较；L3 事件接口未交付前用 fake L3 验证并登记待联调。
- outbox 消费者（补建 grant、通知、workflow 事件对账）；G11、G12、G13、G16；通知类型与每日提醒去重。
- D 接口 gateway 侧：D0、D1、D5、D6a、D6、D7（固定断言注册表 + bank_ro）、D8、D10、D11、D12；依赖 L3 新能力的 D2 / D3 / D4 用 fake L3，登记待联调。
- 退出：S1 幂等与重放测试；审计链校验（含篡改检出）；D 接口只在 DEMO/EVAL 注册的测试。

### W6 网关层安全验收（`feat/l2-acceptance`，波次 2，波次 1 全部合入后）

- `evaluation/bypass/`：A8 全部路径（直连 L3、伪 JWT、A 调审批、自报 origin、approval 错配、复用、篡改、伪造原话、跨客户、D 越权、8201 从 agent-net 不可达）。
- 网关层用例（D6a / D6）：A1、A2（含两个复述变体）、A3（含两个变体）、A5，B0 / B1 / B2 三档对比，断言全部经 L3 状态。
- 全栈集成：在集成分支上 `make up` 后跑上述用例（指挥执行）。
- 退出：A8 全部失败（防御成功）；B2 在 A1/A2/A3/A5 上达到 PRD §10.2 预期，B0/B1 的差异如实报告。

## 4. 共享文件约定（防冲突）

| 文件 | 规则 |
|---|---|
| `packages/contracts/**` | 契约 v1 已冻结。确需新增（如 gateway 内部模型）放 `services/gateway/app/models/`；必须改 contracts 时写进「待决」由指挥统一改 |
| `pyproject.toml` / `uv.lock` | 只有 W0 可新增依赖（PyJWT、argon2-cffi、pypinyin 等）；其他 worker 需要新依赖写进「待决」 |
| `docker-compose.yml` / `Makefile` | 只有 W0 改；后续只允许在 Makefile 末尾追加本任务的 `test-*` 目标 |
| `services/gateway/app/pipeline/<stage>.py` | 每个阶段一个文件，按 §3 分配给 W2 / W3；`pipeline/__init__.py` 只有 W0 改 |
| `AGENTS.md` | worker 不改；指挥汇总 `docs/progress/*.md` |

## 5. 指挥的检查点

| 时点 | 动作 |
|---|---|
| W0 完成 | 复跑退出命令；**架构评审关卡**：指挥审查插件接口、表结构、凭证中间件、L3 客户端与 fake L3，另派 1 个只读评审 subagent 对照 PRD 审架构合理性与可扩展性（报告写 `docs/reviews/L2-W0-architecture.md`）；问题全部修完后合入 `feat/l2-gateway`，再以它为基线开波次 1。若评审认为某两路仍会改同一处，改为串行 |
| 波次 1 每个完成 | 复跑其退出命令；依次合入集成分支并跑 `make test-gateway` + `make lint`；冲突由指挥解决或让对应 worker 续改 |
| 波次 1 全部合入 | 指挥跑全栈 `make up` + `make test-p1` + 网关层冒烟；推送集成分支、开 PR、看 CI |
| 波次 2 完成 | 同上；开 PR；**请用户确认后合入 main** |

## 6. 退出条件（L2 阶段完成的定义）

- gateway 实现 §9.5 G1–G22a、§9.5a S1、§9.7 D 接口的 gateway 侧，字段与 `docs/contracts/v1.json` 一致。
- 授权矩阵、A8 绕过测试全部防御成功；A1/A2/A3/A5 网关层用例在 B2 下达到预期。
- 评估、审批、执行、UNKNOWN 恢复、审计链在真实 PostgreSQL 上测试通过；预演与 L3 执行一致。
- CI 全绿；所有依赖 L3 后续能力的部分在 §7 登记为「待联调」，不计为完成。

## 7. 待联调登记（随进度更新）

| 项 | 依赖的 L3 能力 | 状态 |
|---|---|---|
| S1 定时授权 | `/internal/events/*`、scheduler | 待 P4 |
| D2 / D3 / D4 | `/internal/demo/*`、`/internal/clock/*` | 待 P4 |
| 复述检测的 L3 近 90 天不可信数据 | snapshot 或读工具返回 memo / counterparty 历史 | W1 先核对 P1 snapshot 是否已含，缺则登记 |
