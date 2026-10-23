# Codex 波次 1 启动记录（2026-10-23）

本记录补充现有任务书，不改变 PRD、冻结接口、文件所有权或验收标准。唯一项目来源为 SJeffZhang/Safebank。

## 基线与调度

- PR #3 已合并，unit / p1-postgres 均 SUCCESS；main 基线 `8555bf0c82860a11765d73f2a59f8e72d6a5de7a`，已 fetch 确认最新。
- 协调者只负责环境、文档、分工、亲自复跑、评审、推送与合并，不写业务实现。
- 使用 Codex 内置子 agent；不再使用旧计划中的 Claude CLI。提交不得带虚假的 DeepSeek/Claude 署名。
- 运行时总并发槽为 4（含协调者），因此最多 3 个 worker 同时工作；四个任务分别使用独立 agent / worktree / 分支，分批派发，评审时留出独立只读 agent 槽。
- 完整 main 基线验收通过后开始业务实现；前端 App/BFF 和 L1 不开工。

| 任务 | 分支 | 已创建 worktree | 任务书 | 退出命令 |
|---|---|---|---|---|
| W1 | `feat/l2-identity` | `/Users/jet/.codex/worktrees/safebank-w1-identity/WeBank` | `W1-identity.md` | `make lint && make test-gateway && make test-gateway-db && make test && make test-w1` |
| W2 | `feat/l2-provenance` | `/Users/jet/.codex/worktrees/safebank-w2-provenance/WeBank` | `W2-provenance.md` | `make lint && make test-gateway && make test-gateway-db && make test && make test-w2` |
| W3 | `feat/l2-decision` | `/Users/jet/.codex/worktrees/safebank-w3-decision/WeBank` | `W3-decision.md` | `make lint && make test-gateway && make test-gateway-db && make test && make test-w3` |
| W4 | `feat/l2-execution` | `/Users/jet/.codex/worktrees/safebank-w4-execution/WeBank` | `W4-execution.md` | `make lint && make test-gateway && make test-gateway-db && make test && make test-w4` |

所有任务完整遵守 `wave1-common.md`。只在本地提交；不得 push、开 PR、合并、启动其他 agent、操作全栈或删除/重建 Docker 数据卷。

## 开工前接口与冲突核查

| 任务 / 交叉点 | 生产与消费关系 | 裁定与执行要求 |
|---|---|---|
| W1 自身 | 身份、原话、作用域；矩阵已冻结 | 补矩阵测试，不能改授权矩阵；近 90 天数据缺口依任务书登记，不能假造 L3 数据 |
| W2 自身 | ①–④、G2、注册表值 | 以 wave1-common 和第三轮复核为准，① 归 W2；不改注册表结构 |
| W3 自身 | ⑤–⑨、G4、replay | app_form 构造内部步骤以便取快照；replay 每次新建 EvalContext；不复用旧缓存 |
| W4 自身 | 审批、执行、客户锁、恢复 | 不改 replay.py；不得以超时猜失败；fake 与真实 PG 的证明范围分别报告 |
| W1 → W2/W3/W4 | `load_for_plan(conn, sid, ids, caller) -> AuthorizationInputs` | 冻结共享模型；W1 未合入前用 fixture，后续同步 main 后真实接口联测 |
| W2 → W3 | `ctx.steps`、provenance、intent、findings | W2 写阶段结果，W3 聚合 Decision；不得在 W2 私造 Decision |
| W2 ↔ W3 | `available_less_7d_charges` 共用 7 天预留算法 | 实现前核对 bankcore 现有能力并报缺口；签名由协调者收敛，未定前用测试替身，不复制两套算术 |
| W3 ↔ W4 | append_evaluation / replay 与 create_for_evaluation / build_view | 已有函数签名不变；对方未合入前打桩，合入后必须补真实接口边界验证 |
| W1 ↔ W4 | 取消/撤销与执行、授权消费 | 统一 state.transition；取消以客户锁为线性化点；发现冻结基础设施缺口立即登记并通知协调者 |
| W1–W4 共享文件 | Makefile、结果 dataclass、迁移 | Makefile 仅末尾追加各自目标；结果类只能由拥有者新增字段，任何共享文件修改先报协调者串行处理；迁移仅统一 0002_wave1 |
| W1–W4 数据库验收 | 当前 test-gateway-db 只收集 tests/gateway/db | 各自 wN/db 测试必须在自己的 test-wN 中实际启用并复用临时 PG fixture；不得只显示 skipped 就算通过；不改共享测试基础设施绕过所有权 |
| 文档残留 | L2-plan 的旧集成分支/逐次确认与新 §2.5 冲突 | 按用户最新长期授权：逐组件 base main PR，评审通过且 CI 全绿后 merge commit，无需再次询问 |

## 合并门禁

固定顺序：W1 → W2 → W3 → W4 → 汇总待决后串行 0002_wave1 单独 PR。

每组件：同步最新 main → worker 完成及本地提交 → 协调者亲自复跑全部退出命令 → 独立只读评审对照 PRD（规格与代码质量）→ 同一 worker 修复并复验 → 协调者推送、开 PR（base main）→ 核查当前提交的 unit / p1-postgres 全绿 → `gh pr merge --merge` → 同步 main、记录 AGENTS.md §4/§5。

有冻结接口或迁移阻塞时停止对应组件在可交付点，不假装完整通过，不为维持并行扩大改动范围。波次 1 完成后再写 W5/W6 任务书。

## 实际基线结果（截至准备阶段）

- make setup：首次沙箱 DNS 失败；联网重试通过，按 uv.lock 安装 78 个包。
- make keys：通过；.secrets 与 .venv 均被 Git 忽略，未输出密钥。
- make test：183 passed / 48 skipped。
- make test-gateway：64 passed。
- make lint：通过，145 files already formatted。
- Docker Desktop 应用已落盘，使用应用内 CLI，后台引擎启动参数 `-with-frontend=false -unattended`。Homebrew 系统链接步骤未完成，不影响应用内 CLI；Docker 29.9.2 / Compose 5.6.1。
- 随后完整重跑 `make setup && make keys && make up && make test && make test-gateway && make test-gateway-db`，exit 0；结果仍为 183 passed/48 skipped、64 passed，真实 PostgreSQL 17 passed（4.95s）。六个既有服务 healthy。
- 初始 Docker 卷清单为空；首次建立 safebank_pg-bank / safebank_pg-agent，CreatedAt 均为 2026-10-23T12:26:36Z。没有删除或重建任何卷。
- 静态核查 bankcore 当前没有 reserve/shortfall 实现；W3 先核实共享能力缺口并登记，W2 不另写一套派生算法。

## 实施中确认的交叉约定

- app.state.db 是 AsyncEngine；l3 是 L3Client；wall_now 为返回带时区时间的 callable。各处理器不创建独立连接池；缺部署接线明确登记，不用测试注入冒充生产联调。
- NormalizedScope.fields：非转账参数直接字段；predicates 对象保存 idle_subscriptions/robust_products/suspicious_cards 对象ID集合；derived_template 保存模板；转账用现有 ScopeTransfer，本人目标指纹使用 int:target_account_id。
- W3仅给自己拥有的 PreflightResult 增加 view/shadow_state，WorkflowResult 增加 cooldowns，保持 EvalContext 不变；W2不改context.py。
- W3 append_evaluation 锁plan、分配revision与evaluation_id、更新ctx.decision，再调用W4 build_view，存view_hash；返回evaluation mapping并带view供复用。所有结论都构建view，以便绿色执行具有final_view_hash。既有签名不变。
- W4拒绝审批审计事件使用 APPROVAL_REJECTED，W3防重试窗口取实际审计wall时间；DENY取evaluations.created_at_wall。
- FIRST_TRANSFER_TO_PAYEE 使用快照已有全历史transactions，不用ops_7d缺记录推断“从未”。
- IRREVERSIBLE_STEP升红按PRD §7.7仅挂失/申购，不将工具展示用irreversible旗标一概当作升红条件。
- 共享冻结缺口另见 wave1-shared-gaps.md；用户决定前不变更冻结区。

### W4 启动时需确认

- W1取消原话将ACTIVE→CANCELLED，保留EXECUTING/UNKNOWN计划与claims；执行终态消费不得尝试非法CANCELLED→CONSUMED，需在客户锁内按实际终态处理。
- W3检查点3bcbe8e并非完成：G4/repo/replay仍未验收，拒绝wall时间仍待修；所有“拟定/约定”不等于代码已联通。
- W3 resolved_steps当前带来源/metadata，W4重建领域steps须核对真实契约，不能直接传metadata给L3；跨模块未定之处先回报协调者。
- S1集中runtime将注入settings，并需与W4收敛锁服务初始化接口；固定customer_lock(customer_ids)签名不变，创建资源只在集中生命周期。
- 用户已批准受控共享依赖补缺；W4仍只修改原白名单，缺状态表/错误码/数据库字段必须列待决，由共享任务串行处理。

- 环境纠正：原PG fixture镜像隐含VOLUME，虽未显式挂卷也可能创建并自动清理匿名卷；此前“未删任何卷”不能覆盖此隐式行为。S1改显式tmpfs，后续所有PG测试使用修正夹具；两个safebank持久卷保留。
- W2对echoed新收款人+转出判TAINTED后，即使流水线DENY短路⑤–⑦，G4仍须按受污染指纹持久化24h cooldown；不得因workflow未运行漏掉。
