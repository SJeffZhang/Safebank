# W4c 任务书：G5 计划状态 / G8 执行与恢复（含 EXECUTE_REPLAY 复演）

2026-10-26；worktree `codex/w4c-execute`，基线 W3b 候选 `b8fbb8e`（含已 APPROVE 的 W3b）。
配套事实与待裁定：`docs/plans/briefs/W4c-design-notes.md`（本任务书引用其中的 Q1–Q6）。

---

## 0. 目标

实现 L2 的执行阶段两端点，使一笔已授权计划（ALLOW 或已确认审批）真正落到 L3 且**只记一次**：

- **G8 `POST /plans/{id}/execute`**：客户锁 → 新快照复演（`replay()`，EXECUTE_REPLAY 模式）→ 审批/claim 校验 → L3 `prepare`/`execute`（attempt、evidence、`deadline_cap_wall` 不变式）→ 终态归档；处理 `STATE_CHANGED`（ADR-008 屏障）、`202 UNKNOWN`、`409 EXECUTION_IN_PROGRESS`、迟到请求、取消/撤销竞态。
- **G5 `GET /plans/{id}`**：返回 `{plan_id, status, decision, execution?}`；对 UNKNOWN job 做内联对账（只 query/reconcile，绝不盲目重提）。

**核心不变式（本组件的一切测试都要能证明）：**
1. 一笔 plan 在 L3 最多 COMMITTED 一次（幂等键 `(customer_id, plan_id)` + L2 `execution_jobs` 单行）。
2. 任何 L3 HTTP 都发生在**短事务之外**；事务内绝不发 HTTP。
3. 复演等价只认 `view_hash` 逐字节相同；不同即 STATE_CHANGED，绝不静默执行旧视图。
4. `execution_jobs.status` 只经 S1b/S1e 专用 CAS 助手迁移，绝不 `transition()`、绝不手写 UPDATE。
5. 终态归档才消费 claim 与原话；live job 期间 claim 保持 CLAIMED。

---

## 1. 接口规格

### G8 `POST /plans/{plan_id}/execute`

- 请求体：`{request_id}`（G8 幂等键，**不**接受 `steps` / `approval_id` / 其它字段；多余字段拒绝）。
- 调用方：F+app 或 A+assistant，按 JWT `origin` 约束（见 §2）。
- 响应：
  - 成功执行：`200` `ExecutionResult`（PRD §9.5：`{plan_id, status: EXECUTED|FAILED, steps, versions, committed_at?, error_code?, replayed: bool}`）。
  - 复演不一致 / 屏障后重决策：`409 STATE_CHANGED`，`details.decision` 为**新 Decision**（L1 按 PRD §6.1.1 路由；App 取新 approval）。
  - 结果不明：`202 {plan_id, status: "UNKNOWN"}`（wire 模型 `app/schemas/execution.py:ExecutionAccepted`）。
  - 并发 / 迟到 live：`409 EXECUTION_IN_PROGRESS`。
  - 已完成计划的新 request_id：`200` 原 `ExecutionResult` 且 `replayed=true`（见 §7.5）。
  - 已有 request_id 曾返回终态：重放原响应（见 §7.5）。
  - 客户锁丢失：`503 DEPENDENCY_UNAVAILABLE`。
  - L3 不可用且非终态：`503 DEPENDENCY_UNAVAILABLE`（DB 状态见故障矩阵 F1/F6）。

### G5 `GET /plans/{plan_id}`

- 调用方：F+app 按 plan_id 查询；A+assistant **限 sid**（只能查 `sid == caller.sid` 且 `customer_id == caller.customer_id` 的计划）。
- 响应：`{plan_id, status, decision, execution?}`：
  - `status`：`plans.status`（PlanStatus）。
  - `decision`：最新 evaluation 的决策子集（`decision`、`risk`、`approval_id`、`evaluation_id`、`revision`）。
  - `execution?`：job 终态时取 `execution_jobs.result`（完整 `ExecutionResult`）；UNKNOWN 时 `{status: "UNKNOWN"}`；无 job 或 live 时 `null`。
- 副作用：读到 `execution_jobs.status == UNKNOWN` 时执行内联对账（§7.6），对账能定论则归档并返回终态结果。

> G5 的响应 wire 模型需新建（design-notes Q2）。形状按上面定义，除非协调者裁定变更。

---

## 2. 调用方矩阵与 origin 约束

| 端点 | F+app（origin=app_form） | A+assistant（origin=agent） |
|---|---|---|
| G8 | 允许，`plan.origin == app_form` | 允许，`plan.origin == agent` |
| G5 | 按 plan_id（+customer_id） | 仅 `sid == caller.sid`（+customer_id） |

- `origin` 来自 `Caller.origin`（`schemas/identity.py`：A→agent、F→app_form），**不来自请求体**。
- G8 加载计划后必须断言 `plan.origin == caller.origin` 且 `plan.customer_id == caller.customer_id`，否则 `403`/`404`（按既有 caller 依赖口径，见 W0 评审约定）。
- 撤销/epoch 变化见 §7.8。

---

## 3. 全局锁序（沿用 B5，不新增反向锁）

`session → plan → evaluation → approval → otp_challenge`；`execution_jobs` 经 `_job_update` 内部按 `plan → evaluation(job)` 顺序。

G8 每条写路径必须在**同一连接、同一顺序**取得行锁；G5 只读路径不加行锁（用快照读 + 必要时的对账写事务单独成 tx）。违反锁序用死锁红测抓（见 §10）。

---

## 4. 状态机迁移表（专用 CAS，勿用 `transition()`）

`execution_jobs.status` 与 plan 决策态在 G8 内的合法迁移（全部来自 `state.py` 已实现助手）：

| 触发 | 助手 | job: from→to | plan 变化 | claim/原话 |
|---|---|---|---|---|
| 首次提交执行 | （新建行） | — → EXECUTING | EVALUATED/CONFIRMED → EXECUTING | 校验 CLAIMED，不消费 |
| execute 明确 STATE_CHANGED | `begin_fencing` | EXECUTING → FENCING | 仍 EXECUTING | 保留 |
| 同 attempt fence prepare 得到 PREPARED ACK | `acknowledge_fence` | FENCING → PAUSED（deadline_wall=FENCE_AT） | 仍 EXECUTING | 保留 |
| 复演重决策后返回 | `publish_paused_decision` | （不动 job） | EXECUTING → PENDING_APPROVAL/DENIED/REPLAN/EVALUATED | 保留 CLAIMED |
| 重新确认后重放 | `resume_paused_execution` | PAUSED → EXECUTING（attempt+1） | → EXECUTING | 保留 |
| 结果不明（超时/丢包） | `mark_execution_unknown` | EXECUTING/FENCING/PAUSED → UNKNOWN | → UNKNOWN | 保留 |
| 对账定论：L3 COMMITTED | `archive_execution_terminal(l3_status="COMMITTED")` | UNKNOWN → EXECUTED | → EXECUTED | CONSUMED + 原话 CONSUMED |
| 对账定论：L3 未提交 | `archive_execution_terminal(l3_status="CANCELLED")` | UNKNOWN → FAILED | → FAILED | CONSUMED + 原话 CONSUMED |
| L3 直接返回 COMMITTED（无超时） | `archive_execution_terminal(l3_status="COMMITTED")` | EXECUTING → EXECUTED | → EXECUTED | CONSUMED + 原话 CONSUMED |
| L3 直接返回 CANCELLED/失败 | `archive_execution_terminal(l3_status="CANCELLED")` | EXECUTING → FAILED | → FAILED | CONSUMED + 原话 CONSUMED |
| 执行中取消/撤销/超时失效 | `mark_execution_unknown` → 对账 | EXECUTING → UNKNOWN → … | 视对账 | 视对账 |

- 决策态 `plans.status` 的其它迁移（PENDING_APPROVAL→CONFIRMED 等）由 W3b 已实现的 G6/G7 负责，**不在 W4c 内重复实现**；W4c 只消费 CONFIRMED/EVALUATED 起点与 EXECUTING/终态终点。
- `resume_paused_execution` 的校验（evidence 一致、deadline_cap 不变、approval CONFIRMED、风险一致、`final_view_hash == evaluation.view_hash`）由该助手完成；W4c 调用前必须把复演后的新 evaluation 与 evidence 准备好。

---

## 5. G8 执行流程与事务边界（事务内不发 HTTP）

`customer_lock([customer_id])`（`locks.py`）**贯穿整个 G8**，返回一个连接。以下每步中的「写事务」都在该连接上短事务提交；「HTTP」一律在事务外。

### 5.0 前置：request_id 重放检查（读事务）
短读事务查 `request_dedup`（route=`POST /plans/{plan_id}/execute`）与 `plans`/`execution_jobs`：
- 已有该 request_id 的**终态**响应 → 直接重放（见 §7.5）。
- 其它情况 → 进入 5.1。

### 5.1 加载 + 校验（读事务，可含 L3 快照）
- 锁读 `session → plan → evaluation(最新 revision) → approval(如 CONFIRMED)`（行锁）。
- 校验：plan.origin/customer_id 与 caller 一致；`session.revoked_at_wall is null` 且 `authorization_epoch` 与 caller 一致；plan.status ∈ {EVALUATED, CONFIRMED}；CONFIRMED 时 approval.status == CONFIRMED 且未过期、`risk_ack` 已满足（红级）。
- `_claims` 校验：scope_ids 唯一、完整、全部 CLAIMED。
- **HTTP**：L3 `snapshot`（新快照）→ 构造新 `EvalContext` → `replay(ctx)`（`pipeline.evaluate(ctx, mode=EXECUTE_REPLAY)`）。
- 复演判定（`view_hash` 逐字节）：
  - 等价（CONFIRMED：新 view_hash == approval.view_hash；EVALUATED/ALLOW：新 view_hash == evaluation.view_hash 且 decision 仍 ALLOW）→ 进入 5.2。
  - 不等价 → **STATE_CHANGED 路径**（§6）：写事务持久化新 evaluation/approval + 返回 409。

### 5.2 首次提交执行（写事务，无 HTTP）
同一写事务：
1. `append_evaluation`（`evaluation/repo.py`）落新 evaluation（revision+1，`equivalent_to=旧 evaluation_id`；view_hash/view_json/decision/risk/versions/clock_version/policy_version 来自复演）。
2. 构造 `InteractiveEvidence`（字段须与 `_stored_submission`/`resume_paused_execution` 的校验一致）：
   `origin=plan.origin`、`scope_ids=plan.scope_ids`、`utterance_ids=plan.utterance_ids`、`plan_hash=plan.plan_hash`、
   `evaluation_id=新 evaluation.id`、`approved_evaluation_id=approval.evaluation_id 或 None`、`approval_id=approval.id 或 None`、
   `confirmed_view_hash=approval.view_hash 或 None`、`final_view_hash=新 evaluation.view_hash`、`risk=新 evaluation.risk`、
   `confirmed_at_wall=approval.confirmed_at_wall 或 None`、`authorization_epoch=session.authorization_epoch`。
3. `INSERT execution_jobs(status=EXECUTING, l3_attempt=1, deadline_wall, deadline_cap_wall, authorization_evidence)`；
   `deadline_wall` 由执行受理时刻 + 授权窗口规则计算，`deadline_cap_wall` 必须等于复演/审批得出的 cap（`deadline_cap_wall` 不变式，ADR-006）。
4. `transition(plans, EVALUATED|CONFIRMED → EXECUTING)`。
5. audit append（执行路径，同一事务）。
- **COMMIT**。此后才开始任何 L3 写 HTTP。

### 5.3 L3 prepare（HTTP，事务外）
`l3.prepare(plan_id, customer_id, attempt=1, deadline, evidence, resolved_steps, …)`。
- `L3NotDelivered` → 同 attempt 有界重试（`retry_count+1`，不重持久化，L3 靠 `(customer_id, plan_id)` 幂等）；耗尽 → 走 UNKNOWN（§7.6）→ 归档。
- `L3OutcomeUnknown` → 走 UNKNOWN（§7.6）。
- `L3BusinessError STATE_CHANGED` → 首次 prepare 前**无屏障**（PRD §7.10.4）：重跑复演（最多 2 次）；超过 2 次 → 409 STATE_CHANGED + 新 Decision。
- 其它 `L3BusinessError`（如 EXECUTION_CANCELLED）→ 归档 FAILED。

### 5.4 L3 execute（HTTP，事务外）
`l3.execute(plan_id, customer_id, expected_versions, clock_version, …)`。
- `200/EXECUTED` → `archive_execution_terminal(from=EXECUTING, l3_status="COMMITTED", result)` → 返回 ExecutionResult。
- `CANCELLED/FAILED` → `archive_execution_terminal(from=EXECUTING, l3_status="CANCELLED", result)` → 返回 ExecutionResult(FAILED)。
- `STATE_CHANGED` → ADR-008 屏障（§6）。
- `L3OutcomeUnknown` → `mark_execution_unknown` + 内联对账（§7.6）→ 能定论归档，不能定论 202 UNKNOWN。
- `L3NotDelivered`（连接拒绝，一定未到 L3）→ 同 attempt 有界重试 execute；耗尽 → `mark_execution_unknown` + 内联对账。

### 5.5 终态归档（写事务，无 HTTP）
`archive_execution_terminal` 在同一写事务内：job → 终态、plan → 终态、claims CLAIMED→CONSUMED、utterances ACTIVE→CONSUMED、audit append。此后把终态响应写入 `request_dedup`（同一或后续短事务）。

---

## 6. ADR-008 STATE_CHANGED 屏障（execute 明确返回 STATE_CHANGED）

仅当**当前 attempt 已成功 prepare 且 execute 明确返回 STATE_CHANGED** 时进入屏障：

1. `begin_fencing(conn, expected)`：EXECUTING → FENCING（调用方证明 STATE_CHANGED）。
2. **HTTP**：fence prepare（同 attempt，`deadline_wall=min(原, FENCE_AT)`，`deadline_cap_wall` 不变）→ 得到 PREPARED ACK（验证 L3 未提交新 intent）。
3. `acknowledge_fence(conn, expected)`：FENCING → PAUSED，`deadline_wall=FENCE_AT=1970-01-01T00:00:00Z`。
4. **HTTP**：重跑复演（新快照，EXECUTE_REPLAY）得新 Decision。
5. `guard_paused_execution` + `append_evaluation`（新 revision）+ `publish_paused_decision(conn, expected, to=新决策态, approval_id=新 approval.id 或 None, now)`：plan → PENDING_APPROVAL / DENIED / REPLAN / EVALUATED，job 保持 PAUSED、claim 保留 CLAIMED。
6. 返回 `409 STATE_CHANGED`，`details.decision` = 新 Decision（含新 approval_id，若有）。

- 重新确认后，App/L1 再次 G8 → 5.1 校验 + `resume_paused_execution`（attempt+1）→ 5.3 起继续，**不复用旧 attempt**。
- **Q6 前提**：真实 L3 必须接受 fence prepare 的过期 `deadline_wall`（同 attempt `min` 规则）；若实测被拒，屏障无法走通，停下上报（见 design-notes Q6）。

---

## 7. 恢复与幂等

### 7.1 结果不明（UNKNOWN）
`L3OutcomeUnknown`（execute/prepare 读/写超时）→ `mark_execution_unknown`（job 任一 live 态 → UNKNOWN，plan → UNKNOWN，**保留 claim**）→ 内联对账。

### 7.2 内联对账（只 query/reconcile，绝不重提 execute、绝不重复记账）
- `l3.query(plan_id, customer_id)`：
  - `COMMITTED` → `archive_execution_terminal(l3_status="COMMITTED", result=query.result)`（结果 replayed，`replayed=true`）。
  - `PREPARED` / 其它未提交态 → `l3.reconcile(plan_id, …)`（L3 写不可逆 CANCELLED 墓碑，幂等）→ `archive_execution_terminal(l3_status="CANCELLED", result=None|FAILED)`。
  - 404（从未 prepare）→ `reconcile` 生成 CANCELLED 墓碑 → 归档 FAILED。
- 对账本身也 `L3OutcomeUnknown` → 保持 UNKNOWN，返回 202（G8）/ `execution={status:UNKNOWN}`（G5）。

### 7.3 触发点（Q3 待裁定，先按此实现）
- G5 读到 UNKNOWN job；G8 对新 request_id 命中 UNKNOWN job。启动扫描/周期工作器**不在 W4c 范围**（留 W5）。

### 7.4 迟到请求 / 并发
- 同 plan 并发 G8：`customer_lock` 串行化；后到者读到的 plan 已 EXECUTING → 409 `EXECUTION_IN_PROGRESS`（锁竞争 55P03 或状态判断）。
- plan 已终态的新 request_id → 200 原 `ExecutionResult` 且 `replayed=true`（PRD §9.5），**不产生新提交**。

### 7.5 G8 request_id 重放（Q4 待裁定，先按此实现）
- 同一 `(customer_id, plan_id)` 的 G8 request_id 曾返回**终态** ExecutionResult → 重放该响应（`request_dedup`）。
- 曾返回 202/409（非终态）→ **不**重放，走「当前状态路径」（7.4）。
- 存储：终态响应写 `request_dedup`（route=`POST /plans/{plan_id}/execute`）；**不在 `request_idempotent` 的 produce 里做任何 L3 HTTP**。

### 7.6 取消/撤销 vs 执行竞态（F8）
执行写事务内复核 `session.revoked_at_wall is null` 且 epoch 一致；若已撤销 → 中止，返回 `403 UNAUTHENTICATED`/`409`，不触达 L3。

---

## 8. 故障矩阵（期望 DB 结果 + 测试）

| # | 故障 | 检测 | 动作 | 期望 DB 终态 | 测试 |
|---|---|---|---|---|---|
| F1 | prepare 连接拒绝（未到 L3） | `L3NotDelivered` | 同 attempt 有界重试；耗尽 → UNKNOWN → reconcile(CANCELLED) → 归档 | 耗尽：job FAILED、plan FAILED、claims CONSUMED、L3 无 intent（或 CANCELLED 墓碑） | 真实 L3 + 连接拒绝 transport |
| F2 | prepare 读/写超时（可能已到） | `L3OutcomeUnknown` | mark UNKNOWN → query → 定论归档 | job EXECUTED 或 FAILED；plan 同步；claims CONSUMED | MockTransport write timeout |
| F3 | execute 明确 STATE_CHANGED | `L3BusinessError 409` | 屏障 → 复演 → publish → 409 | job PAUSED、deadline_wall=FENCE_AT、plan 回到新决策态、claims CLAIMED | 真实 L3 版本/时钟冲突 |
| F4 | execute 提交后回包丢失 | `L3OutcomeUnknown` | mark UNKNOWN → query(COMMITTED) → 归档 | job EXECUTED、plan EXECUTED、claims CONSUMED、**L3 只记一次** | 真实 L3 + 响应丢弃（Q5） |
| F5 | execute 进程崩溃后恢复 | 进程退出 | 恢复时 query → 归档 | 同 F4 | 真实 L3 进程重启（Q5-B） |
| F6 | 客户锁丢失 | `LockOwnershipLost` | 立即 503，不写终态 | DB 不变 | 锁被并发释放 |
| F7 | 并发 G8（同 plan） | 锁竞争 55P03 / 状态 | 409 EXECUTION_IN_PROGRESS | DB 不变 | 双 HTTP 并发 G8 |
| F8 | 执行中撤销 session | 写事务内复核 | 中止，不触达 L3 | 无 intent、不记账 | 执行中并发注销 |
| F9 | G8 迟到（plan 已终态） | 加载发现终态 | 200 原 result + replayed=true | DB 不变 | 重复 G8 |
| F10 | UNKNOWN 对账发现 PREPARED 未提交 | query 返回 PREPARED | reconcile → CANCELLED 墓碑 → 归档 FAILED | job FAILED、plan FAILED、claims CONSUMED | 超时 transport |

---

## 9. 证据边界：真实 L3 vs FakeL3

- **「只记一次」「迟到竞争」「失锁」「崩溃恢复」必须用真实 L3**：复用 `tests/gateway/w3a/db/test_real_preflight.py` 的 `_serve_l3` 模式（真实 `create_app` + uvicorn 线程 + `install_core`/`install_obligations` + `L3Client`），每次断言 L2 与 L3 双侧 DB 终态（`execution_jobs.result`、`plans.status`、`authorization_claims.status`、L3 `execution_intents`/账本/流水）。
- `FakeL3` 仅用于：L3 异常分类的单元断言、非终态逻辑（STATE_CHANGED 决策构造、UNKNOWN 状态机分支）的快速路径。**不得**把 fake 场景写进验收的「只记一次/竞争/恢复」证据。
- 响应丢弃 / 连接拒绝的确定性构造：Q5（写 timeout 用 `httpx` transport 包一层丢弃响应；连接拒绝用指向未监听端口的 client；进程重启用 S1/P1 的 kill + 重启线程模式）。

---

## 10. 红测要求（red-first-green）

每条行为测试先构造「旧实现/错误实现」能失败的红测，再转绿；**红测必须真的失败过并记录**（不能只写断言）。重点红测：

1. **`replay()` 非 stub**：删除 `execution/replay.py` 的 `raise NotImplementedError` 前，G8 happy path 红测应失败；实现 `replay()` 后转绿。
2. **只记一次**：对同一 plan 重复 G8（含 F4 响应丢弃后的恢复），断言 L3 账本只变一次；把归档幂等去掉会失败。
3. **STATE_CHANGED 屏障**：execute 返回 STATE_CHANGED 时，把 `acknowledge_fence` 的 `deadline_wall=FENCE_AT` 去掉 → 屏障红测失败。
4. **锁序**：把 G8 写路径的 `_lock_session` 与 `_lock_approval` 对调 → 死锁红测失败（参照 W3b 的 AST 守卫 + 真实并发）。
5. **不盲目重提**：UNKNOWN 恢复路径若调用 `execute`（而非仅 query/reconcile）→ 红测失败（用记录 L3 调用次数的 transport 断言 execute 只被调用 1 次）。
6. **claim 消费时机**：把 claim 在 EXECUTING 阶段就 CONSUMED → 红测失败（终态才消费）。
7. **origin 约束**：A 调用 origin=app_form 的 plan → 拒绝红测。

---

## 11. 全局约束 / 冻结清单 / 所有权白名单

### 所有权（只允许改）
- `services/gateway/app/api/execution.py`（G5/G8 处理器）
- `services/gateway/app/execution/replay.py`（实现 `replay()`）
- `services/gateway/app/execution/*`（如有新增执行/恢复模块）
- `services/gateway/app/schemas/*`（G5 响应模型等新 wire 模型，按需要）
- `tests/gateway/w4c/**`（新建）
- `Makefile`（新增 `test-w4c` 目标）
- `.github/workflows/checks.yml`（p1-postgres 追加 `make test-w4c` 一行）
- `docs/plans/briefs/W4c-*.md`、`docs/acceptance/L2-W4c-*.md`、`docs/progress/*`

### 冻结（不擅自改，除非红测证明必要且先提待决）
- `services/gateway/app/state.py`（S1b/S1e 专用 CAS 助手；若需新增助手，先提待决）
- `services/gateway/app/locks.py`、`app/l3/client.py`、`app/l3/fake.py`
- `packages/contracts/**`、`packages/bankcore/**`、`services/bank-backend/**`（真实 L3 只读调用，不改）
- `services/gateway/app/pipeline/*`（复演走既有 `evaluate(mode=…)`；若 EXECUTE_REPLAY 需要 stage 差异，先列最小差异清单待裁定——Q1）
- `services/agent/**`、`services/gateway/app/api/plans.py`（G4）、W1–W3b 的测试
- 迁移文件（**不新增迁移**；execution_jobs 已含全部列）
- `AGENTS.md`、根 `PRD.md`（worker 禁改）

### 通用约束
- 单条 shell 命令；不 commit/push；不读取/打印 `.env`/`.secrets`/`~/.claude/settings.json`。
- 金额整数分、无 float；`_cents` 字段。错误码用 `safebank_contracts.codes.Code` 既有枚举，不新造（G5/G8 相关已有 code 见 PRD §9.8）。
- 真实 PG 用 tmpfs fixture，不碰长期业务卷；只清理测试专属临时库。
- 验收必须含 `make test-w4c` 真实 PG 专项 + 既有全套回归；未运行的不得写通过。

---

## 12. Task 清单（red-first-green，逐任务提交检查点）

- [ ] T0 检查点：在 `codex/w4c-execute` 建立 `tests/gateway/w4c/` 骨架（复用 `tests/gateway/db/conftest.py` 的 `gateway_db` + `test_auth.keys` + `obligations_db`），`make test-w4c` 空收集也 exit 0；确认基线全量退出通过。
- [ ] T1 `replay()`：实现 EXECUTE_REPLAY 复演（新 EvalContext + L3 snapshot + `pipeline.evaluate(mode=EXECUTE_REPLAY)`）；红测：非 stub、复演 view_hash 与执行前校验一致。
- [ ] T2 G8 happy path（ALLOW，真实 L3）：5.1→5.5 全流程；红测：只记一次、attempt=1、evidence/cap 不变式、终态消费 claim。
- [ ] T3 G8 happy path（CONFIRMED，真实 L3）：approval 校验 + view_hash 等价判定；红测：view_hash 不等价 → STATE_CHANGED（不等价绝不执行）。
- [ ] T4 STATE_CHANGED 屏障：begin_fencing→fence prepare→acknowledge_fence→复演→publish_paused_decision→409；红测：去 fence 不通过、PAUSED 后 claim 保留、resume_paused_execution attempt+1。
- [ ] T5 UNKNOWN 恢复（只 query/reconcile）：F1/F2/F4/F5/F10；红测：恢复绝不调用 execute、L3 只记一次。
- [ ] T6 迟到/并发/撤销：F7/F8/F9 + origin 约束（F 不能执行 agent plan，A 不能执行 app_form plan，A 的 G5 限 sid）；红测：并发 409、撤销中止。
- [ ] T7 G5 + request_id 重放：G5 响应模型、UNKNOWN 内联对账、request_dedup 终态重放；红测：非终态 202/409 不重放、终态 replayed=true。
- [ ] T8 `make test-w4c` 目标 + checks.yml 一行 + 全量回归 + 验收文档 `docs/acceptance/L2-W4c-execute.md`。

---

## 13. `make test-w4c` 与 CI

- `Makefile` 新增（仿 `test-w3b`）：
  ```make
  test-w4c:
  	GATEWAY_DB_TESTS=1 .venv/bin/pytest tests/gateway/w4c -q
  ```
- `.github/workflows/checks.yml` 的 p1-postgres job 在既有 `make test-w3b`（或相邻专项）之后追加一行 `make test-w4c`（不删、不改其它行、不改 job 名）。

---

## 14. 退出命令（全部 exit 0 才可交付；真实 PG 用 Docker 应用内 PATH）

```bash
make setup
make lint
make test
make test-gateway
make test-gateway-db
make test-w4c
make contracts-check
make test-agent-db        # 若不可用则记 skipped，并说明
actionlint               # 若未安装，先 brew install actionlint
```

> 真实 L3 专项（test-w4c 内）必须实际建立 tmpfs PG + 起真实 bank-backend uvicorn 线程；若 Docker 不可用，记录为外部阻塞，**不**写通过。

---

## 15. 不在范围

- 启动扫描 / 周期恢复工作器（W5）。
- G10 OTP、G6/G7 审批确认、G9 面板（W4b2b/W4b2c 已实现，W4c 只消费其产物）。
- L3 scheduler 回调 `/internal/scheduled/authorize`（W5）。
- G8 的 body 若含 steps/approval_id → 拒绝，但不实现「接受 body」路径。
- 前端 / BFF / L1 图改动。
- 新迁移、新错误码枚举（复用既有）。

---

## 16. 协调者裁定（待填）

实现前请协调者裁定 design-notes 的 Q1–Q6，尤其：
- **Q3**（UNKNOWN 恢复触发范围：只做 G5/G8 内联对账，还是加启动扫描）
- **Q4**（G8 request_id 重放：复用 request_dedup 只存终态）
- **Q6**（fence prepare 的过期 deadline 在真实 L3 是否被拒——若被拒，屏障设计需调整，先停下上报）

未裁定前，按本文 §5–§8 的实现建议与红测先行推进；任何与 Q1–Q6 冲突处以上报为准。

## 协调者裁定（2026-10-26，与上文冲突时以本节为准）

- **C1 replay 归 W4c**（Q1）：W3b 没有实现 `execution/replay.py`，改由 W4c 实现。要求：每次调用都新建 EvalContext，并取新的 L3 snapshot，不得复用 G4 时缓存的快照或结果。允许为 EXECUTE_REPLAY 模式对 `pipeline/*` 做最小改动，动手前先在进度文件里列出差异清单。replay 必须与 G4 共用同一套 ⑤–⑨ 阶段和 bankcore 预演，不得另写一套判定。
- **C2 G5**（Q2）：采用设计稿方案，新增 wire 模型 `{plan_id, status, decision, execution?}`。A+assistant 调用者只能查询本 sid 下的计划，F+app 调用者只能查询本客户的计划。
- **C3 UNKNOWN 恢复**（Q3）：本组件只在 G5/G8 遇到 UNKNOWN job 时内联对账：先 query；结果为 COMMITTED 时归档为 EXECUTED；明确未提交时 reconcile 后归档为 FAILED；对账本身仍不明确时保持 UNKNOWN。全程只允许 query/reconcile，绝不重新提交，也不重复记账。启动扫描和后台恢复 worker 归 W5，已登记为待办。
- **C4 G8 幂等**（Q4）：`request_dedup` 只缓存终态响应（EXECUTED/FAILED/CANCELLED 等）；非终态时按当前状态路径返回 202 UNKNOWN 或对应的 409。持有 request_dedup 的事务里绝不发 L3 HTTP。按 ADR-008，用旧的 G8 request_id 重放不得触发新的 attempt。
- **C5 故障注入**（Q5）：两种手段都用。「L3 已提交但回包丢失」和超时，用 httpx transport 包装做到可确定复现；进程崩溃后恢复，至少要有一项真实测试，即真实 bank-backend 应用加 gateway 重建，参照 S1/P1 的做法。
- **C6 ADR-008 前提**（Q6）：Task 0 先用真实 L3 验证「deadline 已过期的同 attempt prepare 不会被挡掉」。验证不通过就**停止**，在进度文件里写明证据并上报，不得绕过屏障，也不得放宽 L3 校验。
- **C7 锁序与范围**：沿用 B5 锁序 session→plan→evaluation→approval→otp_challenge，客户锁只能用 W4a 的 `customer_lock`，并说明客户锁与上述行锁之间的相对顺序。不新增迁移，不改 contracts。fake 与真实 L3 的证据边界要在测试名和文档里标清。

### 协调者裁定 C8（2026-10-26，修订 C7「不加迁移」）

- 事实：PRD §7.10 第 4 项要求执行前「在新快照上重跑 ①–⑧」，而阶段 ①–④（实体、来源与污点、意图）需要原始 PlanDraft（含 `arg_sources`）；`plans` 表只存 `resolved_steps`/`plan_hash`，**没有**原始 PlanDraft，复演无从重跑。T2c 的 WIP 以 `plan_draft=None` 构造复演上下文，实体阶段因此直接返回，等于不复核——判为不可接受。
- 裁定：新增 gateway 迁移 `0003_plan_draft`，`plans.plan_draft JSONB NULL`（旧行 NULL，不回填）；G4 在插入 plans 的同一事务写入 `PlanDraft.model_dump(mode="json")`；G8 用 `PlanDraft.model_validate(plans.plan_draft)` 构造复演上下文，授权输入复用 G4 的 `_load_authorization`；`plan_draft IS NULL` 的计划 G8 一律 503 DEPENDENCY_UNAVAILABLE、DB 不变。PRD §7.2 `plans` 行已同步。
- 拆分：T2d 迁移 + tables + G4 写入 + 漂移/升级测试；T2e 在 T2d 之上完成 G8 ALLOW 路径（T2c 的 execution.py WIP 已放在 `git stash`「W4c-T2c2 WIP」，可参考，不得照搬 `plan_draft=None`）。
