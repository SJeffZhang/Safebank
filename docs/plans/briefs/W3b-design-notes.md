# W3b G4 evaluate 设计笔记

2026-10-25；只读代码分析（不实施）；baseline main `0472689`。

## 1. 代码事实（file:line）

### 1.1 G4 当前状态

- **`services/gateway/app/api/plans.py:9`**：`router.post("/plans/evaluate")(stub("G4 evaluate lands in W3"))` — 返回 501，需要完全替换
- **`services/gateway/app/evaluation/repo.py`**：`append_evaluation` 抛出 `NotImplementedError("append_evaluation is W3...")` — 没有任何 evaluation 持久化代码
- **没有 `tests/gateway/w3b/` 目录** — 需要新建

### 1.2 已有依赖（W3b 可复用）

| 组件 | 文件 | 状态 |
|---|---|---|
| 9 阶段 Pipeline | `pipeline/__init__.py:28-38` | `STAGES` 元组已定义，`evaluate()` 可用 |
| EvalContext | `pipeline/context.py:132-167` | 所有字段就绪；`authorization` 需 G4 填充 |
| Decision 生成 | `pipeline/decision.py:79-109` | `evaluation_id` 和 `revision` 硬编码占位，需 G4 替换 |
| Workflow 阶段 | `pipeline/workflow.py` | 完整（含 cooldown 写入、历史加载） |
| Preflight 阶段 | `pipeline/preflight.py` | 完整（依赖 S3a 真实 obligations） |
| Approval 创建 | `approval/service.py:307-495` | `create_for_evaluation(conn, plan, evaluation, view)` 可用 |
| PlanView 构建 | `approval/planview.py:371-424` | `build_view(ctx) -> (PlanView, view_hash)` 纯函数 |
| 状态迁移 | `state.py` | `transition()` 完整；plan/approval/utterance 迁移表已定义 |
| 审计 | `audit/chain.py` | `append_tx()` 、`append()` 均可用 |
| 幂等 | `idempotency.py` | `request_idempotent()` 可用 |
| 鉴权 | `auth/` | `caller_dependency` 已注入 `request.state.caller` |
| 客户锁 | `locks.py` | G4 不使用（留给 G8） |
| 表定义 | `tables.py` | plans / evaluations / approvals / audit_events / utterances 全部列就绪 |

### 1.3 决策阶段占位代码

**`pipeline/decision.py:97-108`**：
```python
ctx.decision = Decision(
    plan_id=ctx.plan_id,
    evaluation_id=f"evl_{ctx.plan_id}",  # ← 占位：重复 plan_id，不唯一
    revision=1,                            # ← 占位：始终为 1，不递增
    ...
)
```

W3b 必须在 pipeline 返回后、持久化前分配真实的 `evaluation_id`（`evl_<ulid>`）和 `revision`（MAX+1），然后更新 `ctx.decision`。

### 1.4 G6/G7 锁序（P2-4 根因）

**当前 G6/G7 `produce()` 调用顺序**（`api/approvals.py:282-283`）：
```python
approval = await _lock_approval(conn, approval_id, customer_id)  # 锁 approval
plan, evaluation, _binding = await _validate_confirmable(...)     # 锁 session → plan → eval
```

**`_lock_approval`**（`:80-95`）：`SELECT ... FROM approvals WHERE id=$1 FOR UPDATE` — 锁定 approval 行

**`_validate_confirmable`**（`:98-204`）：
- L134-146：`SELECT ... FROM sessions WHERE id=$1 FOR UPDATE` — 锁 session
- L149-161：`SELECT ... FROM plans WHERE id=$1 FOR UPDATE` — 锁 plan
- L164-179：`SELECT ... FROM evaluations WHERE id=$1 FOR UPDATE` — 锁 eval

**W4b1 `create_for_evaluation` 锁序**（`approval/service.py:307-495`）：
- L349-357：`SELECT ... FROM sessions FOR UPDATE` — 锁 session
- L359-373：`SELECT ... FROM plans FOR UPDATE` — 锁 plan
- L375-398：`SELECT ... FROM evaluations FOR UPDATE` — 锁 eval
- L402-408：`SELECT ... FROM execution_jobs FOR UPDATE` — 锁 job
- L418-440：`SELECT ... FROM approvals FOR UPDATE` — 锁 approval（idempotency 重检）
- L442：`_supersede_session` → 锁 jobs → approval（内嵌）

**ABBA 场景**：
- TX1（W4b1 create_for P1）：已持 session（L349）→ 在 `_supersede_session` 中想锁 approval（A2）
- TX2（G6 confirm A2）：已持 approval（`_lock_approval`）→ 在 `_validate_confirmable` 中想锁 session（L134）
- PG 死锁检测（~1s）回滚一方

### 1.5 需要的 authorization 加载

`EvalContext.authorization` 类型为 `AuthorizationInputs`（`schemas/scope.py`）。当前没有任何代码在 G4 handler 中填充它。

W2 provenance 阶段（`pipeline/provenance.py`）和 W3a decision 阶段（`pipeline/decision.py:220-234`）已经读取 `ctx.authorization`：
- `_active_scopes` 遍历 `auth.utterances`，筛选 ACTIVE + SCOPED + 未过期
- provenance 使用 `auth.utterances` 做污点追踪

**W3b 必须在 pipeline 执行前加载**：
```sql
SELECT * FROM utterances
WHERE sid = $session_id
  AND status = 'ACTIVE'
  AND expires_at_wall > now()
```

### 1.6 utterance_ids 与 plan 的关系

`plans` 表（`tables.py:156-174`）有：
- `utterance_ids: JSONB` — 该 plan 引用的原话 ID 列表
- `scope_ids: JSONB` — 该 plan 引用的 scope ID 列表

G3（W1 identity）创建 plan 时填充这些字段；G4 evaluate 重新评估时应该验证而非修改。

### 1.7 现有测试覆盖

**W4b2b test_race.py**（`tests/gateway/w4b2b/db/test_race.py:60-148`）：
- 直接 import `services.gateway.app.api.approvals` 调 `_lock_approval` 和 `_validate_confirmable`
- 用两连接 + `lock_timeout=500ms` 验证并发隔离
- **W3b 锁序测试应参考此模式**

### 1.8 FakeL3 当前状态

`services/gateway/app/l3/client.py` — L3Client 通过 `BANK_BACKEND_URL` 调真实 bank-backend HTTP。
W3b 测试应使用真实 bank-backend（`make up` 启动的容器），不做 mock。这要求：
- `make up` 后的六服务 healthy
- seed 数据包含 P1 的 `cus_a/cus_b` 账户和 S3a 的 obligations
- Pipeline 所需的 read 工具已实现（accounts, cards, payees, transactions, products, instructions, subscriptions）

## 2. 待裁定问题

### Q1：G4 写事务中是否应包括 workflow cooldown 写入

**现状**：`pipeline/workflow.py:299` 在 `_write_cooldown` 中直接写入 `cooldowns` 表（`ON CONFLICT DO UPDATE`）。当前在 pipeline 的 `ctx.conn`（读事务）中执行。

**问题**：如果 cooldown 在 pipeline 阶段写入，但后续写事务失败回滚，cooldown 会成为孤儿写入（已在读事务中提交）。

**选项**：
- A) 把 cooldown 写入推迟到写事务中重放（复杂：需重跑 workflow 逻辑）
- B) 保持现状：cooldown 是幂等的、非关键（最坏情况是少一个冷却期保护），且 pipeline 读事务中其他 `ON CONFLICT DO UPDATE` 写入也是同样模式

**建议**：B。cooldown 和 workflow_events 都是幂等写入，在 pipeline 阶段执行是故意的设计（让 pipeline 在一致的快照上评估）。即使后续写事务失败，残留 cooldown 仅影响下一次评估，属于安全侧（偏保守）。

### Q2：REPLAN 时是否保存 evaluation

**现状**：PRD §7.10 说「REPLAN 不消耗原话，agent 重新提交 plan」。

**问题**：REPLAN 的 evaluation 是否存入 evaluations 表？

**建议**：是。存为 `revision=N+1`，decision=REPLAN，findings 含 REPLAN 原因。这样：
- 审计链完整（每次 evaluate 都有记录）
- `equivalent_to` 可以跨 revision 去重
- revision 递增逻辑一致

### Q3：DENY 时 plan.status 是什么

**现状**：`state.py:55` — `PlanStatus.DENIED: frozenset({PlanStatus.SUPERSEDED})`

**建议**：DENY 时 plan.status = DENIED。与 EVALUATED / PENDING_APPROVAL / REPLAN 平行。

### Q4：evaluation 的 view_json 什么时候写

**现状**：W4b1 `create_for_evaluation` 在创建 approval 后更新 `evaluations.view_json`（含真实 approval_id）。但 ALLOW/REPLAN/DENY 不走 `create_for_evaluation`。

**建议**：
- REQUIRE_APPROVAL：由 `create_for_evaluation` 更新 view_json（已有）
- ALLOW/REPLAN/DENY：G4 handler 在写事务末尾更新 view_json（含空 approval_id 和最终 status）

### Q5：shadow_b1 是否需要持久化

**现状**：`pipeline/decision.py:90-94` 生成 `shadow_b1` 放入 `ctx.decision`。

**建议**：shadow_b1 放入 evaluations.decision JSONB 的 `shadow_b1` 字段（Decision 模型已有此字段）。仅 B2 profile + EVALUATE mode + state 非 null 时有值。

## 3. PRD 未闭合点

### 3.1 评估频控 / 预算

PRD §7 未定义每 session 评估次数上限。当前 G4 不做频控。**建议**：留给 W5 或集成阶段。

### 3.2 AppFormPlanRequest → PlanDraft 转换

PRD §9.5 定义了 `AppFormPlanRequest` 结构，但未详述如何从 `form_actions` 组装 `PlanDraft`。**本轮不实现**，留 501。

### 3.3 评估结果缓存 / equivalent_to

`evaluations` 表有 `equivalent_to` 字段（指向等价的旧 evaluation_id）。PRD 未定义等价判定算法。**本轮不实现**。

## 4. 迁移需求

**无需新迁移**。evaluations 表（0002_wave1）已包含所有需要的列：id, plan_id, revision, decision, risk, findings, preflight, versions, policy_version, view_hash, equivalent_to, view_json, clock_version, created_at_wall。

## 5. 共享协议影响

- `pipeline/decision.py` 的 `DecisionStage.run()` 中 `evaluation_id` 和 `revision` 的赋值方式需要支持外部覆盖。**方案**：G4 handler 在 pipeline 返回后直接修改 `ctx.decision.evaluation_id` 和 `ctx.decision.revision`（Decision 是可变 dataclass？不，它是 Pydantic Contract，不可变。需要用 `model_copy(update={...})`）。
- 或者：pipeline 不负责 decision 构造的最后一步；G4 handler 在 pipeline 返回后自己 build Decision。**但** 当前 decision stage 做了大量工作（聚合、hint、shadow_b1），不应该移到 handler。

**最佳方案**：在 decision stage 中支持 `evaluation_id` 和 `revision` 的注入点。要么：
  - A) 在 EvalContext 中增加 `evaluation_id: str | None` 和 `revision: int = 0` 字段（破坏冻结）
  - B) G4 handler 在 pipeline 返回后重建 Decision：
    ```python
    ctx.decision = ctx.decision.model_copy(update={
        "evaluation_id": evaluation_id,
        "revision": revision,
        "approval_id": approval_id,
    })
    ```
  - C) decision stage 从 EvalContext 读 `plan_id` 生成 evaluation_id，revision 为 1；G4 handler 在持久化前替换

**建议 B**：不破坏 EvalContext 冻结，handler 拿到 pipeline 产出后用 `model_copy` 替换三个字段。

## 6. 测试策略

### FakeL3 vs 真实 L3

- **单元测试**（`tests/gateway/w3b/unit/`）：FakeL3 + tmpfs PG，验证 handler 逻辑
- **集成测试**（`tests/gateway/w3b/db/`）：真实 bank-backend（`make up` 启动），真实 PG（tmpfs），验证端到端 G4

### 测试数据

需要至少两个测试场景：
1. **cus_a** 向本人账户转账 100 元 → ALLOW（绿级）
2. **cus_a** 向外部收款人转账 30,000 元 → REQUIRE_APPROVAL（黄级，首次转账 + 金额超黄线）
3. **cus_a** 的 RISKY_SEQUENCE 冷却期场景 → DENY

### 并发测试

- 两个 request_id 同时 evaluate 同一 session → 第二个等锁或 409
- G4 evaluate（触发 create_for_evaluation）与 G6 confirm 并发 → 不触发 deadlock（T0.3c）