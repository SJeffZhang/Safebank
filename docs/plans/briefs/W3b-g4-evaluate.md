# W3b G4 evaluate：真实 `/plans/evaluate` 端点与完整持久化

2026-10-25；起草（DeepSeek V4 Pro 只读设计）；`codex/w3b-g4-evaluate`，基线 main `0472689`（C1d 已合）。

## 目标

把 `api/plans.py` 中的 G4 stub 替换为真实实现：

1. **A 路径**（Agent + assistant JWT）：接收 `PlanDraft`，跑完整 9 阶段流水线 → 持久化 plans / evaluations / audit → 必要时调 `create_for_evaluation` 建审批 → 返回 `Decision`
2. **F 路径**（App form）：TODO 骨架（`AppFormPlanRequest` → 从 `form_actions` 组装 PlanDraft → 同上）
3. **补齐依赖**：`evaluation/repo.py` 的 `append_evaluation` 从 `NotImplementedError` 改为真实 INSERT
4. **定义并遵守全局锁序**，解决 W4b2b 评审 P2-4 ABBA 死锁

## 架构

```
POST /plans/evaluate  (A + assistant JWT)
  │
  ├─ request_id idempotency 检查
  ├─ 解析 PlanDraft，分配 plan_id / evaluation_id
  ├─ 组装 EvalContext（identity, authorization, plan_draft, conn, l3, wall_now）
  │
  ├─ await pipeline.evaluate(ctx)   ← ①–⑨ 全部阶段
  │     ├─ ① contract, ② entity, ③ provenance, ④ intent  (W2)
  │     ├─ ⑤ snapshot → ctx._lazy_state  (一次 HTTP 到 L3)
  │     ├─ ⑥ preflight   (W3a)
  │     ├─ ⑦ workflow    (W3a — 含 cooldown 写入)
  │     └─ ⑧ decision    (W3a — 聚合、hint、shadow B1)
  │
  ├─ ⑨ audit EVALUATION_COMPLETED
  │
  ├─ [短事务：持久化]
  │     ├─ INSERT plans (status=EVALUATED / DENIED / …)
  │     ├─ INSERT evaluations (revision=N+1, decision JSONB, view_hash)
  │     ├─ 必要时 create_for_evaluation → INSERT approvals (status=PENDING)
  │     ├─ state.transition utterances → CONSUMED（当 plan 不为 REPLAN 时）
  │     └─ INSERT audit_events
  │
  └─ 返回 Decision
```

## 全局锁序（解决 W4b2b P2-4）

旧代码两处锁序存在 ABBA 风险：

| 调用路径 | 当前锁序 | 问题 |
|---|---|---|
| W4b1 `create_for_evaluation` → `_supersede_session` | session → plan → eval → **jobs → approval** | jobs 在 approval 前 |
| G6/G7 `confirm_approval` | **approval → session** → plan → eval | approval 在 session 前 |

**两事务交错**：TX1（create）持有 session，经 jobs 等 approval；TX2（G6）持有 approval，等 session → ABBA。

### 统一锁序

```
session → plan → evaluation → jobs → approval
```

**实施**：
1. **G6/G7 改**：`_validate_confirmable` 先锁 session（`.with_for_update()`），再锁 approval。当前代码已在 `_validate_confirmable` 中先锁 session（lines 134-146），但 `_lock_approval` 在调用方先执行（lines 282/387）。需要调整：先 `_validate_confirmable`（含 session 锁），再 `_lock_approval`。
   - 实际：G6/G7 的 `produce()` 内先调 `_lock_approval` 再调 `_validate_confirmable`。`_validate_confirmable` 内先锁 session 再锁 plan 再锁 eval。如果改为先调 `_validate_confirmable`（先锁 session）再调 `_lock_approval`（后锁 approval），则锁序为 session → plan → eval → approval，匹配统一序。
2. **W3b G4 遵守同一锁序**：`append_evaluation` 事务内如果需要锁 approvals（例如 idempotency 重检），先确保 session → plan → eval 已锁，再碰 approvals 表。
3. **W4b1 `_supersede_session`**：当前锁序 jobs → approval，嵌套在已持 session → plan → eval 的 `create_for_evaluation` 中。这实际是 session → plan → eval → jobs → approval，**已经匹配统一序**。唯一的冲突是 G6/G7 顺序相反。

### 死锁回归测试

新增 `tests/gateway/w3b/test_lock_order.py`：
- **T1**：G6 与 W4b1 create 并发（不同 plan 同 session），PG `deadlock_timeout=500ms` 下不触发 deadlock
- **T2**：G7 与 W4b1 create 并发，同上
- **T3**：G4 evaluate（REQUIRE_APPROVAL → 触发 create_for_evaluation）与 G6 并发，不触发 deadlock

## 接口规格

### G4 请求（A 路径）

```json
POST /plans/evaluate
Authorization: Bearer <assistant JWT>
{
  "request_id": "req_<ulid>",
  "plan_draft": { ... }       // PlanDraft per PRD §9.4
}
```

### G4 响应

```json
{
  "plan_id": "pln_<ulid>",
  "evaluation_id": "evl_<ulid>",
  "revision": 1,
  "decision": "ALLOW" | "REQUIRE_APPROVAL" | "REPLAN" | "DENY",
  "risk": "GREEN" | "YELLOW" | "RED",
  "approval_id": "apv_<ulid>" | null,
  "findings": [...],
  "replan_hints": [...],
  "preflight": { ... },
  "versions": {...},
  "clock_version": 0,
  "policy_version": "sha256:...",
  "shadow_b1": null
}
```

字段语义与 PRD §9.5 G4 一致；`approval_id` 仅当 `decision=REQUIRE_APPROVAL` 时非 null。

### 错误码

| 条件 | code | HTTP |
|---|---|---|
| 缺/无效 JWT | UNAUTHENTICATED | 401 |
| customer_id 缺失 | UNAUTHENTICATED | 401 |
| request_id 重复且 body 不同 | IDEMPOTENCY_CONFLICT | 409 |
| request_id 正在处理中 | EXECUTION_IN_PROGRESS | 409 |
| 同一 (customer_id, origin, request_id) 已存在 | STATE_CHANGED | 409 |
| 同一 plan（相同 steps_hash）正在另一 session 处理 | DUPLICATE_PLAN | 409 |
| pipeline 阶段抛 GatewayError | 按具体 code | 4xx/5xx |
| L3 不可达 | DEPENDENCY_UNAVAILABLE | 503 |

### F 路径（App form）

骨架：解析 `AppFormPlanRequest` → 查 `form_actions` 表 → 从 `normalized_fields` 组装 `PlanDraft` → 走同一 pipeline。
**本轮不实现**，仅留 TODO 注解和 501 响应。

## EvalContext 组装（W3b 负责的字段）

Pipeline 已需要的字段（W0/W2/W3a 已使用）：
- `plan_draft: PlanDraft` — 来自请求体
- `plan_id: str` — `pln_<ulid>`，G4 生成
- `origin: str` — 来自 caller（`agent` | `app_form`）
- `customer_id: str` — 来自 caller
- `session_id: str` — 来自 caller 或 request body
- `identity: Caller` — 来自 JWT 鉴权中间件
- `authorization: AuthorizationInputs` — **W3b 组装**：从 utterances 表加载 ACTIVE 原话，组装 scope / normalized_scope
- `conn: AsyncConnection` — 复用写入事务的连接（workflow 阶段需要）
- `l3: L3Client` — request.app.state
- `wall_now: Callable` — request.app.state
- `mode: EvalMode.EVALUATE`
- `profile: str` — B2（生产）/ 其他（eval 模式）

## authorization 组装（W3b 新增）

`EvalContext.authorization` 类型为 `AuthorizationInputs`（`schemas/scope.py`）。G4 handler 需要在 pipeline 执行前从 DB 加载：

1. 查 `utterances` 表：`sid=$session_id, status=ACTIVE, expires_at_wall > now()`
2. 组装 `AuthorizationInputs`：
   - `utterance_ids: list[str]` — ACTIVE 且 SCOPED 的原话 ID
   - `utterances: dict[str, UtteranceView]` — id → 原话视图（含 scope、parse_status、expires_at_wall）
   - `statuses: dict[str, UtteranceStatus]` — id → 状态
3. `AuthorizationInputs` 放进 `ctx.authorization`

**注意**：此查询在读事务中执行（pipeline 前），不在写事务中。写事务开始后重新锁读以确保 `revoked_at_wall` 未被并发置位。

## 评估与持久化事务

### Phase 1：Pipeline 执行（读事务）

```python
async with db.connect() as read_conn:
    ctx.conn = read_conn
    ctx.authorization = await _load_authorization(read_conn, session_id)
    await pipeline.evaluate(ctx)
```

Pipeline 内各阶段通过 `ctx.conn` 做只读查询（workflow 历史、cooldowns）——这些都发生在读事务中。Workflow 阶段写入 cooldowns 是幂等的 `ON CONFLICT DO UPDATE`，在读事务中也能安全执行。

### Phase 2：持久化（写事务）

```python
async with db.begin() as write_conn:
    # 1. Lock session → plan（检查未被撤销、无重复 request_id）
    # 2. 确定 revision = MAX(revision) + 1
    # 3. INSERT plans (plan_id, status=…)
    # 4. INSERT evaluations (evaluation_id, plan_id, revision, decision, …)
    # 5. 如果 decision=REQUIRE_APPROVAL：
    #    a. build_view(ctx) → PlanView + view_hash
    #    b. create_for_evaluation(write_conn, plan, evaluation, view) → approval_id
    #    c. 更新 ctx.decision.approval_id
    # 6. 如果不为 REPLAN/DENY：consumed utterance 相关状态
    #    （CLAIMED scope 消费留给 G8；G4 只标记原话状态）
    # 7. INSERT audit_events（事务内，用 audit.append，确保哈希链完整）
    # 8. 更新 evaluations.view_json（如果 create_for_evaluation 已更新则跳过）
```

**关键约束**：
- `create_for_evaluation` 不 commit（它由调用方管理事务）
- 整个写事务失败时全部回滚（包括 dedup 行和 audit）
- G4 不使用 `customer_lock`（那是 G8 的职责）；G4 只做评价，不执行 L3

### 原话消费时机

| Decision | 原话处理 |
|---|---|
| ALLOW | 不消费（留给 G8 消费） |
| REQUIRE_APPROVAL | 不消费（留给 G8 消费） |
| REPLAN | 不消费（agent 重新提交 plan） |
| DENY | 不消费（与 ALLOW/REQUIRE_APPROVAL 相同策略） |

**当前裁定**：G4 不消费原话；原话消费统一在 G8 执行时通过 `authorization_claims` 的 CLAIMED→CONSUMED 进行。G4 仅在 evaluations 中记录使用了哪些 `utterance_ids`。

## evaluation_id 与 revision 分配

当前 `decision.py:98` 硬编码：
```python
evaluation_id=f"evl_{ctx.plan_id}",
revision=1,
```

W3b 需要在 pipeline **执行后**、持久化 **前** 分配真实 ID：

```python
# 在写事务中：
latest_rev = await conn.scalar(
    select(func.max(t.evaluations.c.revision)).where(t.evaluations.c.plan_id == plan_id)
)
revision = (latest_rev or 0) + 1
evaluation_id = new_id("evl")

# 更新 ctx.decision
ctx.decision.evaluation_id = evaluation_id
ctx.decision.revision = revision
```

## make test-w3b 命令

```bash
AGENT_DB_TESTS=1 .venv/bin/pytest tests/gateway/w3b/ -q
```

## 全局约束

- **不改 `pipeline/context.py` 中 `EvalContext` 的字段**（W0 冻结）
- **不改 `pipeline/__init__.py` 中 `STAGES` 元组或 `evaluate` 函数签名**（W0 冻结）
- **不改 `state.py` 迁移表或状态枚举**（W0 冻结）
- **不改 `tables.py` 表结构**（W0 冻结）
- **不改 W4b1 中 `approval/service.py`、`approval/planview.py` 的公开函数签名**（W4 冻结）
- **不新增数据库迁移**（W3b 使用的所有列已存在于 0002_wave1）
- **不在评估事务中调 L3 HTTP**（避免持锁等待）
- **不改 G6/G7 业务逻辑**，只调整其在 `produce()` 中的 `_lock_approval` 与 `_validate_confirmable` 调用顺序来解决死锁
- customer_lock 留给 G8；G4 不使用

## Task 清单

### Task 0：全局锁序修复（G6/G7 调整调用顺序）

- [ ] T0.1 改 `api/approvals.py` G6 `produce()`：先调 `_validate_confirmable`（内含 session 锁），再调 `_lock_approval`
- [ ] T0.2 改 G7 同上
- [ ] T0.3 死锁回归测试 `tests/gateway/w3b/test_lock_order.py`：
  - T0.3a G6 confirm 与 W4b1 create_for_evaluation 并发（不同 plan 同 session）
  - T0.3b G7 reject 与 W4b1 create 并发
  - T0.3c 两个测试用真实 PG、`deadlock_timeout=500ms`，断言不触发 deadlock
- [ ] T0.4 现有 W4b2b 全量测试回归（`make test-w4b2b`）

### Task 1：evaluation/repo.py 真实实现

- [ ] T1.1 `append_evaluation(conn, plan_id, evaluation_id, revision, decision, risk, findings, preflight, versions, policy_version, clock_version)` — INSERT 到 evaluations 表
- [ ] T1.2 `latest_revision(conn, plan_id) -> int` — SELECT MAX(revision)
- [ ] T1.3 幂等：同一 (plan_id, revision) 重复插入 → 409
- [ ] T1.4 `update_view_json(conn, evaluation_id, plan_id, view_hash, view_json)` — UPDATE evaluations SET view_hash, view_json
- [ ] T1.5 专项测试 `tests/gateway/w3b/db/test_evaluation_repo.py`（真实 PG，tmpfs）

### Task 2：G4 handler 主体

- [ ] T2.1 `api/plans.py` 替换 stub：解析 PlanDraft、分配 plan_id、组装 EvalContext
- [ ] T2.2 `_load_authorization(conn, session_id) -> AuthorizationInputs` — 从 utterances 表加载
- [ ] T2.3 Pipeline 执行（读事务）
- [ ] T2.4 持久化写事务（plan + evaluation + approval + audit）
- [ ] T2.5 request_id 幂等（复用现有 `request_idempotent`）
- [ ] T2.6 重复 (customer_id, origin, request_id) 检测 → 409 STATE_CHANGED
- [ ] T2.7 返回 Decision JSON

### Task 3：App form 骨架

- [ ] T3.1 `AppFormPlanRequest` 解析与验证
- [ ] T3.2 501 响应（`"F path lands in W3b follow-up"`）
- [ ] T3.3 单元测试：正确解析、缺失字段 422

### Task 4：真实 PG/HTTP 集成测试

- [ ] T4.1 ALLOW 路径：简单本人转账 → 不建 approval → plan.status=EVALUATED → evaluation 持久化
- [ ] T4.2 REQUIRE_APPROVAL 路径：对外转账超黄线 → 建 approval → plan.status=PENDING_APPROVAL → approval.status=PENDING → evaluation.view_json 含真实 approval_id
- [ ] T4.3 DENY 路径：RISKY_SEQUENCE（冷却期内） → plan.status=DENIED → 不建 approval
- [ ] T4.4 REPLAN 路径：超额导致 INSUFFICIENT_FUNDS → hints 非空 → plan.status=REPLAN
- [ ] T4.5 request_id 幂等：相同 request_id + body → 200 + 复用响应；相同 request_id + 不同 body → 409
- [ ] T4.6 并发：两个不同 request_id 对同一 session → 第二个等锁或 409
- [ ] T4.7 会话已撤销 → 409 AUTHORIZATION_CANCELLED
- [ ] T4.8 审计哈希链验证：G4 写后 `verify_chain(customer_id)` 通过
- [ ] T4.9 Shadow B1：B2 profile 下 `shadow_b1` 非 null（B2 vs B1 差异随具体场景）

### Task 5：仿真 L3 边界

- [ ] T5.1 G4 测试全部使用 FakeL3（真实 bank-backend），不做 mock L3
- [ ] T5.2 仅 Pipeline 所需的 5 个 read 工具需真实实现（get_accounts, get_cards, get_payees, get_transactions, get_products, get_instructions, get_subscriptions）；其他 read 工具可以 501
- [ ] T5.3 明确记录：G4 不调 L3 write（那是 G8 的职责）

### Task 6：最终门禁

- [ ] T6.1 `make lint`（ruff check + format）
- [ ] T6.2 `make test` 全量回归（所有已有组件）
- [ ] T6.3 `make test-gateway` gateway 测试全量
- [ ] T6.4 `make test-gateway-db` 真实 PG 基础测试
- [ ] T6.5 `AGENT_DB_TESTS=1 .venv/bin/pytest tests/gateway/w3b/ -q` 专项
- [ ] T6.6 `make test-w4b2b` W4b2b 回归（含锁序变更后的原有 57 项）
- [ ] T6.7 `make contracts-check` 契约冻结
- [ ] T6.8 actionlint

## 退出命令

```bash
make lint
make test
make test-gateway
make test-gateway-db
AGENT_DB_TESTS=1 .venv/bin/pytest tests/gateway/w3b/ -q
make test-w4b2b
make contracts-check
actionlint
```

## 不在范围

- G8 执行（留给 W4c）
- OTP 发送/校验（W4b2c）
- scheduler 回调（W5）
- Demo/演示接口
- 前端/BFF
- 真实 L1 agent 调 G4（留给集成阶段）
- F 路径真实实现（仅骨架）
- authorization_claims 消费（G8 职责）
- PIN 校验（G8 职责）
- 客户锁（G8 职责）
## 协调者裁定（2026-10-25，与上文冲突时以本节为准）

- **B1 冷静期写入（否决 Q1「保持在读事务」）**：workflow 冷静期（cooldowns）要和 evaluation 持久化、审计追加放进**同一个写事务**原子提交。只要这个写事务失败，就不能残留冷静期，避免用户被拦截却查不到对应的 evaluation 和审计。pipeline 读阶段只计算出「需要写入的冷静期」，交给持久化步骤写入。这一点必须有红测：在持久化 evaluation 时注入失败，事务回滚后 cooldowns 表里不能有残留。
- **B2**（Q2）：REPLAN 也持久化 evaluation（revision+1，decision=REPLAN），审计链完整。
- **B3**（Q3）：同意。G4 handler 在 pipeline 返回后用 `model_copy(update=...)` 回填 evaluation_id、revision、approval_id，不改冻结的 EvalContext 字段。
- **B4**（Q4）：DENY 时 plan.status=DENIED，只经 `state.transition` 迁移。
- **B5 全局锁序（Q5 方向同意，补充要求）**：全局统一为 **session → plan → evaluation → approval → otp_challenge**。G6、G7 以及 **W4b2c 的 G10** 都要改成：先无锁读出 approval 对应的 sid，锁定 session，再锁 approval，然后重新核对 approval 的 sid、plan_id 和状态有没有变化，变了就 fail-closed。W4b1 的 `_supersede_session` 与 W1 的撤销、注销路径保持 session-first，经核对不需要改。要有死锁回归红测：在当前锁序下，G6 与 supersede 并发会复现死锁或锁超时；改正后不再复现。
- **B6 基线**：W4b2c（PR #27）合并后，实现前必须先同步最新 main，因为 B5 要修改的 G10 在其中。
- **B7 范围**：不含 G8 执行（W4c）。L3 snapshot 优先用真实 L3（tmpfs PG + 真实 bank-backend 应用，参照 W3a/S3a 的做法）；用 FakeL3 的地方要在测试名和文档里标明 fake，不能当作端到端证据。需要新的迁移或字段时，停下来写进待决，不要自行设计迁移。
