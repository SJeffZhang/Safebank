# W4c execute 设计笔记

2026-10-26；只读代码分析（不实施、不提交）；worktree `codex/w4c-execute`，基线 W3b 候选 `b8fbb8e`（含已 APPROVE 的 W3b）。

> 本文件只陈述代码事实（file:line）、缺口、待裁定问题。任务书是 `W4c-execute.md`。
> 本笔记结论以实际读到的代码为准；旧检查点 `3856a4c`（W4 历史草稿）因工具权限未能 `git show` 读取，
> 只作为「只读参考」，不作为事实来源，下面不引用它。

## 1. 代码事实（file:line）

### 1.1 G5 / G8 目前是 stub

- `services/gateway/app/api/execution.py:9-10`：`GET /plans/{plan_id}` 与 `POST /plans/{plan_id}/execute` 都还是 `stub("… lands in W4")`。整文件 10 行，无任何实现。
- G8 的调用方矩阵、origin 约束、状态机、故障处理全部待 W4c 实现。

### 1.2 **关键缺口：`execution/replay.py` 仍是 stub，W3b 没有实现复演入口**

- `services/gateway/app/execution/replay.py:18-20`：

  ```python
  async def replay(ctx: EvalContext) -> Decision:
      """Re-run the evaluation pipeline in EXECUTE_REPLAY mode …"""
      raise NotImplementedError("replay is W3 (PRD §7.10.4-5), called by W4")
  ```

- W3b 只实现了 **G4 evaluate**（`api/plans.py` 的 EVALUATE 模式 + `evaluation/repo.append_evaluation`），
  没有实现 `replay()`，也没有实现 EXECUTE_REPLAY 模式在任一 stage 里的差异化逻辑。
- `pipeline/__init__.py:43-64` 的 `evaluate(ctx, *, stages=None, mode=None)` 已支持传入 `mode`（`ctx.mode = mode`），
  但各 stage 目前只对 `SHADOW_B1` / `EVALUATE` 做了特判：
  - `pipeline/preflight.py:171,225`：`ctx.mode is not EvalMode.SHADOW_B1`
  - `pipeline/decision.py:92`：`ctx.profile == "B2" and ctx.mode is EvalMode.EVALUATE and state is not None`（仅 EVALUATE 生成 shadow_b1）
  - 其余地方不区分 mode → **EXECUTE_REPLAY 目前与 EVALUATE 行为几乎一致**。

**结论**：`replay()` 与 EXECUTE_REPLAY 的 per-stage 差异（若有）都属于 W4c 的实现范围（见协调问题 Q1）。

### 1.3 状态机原语（`services/gateway/app/state.py`）——全部已就绪，无需新增迁移

- 普通迁移走 `transition(conn, table, *, id_, from_statuses, to, code)`（`:171`），合法边见
  `PLAN_TRANSITIONS`（`:42`）、`APPROVAL_TRANSITIONS`（`:87`）、`UTTERANCE_TRANSITIONS`（`:107`）、
  `EXECUTION_JOB_TRANSITIONS`（`:116`）、`CLAIM_TRANSITIONS`（`:149`）。
- **`execution_jobs.status` 只允许专用 CAS 助手修改**（不是 `transition()`）：
  - `begin_fencing(conn, *, expected)`（`:374`）——`EXECUTING → FENCING`（调用方证明 execute 明确返回 STATE_CHANGED）
  - `acknowledge_fence(conn, *, expected)`（`:388`）——`FENCING → PAUSED` 并 `deadline_wall=FENCE_AT`（调用方先验证同 attempt 的 PREPARED ACK）
  - `guard_paused_execution(conn, *, expected)`（`:404`）——锁 PAUSED job，供 W3 在同一事务里追加新 evaluation
  - `publish_paused_decision(conn, *, expected, to, approval_id, now)`（`:487`）——W3 新 revision 落库后把 plan 迁移到新决策态
  - `resume_paused_execution(conn, *, expected, authorization_evidence, deadline_wall, deadline_cap_wall, approval_id, now) -> int`（`:547`）——`PAUSED → EXECUTING`，attempt+1，返回新 attempt
  - `mark_execution_unknown(conn, *, expected, from_status, reason)`（`:684`）——任一 live 态 → UNKNOWN，保留 claim
  - `archive_execution_terminal(conn, *, expected, from_status, l3_status, result)`（`:707`）——live 态 → EXECUTED/FAILED，`_finish_terminal` 消费 claim + 原话
  - 孤儿路径 `OrphanExecutionIdentity` + `mark_orphan_execution_unknown`（`:858`）+ `archive_orphan_execution_terminal`（`:942`）——仅零 evaluation 的遗留 live job 清理用
- `_protect_transition`（`:642`）：普通 DML 无法伪造终态（EXECUTED/FAILED/CANCELLED）、无法在 live job 下改 plan 决策态。
- `_job_update`（`:348`）：所有 job 迁移共用的条件 UPDATE，同时约束 `l3_attempt == expected.attempt` 与 `latest revision == expected.revision`。
- `_stored_submission(plan, job) -> InteractiveEvidence`（`:329`）：校验 job 上的 `authorization_evidence`/`deadline_wall`/`deadline_cap_wall` 与 plan 的 origin/plan_hash/scope_ids/utterance_ids 一致。
- `_terminal_result`（`:882`）：`l3_status=COMMITTED` 要求 `result.status==EXECUTED` 且所有 step EXECUTED → `EXECUTED`；`CANCELLED` 允许 `result=None`（墓碑）或全非 EXECUTED 的 FAILED → `FAILED`。
- `LIVE_JOB_STATUSES = {EXECUTING, FENCING, PAUSED, UNKNOWN}`（`:632`）。

### 1.4 表字段：`execution_jobs` 已含全部所需列（`services/gateway/app/tables.py:239-255`）

`plan_id`(PK)、`status`、`l3_attempt`、`retry_count`、`last_error`、`result`、`created_at_wall`、
`updated_at_wall`、`deadline_wall`、`authorization_evidence`、`deadline_cap_wall`。
→ **无需新迁移**；`plans.request_id` 是 G4 的 request_id，不是 G8 的（G8 的 request_id 语义见 Q4）。

### 1.5 L3 真实协议（`services/bank-backend/app/main.py` + `app/execution.py`）

- 端点（`main.py`）：`POST /internal/plans/prepare`、`POST /internal/plans/execute`、
  `GET /internal/executions/{plan_id}?customer_id=`、`POST /internal/executions/{plan_id}/reconcile`、
  `POST /internal/snapshot`、`POST /internal/read/{tool}`、`GET /internal/outbox`、`POST /internal/outbox/ack`。
- `ExecutionService`（`app/execution.py`）语义（summary 已核）：
  - `prepare`：无行要求 attempt==1；CANCELLED → EXECUTION_CANCELLED；同 attempt 同 evidence 更新 `deadline_wall=min(old,new)`；
    attempt==row.attempt+1 且 row PREPARED 要求 `deadline_wall>now 且 <=deadline_cap_wall`，追加 evidence_history。
  - `execute`：40P01/40001 重试×3；55P03 → EXECUTION_IN_PROGRESS；COMMITTED → 原结果 replayed；
    version/clock/steps_digest 不符 → STATE_CHANGED；成功后 persist + COMMITTED。
  - `query` → `{status, result}`；`reconcile` → 未 COMMITTED 写不可逆 CANCELLED 墓碑，否则返回原结果。

### 1.6 L2 的 L3 客户端异常分类（`services/gateway/app/l3/client.py:23-39,80-101`）

- `L3NotDelivered`（connect/timeout 前：ConnectTimeout/PoolTimeout/ConnectError）→ 一定没到 L3，可安全重试
- `L3OutcomeUnknown`（ReadTimeout/WriteTimeout/RemoteProtocolError）→ 可能已到 L3，**只能 UNKNOWN/对账**
- `L3Unavailable`（其它 HTTPError）→ 依赖不可用
- `L3BusinessError`（≥400，携带 code/status）

### 1.7 幂等约束

- `services/gateway/app/idempotency.py` docstring 明确：**G8 的执行幂等走 plans / execution_jobs，不要把 L3/HTTP 放进 `request_idempotent` 事务**。
- `request_dedup` 表（`tables.py:343-353`）注释把 G8 列入 request_id 幂等端点清单——与上面 docstring 的措辞需要统一（见 Q4）。

### 1.8 锁

- `services/gateway/app/locks.py`：`customer_lock(customer_ids)` 异步上下文管理器，advisory `hashtextextended('cus:'||id,0)`，
  `lock_timeout=5s`、ID 排序去重、`GATEWAY_MAX_EXECUTIONS=8` 信号量、失锁检测（`assert_lock_owner`、`LockOwnershipLost`→503）。
  锁竞争 55P03 → 409 EXECUTION_IN_PROGRESS。
- 全局锁序（B5，W3b 已落地）：`session → plan → evaluation → approval → otp_challenge`；`_job_update` 内部按 `plan → evaluation(job)` 顺序。
  G8 必须沿用，不新增反向锁。

### 1.9 真实 L3 测试模式（`tests/gateway/w3a/db/test_real_preflight.py`）

- `_serve_l3(obligations_db)`：`create_app(db=runtime, service_token, fingerprint_key)` 起 uvicorn 线程 →
  真实 `L3Client(base, token)`；seed 用 `tests.obligations_data.fixtures` 的 `KEY / install_core / install_obligations`。
- `obligations_db` fixture（tmpfs PG，含 `admin_url` / `runtime_url`）来自 `tests/obligations_data/conftest.py`。
- W4c 的「真实 L3 只记一次 / 迟到竞争 / 失锁 / 崩溃恢复」测试应复用此模式，而非 `FakeL3Client`。

### 1.10 G5 线形状与 L1 路由

- PRD §9.5 G5：`GET /plans/{id}` → `{plan_id,status,decision,execution?}`；A 限 sid。
- PRD §6.1.1：execute 202 `{status: UNKNOWN}` → L1 respond「确认中」，**L1 不轮询**，App 经 F8/G5 轮询；
  409 STATE_CHANGED 带 `details.decision` → L1 按 decision 路由（REQUIRE_APPROVAL→await_approval，ALLOW→execute，DENY/REPLAN→respond），不重调 evaluate，本轮最多 1 次。

## 2. 缺口（需 W4c 实现，或提待决）

1. **`replay()` + EXECUTE_REPLAY 模式**（Q1）：`execution/replay.py` 仍抛 NotImplementedError。
2. **G5/G8 处理器**：`api/execution.py` 全 stub。
3. **G5 响应模型**：`schemas/` 下无 `{plan_id,status,decision,execution?}` 的 wire 模型，需新建（Q2）。
4. **UNKNOWN 恢复触发方式**：无 scheduler/worker（W5 才有）；PRD §11.1 提到「启动恢复扫描」，但 gateway 目前无该扫描入口（Q3）。
5. **G8 request_id 重放语义与存储**（Q4）。

## 3. 迁移 / 字段需求

**无需新迁移**。`execution_jobs` 已有全部列；`authorization_claims`、`plans`、`evaluations`、`approvals`、`request_dedup` 均已存在。
若实现中确认需要新列（例如把 G8 request_id 直接记到 execution_jobs 而不是复用 request_dedup），停下来写进待决，不自行设计迁移。

## 4. PRD 未闭合点

- PRD §9.5 G5 的 `execution?` 字段未定义确切形状：是完整 `ExecutionResult`，还是 `{status: UNKNOWN}` 简形，
  还是 job 状态 + result 的合成物（Q2）。
- PRD §9.5 G8「此前返回 202/409 的请求通过 G5 取得最新状态」与「已有 request_id 首先遵守持久响应重放」的
  精确重放矩阵未落地为代码（Q4）。
- PRD §7.10.7 的「恢复工作器」在 L2 内部没有既存调度实体（Q3）。

## 5. 协调问题

### Q1：`replay()` / EXECUTE_REPLAY 是否归 W4c？per-stage 差异有哪些？

事实：`execution/replay.py` 是 stub，W3b 未实现。协调者 scope 描述已写「新快照复演（replay 用新 EvalContext）」。

建议：W4c 实现 `replay(ctx)` = 构造新 `EvalContext`（复用原 plan / authorization / conn，新 L3 snapshot）后
`pipeline.evaluate(ctx, mode=EvalMode.EXECUTE_REPLAY)`，并在 W4c 内定义 EXECUTE_REPLAY 的 stage 差异（至少：
decision 阶段不生成 shadow_b1——现有 `decision.py:92` 已天然满足；复演结果必须与已确认 `view_hash` 逐字节比较）。
是否需要改动 stage 代码，以红测为准；若需要改动 `pipeline/*`，先提出最小差异清单再动。

### Q2：G5 响应形状

建议：`{plan_id, status, decision, execution?}`，其中 `decision` 取最新 evaluation 的 Decision（或仅 decision 字符串 + risk），
`execution?` 在 job 终态时取 `execution_jobs.result`（完整 ExecutionResult），在 UNKNOWN 时给 `{status:"UNKNOWN"}`。
`decision` 与 `execution` 的精确字段请在任务书裁定，我建议：`decision` = `{decision, risk, approval_id, evaluation_id, revision}` 子集，
`execution` = `ExecutionResult | {status:"UNKNOWN"} | null`。

### Q3：UNKNOWN 恢复如何触发

事实：无 scheduler/worker，无启动扫描实现。PRD §11.1 有「启动恢复扫描」表述。

建议（待裁定）：W4c 只实现
(a) G5 读到 UNKNOWN job 时的**内联对账**（query → 若 COMMITTED 归档 EXECUTED / 若未 COMMITTED reconcile → 归档 FAILED，然后返回结果），
(b) G8 对新 request_id 命中 UNKNOWN job 时的**同一内联对账**。
启动扫描 / 周期工作器留给 W5（不在 W4c 范围）；如需 W4c 内做启动扫描，请明确授权。

### Q4：G8 request_id 重放语义与存储

事实：`idempotency.py` 说 G8 不要用 `request_idempotent`；`tables.py` 注释又把 G8 列入 request_dedup 端点。

建议（待裁定，先按此写任务书红测）：
- 同一 `(customer_id, plan_id)` 的 G8 request_id 若此前已返回**终态 ExecutionResult** → 重放原响应（replayed 语义）。
- 此前返回 202/409（非终态）→ **不**重放 202/409，落到「当前状态路径」：终态返回原 result 且 `replayed=true`，
  live 返回 409 `EXECUTION_IN_PROGRESS`，UNKNOWN 走内联对账。
- 存储：复用 `request_dedup`（route=`POST /plans/{plan_id}/execute`）记录**终态响应**；非终态不写 request_dedup。
  但不在 `request_idempotent` 的 produce 里做任何 L3 HTTP。

### Q5：真实 L3 的确定性崩溃 / ack 丢失如何构造

事实：`FakeL3` 有 `commit_then_drop`/`drop`/`timeout`，但那是 fake。真实 L3 的「已提交但回包丢失」需要确定性手段。

建议（待裁定，二选一）：
- A) 测试内用 `httpx.MockTransport` 包一层，在转发到真实 L3 后丢弃响应（模拟 write timeout）——能确定性触发 L3OutcomeUnknown；
- B) 复用 S1/P1 的「实际进程重启」模式（在 prepare/execute 之间 kill uvicorn 线程并重启）。

### Q6：`FENCE_AT` 语义与 L3 对 `deadline_wall=1970-01-01` 的接受

`acknowledge_fence` 会把 `deadline_wall` 写成 `FENCE_AT=1970-01-01T00:00:00Z`。L3 `prepare` 的 attempt+1 校验
要求 `deadline_wall > now`。fence prepare 是同 attempt 且 `deadline_wall=min(old, FENCE_AT)`，但 L3 侧 `prepare` 对
`deadline_wall <= now` 的处理是拒绝还是允许「同 attempt 缩短」？需在真实 L3 上验证 `prepare` 对过期 deadline 的
`min` 规则不会把 fence prepare 挡掉（这是 ADR-008 能否走通的真实前提，必须用真实 L3 红测覆盖）。

## 6. 测试策略（事实/建议）

- 单元/状态机逻辑：tmpfs PG + `gateway_db` fixture（`tests/gateway/db/conftest.py`）。
- 真实 L3：复用 `tests/gateway/w3a/db/test_real_preflight.py` 的 `_serve_l3` 模式（`install_core` + `install_obligations`）。
- FakeL3（`l3/fake.py`）：仅用于「非终态逻辑」与 L3 异常分类的单元断言，**不作为**「只记一次 / 迟到竞争 / 崩溃恢复」的证据。
- 每次真实 L3 场景必须断言 DB 终态（`execution_jobs.result`、`plans.status`、`authorization_claims.status`、L3 `execution_intents`/账本），
  不能只看 HTTP 响应码。
