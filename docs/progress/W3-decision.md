# W3 快照、预演、工作流、决策进度

基线 main 8555bf0；分支 feat/l2-decision；执行者 Codex。只在分配的 worktree 本地实现与提交。

## 组件与验收计划

1. 快照 / 已支持领域预演：输入 EvalContext，输出钉住的快照、bankcore Simulation 与 PreflightView；使用 FakeL3 transport，测试同快照缓存、B1 独立/B2 累积、不可写 L3、已支持领域一致性。
2. 工作流 / 聚合：输入快照 ops_7d 与 gateway 历史，输出 findings、冷静期、Decision；表驱动测试 A1/A3、换序/无卡、sim/wall 边界、风险单调、profile。
3. G4 / append_evaluation / replay：HTTP 不在事务内；DB 持久 request 幂等、行锁修订、状态迁移、审计；W1/W2/W4 未合入前打桩；隔离真实 PostgreSQL 测试。

## 待决（已报协调者）

- bankcore 无 reserve / projected_shortfall；banking.apply_plan 只支持七工具，其余返回 TOOL_NOT_ALLOWED。PRD §7.7 的 7 天预留、295500 hint、SCHEDULED_SHORTFALL 与未来定时预演需要统一领域补缺，不在 gateway 重写算术。
- BankState 无 merchant_billing 原始义务与充分的 time_of_day / 客户账户关联。contracts / bankcore / L3 冻结；需协调者决定共享增量。
- 请求协调者统一增加 PreflightResult.view: PreflightView | None = None、shadow_state: BankState | None = None；WorkflowResult.cooldowns: tuple[tuple[str,datetime], ...] = ()。EvalContext 结构保持不变，W3 不私改共享文件。
- workflow_events 没有 wall 时间字段；DUPLICATE_PLAN 可由 plans.created_at_wall + status=EXECUTED 查询；REJECTED_RETRY 可由 evaluations.created_at_wall + plans.status 查询。拒绝审批时间缺失，需迁移 rejected_at_wall 或统一网关事件时间字段，不能把旧创建时间当拒绝发生时间。

## 实际命令与结果

- 阅读 AGENTS、任务书、公约、指定 PRD、W0 评审及现有代码。首次 cat 使用错误 bankcore 包路径 exit 1（已用 rg --files 找到真实 src 路径）；无实现测试运行。

## 共享补缺精确规格（待用户/协调者裁定，不私改）

- 缺失写工具：schedule_transfer、cancel_scheduled、create_split_request、cancel_subscription、submit_risk_assessment、subscribe_product、redeem_product（banking.SUPPORTED_WRITES之外，当前apply_plan均TOOL_NOT_ALLOWED）。PRD §7.7.1要求预演/执行共用apply_*，应由领域实现任务补齐；W3不能伪造。
- 所需预留输入：BankState.subscriptions 当前包含真实账单 amount_cents、account_id、period、next_due_at、status；BankState.instructions 含from/to/payee、金额、period、anchor_day/weekday/time_of_day/next_run_at/end_at/status，字段可覆盖已投影义务；但L3当前loader不加载这两组数据，数据库也没有对应业务实现。未加载的空元组不能当成“客户无义务”。应明确增加快照能力/数据完整性标记，如 reservation_complete:bool（默认false）或数据版本能力清单，以区分真实空与未实现空。
- 建议统一 bankcore.reserve.projected_shortfall(state: BankState, *, days:int=7) -> ShortfallReport；返回 accounts 映射 `{account_id: {reserve_cents:int, shortfall_cents:int, max_outflow_cents:int}}`、total_shortfall_cents:int、total_reserve_cents:int，并可附有序 obligations（due_at,rank,id,account_id,delta_cents）；全部整数分，06:00默认，不用max_amount、不信用未来入账，本人定时目标记正delta，窗口(sim_now,sim_now+7d]。PRD §7.7。
- W2 available_less_7d_charges与W3 hint必须取同一report.accounts[account_id].max_outflow_cents；前后total_shortfall比较判SCHEDULED_SHORTFALL。创建定时首次到期日预演与30天承诺量需要统一 bankcore occurrence 展开（建议 scheduled_occurrences(instructions,start,end)），不能在gateway复制日历算术。
- 收回“首次转账需新字段”提议：已实读L3 state.py:136–145，transactions按customer_id加载全历史；FIRST_TRANSFER_TO_PAYEE改用成功P2P OUT全历史及当前计划，不把ops_7d限制套用到transactions。
- 协调者明确授权W3新增共享阶段结果字段，已在context.py仅增加PreflightResult.view:PreflightView|None、shadow_state:BankState|None，WorkflowResult.cooldowns:tuple[tuple[str,datetime],...]；原字段与EvalContext结构均不改。view供W4面板，shadow_state供策略对照，cooldowns由G4持久化；原本地结果子类已移除。
- 统一资源依赖为app.state.db（AsyncEngine）、l3（L3Client）、wall_now（aware datetime callable）；W3不创建池或L3客户端，部署接线由协调者集中完成。

## 小组件实际结果

- `.venv/bin/pytest tests/gateway/w3/test_preflight.py -q`：首次2 failed（占位未实现；并修正测试FakeL3 set_snapshot API）；实现后2 passed。
- `.venv/bin/pytest tests/gateway/w3/test_workflow.py -q`：首次4 failed（占位）；实现后4 passed，覆盖A1、A3三变体。
- `.venv/bin/pytest tests/gateway/w3/test_decision.py -q`：首次5 failed（占位）；聚合实现后W3合计11 passed。
- 上述仅组件测试，7天预留、295500 hint、SCHEDULED_SHORTFALL、未来指令尚无生产实现，不属于通过项。

## 当前检查点：PARTIAL / BLOCKED（不是W3完整交付）

协调者收到用户受控共享补缺授权后，要求本agent暂停铺新组件、提交检查点并释放并发槽。以下代码保留供共享依赖合入后续接，不作为可合并完成组件。

### 修改文件

- pipeline/snapshot.py：同缓存快照钉住。
- pipeline/preflight.py：现有bankcore七工具累积预演、B1独立预演、违规归类、客户余额差异。B2可成功的资金支出在缺共享reserve时明确503，不静默跳过；受保护动作仍可用。
- pipeline/workflow.py、workflow/sequence.py、policies/thresholds.yaml、workflow.yaml：A1/A3核心规则、精确阈值、sim窗口和冷静期内存产出；首次转账已使用全历史交易。
- pipeline/decision.py：优先级、基础等级、卡LOOSEN、B1单笔阈值、不可逆finding、内部全versions；不生成未经核实的金额hint。
- pipeline/audit.py：SHADOW_B1禁止审计的callback接口；G4最终append_tx在HTTP后独立事务。
- pipeline/context.py：只有协调者明确授权的三个结果字段增量，EvalContext完全不改。
- evaluation/repo.py：行锁串行revision、append-only、所有结论调用W4 build_view并存view_hash；返回mapping额外含view供G4/W4。
- execution/replay.py：新EvalContext，不复用旧快照/findings；复演结果槽复制回调用ctx供W4 build_view。
- api/plans.py：G4初稿，身份判别、F表单内部draft确保快照带steps、资源注入、HTTP与写事务分离、持久幂等、W1最终授权复核、W4调用、冷静期持久化、客户版本过滤。**尚未做G4/仓储/复演独立验收，不能视为完成。**
- tests/gateway/w3/test_{preflight,workflow,decision}.py：12组件测试；Makefile末尾test-w3；本进度文件。

### 尚未完成 / 后续自审关注

1. 共享reserve与能力完整性、295500 hint、SCHEDULED_SHORTFALL、未来首次定时预演、30天定时承诺量。当前生产不伪造成功。
2. REPLAN hint作用域收缩、持久hint记录；B2 shadow B1尚未接入（单独SHADOW_B1模式审计无副作用已有护栏）。
3. REJECTED_RETRY当前初稿仍用plans.created_at_wall作10分钟筛选，**不满足实际拒绝wall时刻语义**。应改为evaluations创建时间的DENY，以及W4统一APPROVAL_REJECTED审计wall时刻；先前通知协调者的“改用”是待实施方案，当前并未完成，不能当成已修。
4. 序列same-plan复杂引用/无操作、定时grant与SCHEDULED_BYPASS由W5/共享领域能力接入后验证；现阶段仅A1/A3核心变体测试。
5. G4持久化、复演、审计事务故障与并发、请求幂等过期源重试、表单/W1/W2/W4真实边界仍未测试。G4生成resolved_steps含来源与metadata，W4重建steps需先剥离metadata；该数据约定尚需W4收敛。
6. G4审计在持久化后独立append_tx，commit→audit失败间隙需明确恢复/补审计机制；当前不宣称审计故障恢复闭合。
7. G4评估预算计数已有初稿，但“最多3个审批”、作用域失效/终态消费与W1/W4应做真实接口联测；W4占位会阻止生产批准路径完成。
8. app.state统一资源由协调者部署接线（db/l3/wall_now/settings）。缺资源明确503。
9. W3真实PG测试尚未创建；make test-gateway-db通过的是既有17项W0门禁，不是G4/仓储证明。test-w3现在12单测，无DB条目，不把无DB条目算PG验收。
10. 当前使用secrets生成ID，符合前缀+26位Crockford字母表；DEMO/EVAL业务ID确定性待协调统一注入，未实现。

### 检查点退出命令（真实执行）

- ruff首次针对新增文件check --fix：22项（12自动修复、10长行待format）；随后ruff format修改11文件。此首次check非通过，后续完整make lint确认已修。
- `make test-w3`：12 passed（0.39s）；无数据库条目。
- `make lint`：exit0，All checks passed，151 files formatted。
- `make test-gateway`：exit0，76 passed（1.80s）。
- `make test`：exit0，195 passed / 48 skipped（3.76s）。
- `PATH=/Applications/Docker.app/Contents/Resources/bin:$PATH make test-gateway-db`：exit0，既有17 passed（4.62s），随机临时PG；没有操作共享数据卷。
- `git diff --check`：exit0；自审确认只在白名单及明确批准的context结果字段范围更改，未包含.env/密钥、未改迁移/contracts/bankcore/L3/PRD/AGENTS。

本检查点只本地提交，无push/PR/merge；不带虚假DeepSeek/Claude署名。
