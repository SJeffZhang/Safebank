# W3b G4 evaluate 独立评审

**评审者**：DeepSeek V4 Pro（只读评审 worker）
**范围**：`git diff 6986a27..df76f3e`（17 文件，+3668/−62）
**结论**：**REQUEST_CHANGES**（P0 无，P1 1 条，P2 3 条）

---

## P0（无）

所有核心数据流、事务边界、安全门禁正确。

---

## P1

### P1-1 `plans.py:331` 硬编码 `policy_version_str = "sha256:test"`

**证据**：`services/gateway/app/api/plans.py:331`
```python
policy_version_str: str = "sha256:test"  # TODO: real policy version
```

决策阶段 `pipeline/decision.py:29-33` 已通过 `_policy_version()` 对 `tools.yaml`、`thresholds.yaml`、`workflow.yaml` 三个政策文件做 SHA256 并缓存。G4 持久化时丢弃真实摘要，代以字面量 `"sha256:test"`。

**失败场景**：所有经由 G4 的 evaluation 行都携带此假版本。政策文件日后变更时，无法通过 `policy_version` 字段关联到当时的政策内容，审计链中 policy-version 维度完全作废。

**修复建议**：使用 `decision.policy_version`（`ctx.decision.policy_version`，已在 `DecisionStage.run()` 中填充）替代硬编码字面量。

---

## P2

### P2-1 `approvals.py:134` `_validate_confirmable` 冗余 session 锁

**证据**：`services/gateway/app/api/approvals.py:134-146` — `_validate_confirmable` 内部仍执行 `SELECT … FROM sessions … FOR UPDATE`。G6/G7/G10 已在调用前通过 `_lock_session()` 锁定了同一 session 行。

**影响**：同事务内 PG 对已锁定行执行第二次 FOR UPDATE 是 no-op，无功能影响。但读代码时需跳转到两处才能确认锁序，增加维护风险（未来修改者可能误以为可删除 `_lock_session` 而破坏锁序）。

**修复建议**：在 `_validate_confirmable` 的 session 锁处添加注释说明"上层调用方已锁定，此处为防御性重锁"；或在未来清理轮次中将 session 锁提取为上层统一职责。

### P2-2 `test_g4_evaluate.py:245-246` 测试名/文档与实际行为矛盾

**证据**：
- 函数名：`test_evaluate_transfer_allowed`
- 文档字符串第 1 行：`"Transfer with complete arg_sources + read records → ALLOW."`
- 实际断言 `test_g4_evaluate.py:317`：`assert data["decision"] == "REQUIRE_APPROVAL"`

800 元对外转账命中 FIRST_TRANSFER + AMOUNT_ABOVE_YELLOW，决策为 REQUIRE_APPROVAL，并非 ALLOW。函数名和文档字符串写反了。

**修复建议**：改为 `test_evaluate_transfer_requires_approval` 并修正文档字符串。

### P2-3 `test_lock_order_endpoint.py` 使用与 app 不同的 HMAC 密钥

**证据**：
- `test_lock_order_endpoint.py:38` 导入 `from tests.gateway.w4b2b.db.conftest import HMAC_KEY`（值为 `b"w4b2b-approval-hmac-key-at-least-32-bytes-long"`）
- `test_lock_order_endpoint.py:38` 的 `build_app` 来自 `tests.gateway.w3b.db.conftest`（W3b conftest `:351` 用 `b"w3b-approval-hmac-key-at-least-32-bytes-long!"` 配置 app）

`_seed_two_plans` 用 W4b2b 的 HMAC 计算 view_hash，但端点用 W3b 的 HMAC 校验 → 解密后返回 409 VIEW_HASH_MISMATCH。

**影响**：死锁测试本身仍有效（竞争在 session 行锁，而非 view_hash 校验），但端点 handler 在获取锁之后的业务逻辑（view_hash 校验、状态迁移、审计追加）**实际未被此测试覆盖**——请求在 view_hash 校验处即失败返回。

**修复建议**：统一使用 W3b conftest 的 HMAC_KEY，或让 build_app 接受显式 HMAC 参数以对齐种子数据。

---

## 逐项核对

| 检查项 | 结论 |
|---|---|
| **端到端数据流** | ✅ W1 原话→W2 阶段→W3a 阶段→持久化→W4b1 审批。ALLOW/REQUIRE_APPROVAL/DENY/REPLAN 四个分支 DB 状态正确。 |
| **B2 REPLAN 持久化** | ✅ REPLAN 时 evaluations 行写入（decision=REPLAN），plan.status=REPLAN。 |
| **B3 evaluation_id 回填** | ✅ `model_copy(update={"evaluation_id": evaluation_id})` 在 `build_view` 前，不破坏 EvalContext 冻结。 |
| **B4 DENY→DENIED** | ✅ `_DECISION_STATUS[DecisionValue.DENY] = PlanStatus.DENIED`，不建 approval。 |
| **B1 冷静期原子性** | ✅ 读阶段只收集到 `pending_cooldowns`，写事务内 `_persist_cooldowns` 同事务执行。红测 `test_cooldown_rollback_on_transaction_failure` 证实回滚无残留。 |
| **B5 全局锁序** | ✅ G6/G7/G10 全部改为先无锁读 approval→锁 session→锁 approval→重检。`_lock_session`、`_lock_approval` 在同一路由内线性顺序明确。AST 守卫 `test_lock_order_guard.py` 防止回归。 |
| **事务边界** | ✅ Pipeline 读事务（ctx.conn=read_conn），持久化写事务（db_engine.begin()）。L3 snapshot 在 pipeline 读阶段完成，写事务内无 HTTP。 |
| **request_idempotent** | ✅ `request_idempotent` 管理 `_persist` 的事务范围，produce 失败时删除 dedup 行后重抛，外层事务回滚。 |
| **W1 test_s1_runtime 适配** | ✅ 未放宽断言。`/plans/evaluate` 从 503 stub 改为真实 handler 后，登出前验证 auth 通过（非 401/403），登出后断言 401 UNAUTHENTICATED。 |
| **W3a test_workflow_db 修改** | ✅ 未放宽断言。从 DB 查询 cooldown 改为检查 `ctx.workflow.pending_cooldowns`（内存收集），语义等价且与 B1 设计一致。 |
| **FakeL3 标注** | ✅ `tests/gateway/w3b/db/conftest.py:69-73` 明确定义 `FakeL3Client`，注释说明真实 L3 覆盖留给端到端套件。 |
| **安全** | ✅ caller.kind 检查（F→503、非A→401）；customer_id/sid 来自 JWT；utts ⊆ 请求体；session 所有权校验。 |
| **原话消费** | ✅ G4 不消费原话——按裁定，消费留给 G8。 |
| **冻结** | ✅ contracts 未改；context.py 仅 WorkflowResult 新增 `pending_cooldowns` 字段；迁移未改。 |
| **G8 执行** | ✅ 不在范围。 |
| **ABBA 死锁覆盖** | ✅ G6/G7/G10 全部 session-first。`create_for_evaluation` 为 session→plan→eval→job→approval（匹配统一序）。W1 撤销路径只锁 sessions（不碰 approvals），无 ABBA。 |

## 未确认项

1. `policy_version_str` 硬编码是否也出现在 `append_evaluation` 的测试固件中？`test_evaluation_repo.py` 的测试直接传 `policy_version` 参数，不依赖 plans.py 的常量，无影响。

2. `test_lock_order_endpoint.py` 中 HMAC 不匹配是否导致 `test_g10_otp_send_serializes_no_deadlock` 的 endpoint handler 在锁获取后仅执行到 `_read_approval_unlocked` 的 view_hash 预检查即失败？从代码路径推断：是的，但锁顺序测试目的仍达成（竞争发生在更早的 session 锁上）。