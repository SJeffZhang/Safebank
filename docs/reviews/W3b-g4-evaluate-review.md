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

---

## 修复轮 6 定点复审（2026-10-25，df76f3e..b2f1df0）

**范围**：`git diff df76f3e b2f1df0`（10 文件，+283/−32）
**结论**：**APPROVE**（P0 无，P1 无，P2 3 条——均不阻塞合并）

---

### 上轮 4 条逐项核对

#### P1-1 policy_version 硬编码 — ✅ 已修复，且格式修正是必须的

- `plans.py:331` 现为 `policy_version_str: str = decision.policy_version`，丢弃了 `"sha256:test"` 字面量。
- `decision.py:60-64` `_policy_version()` 现返回 `digest.hexdigest()`（64 位 hex，无 `"sha256:"` 前缀）。

**关键证据**：`tables.py:187` `Column("policy_version", String(64), nullable=False)`。旧格式 `"sha256:" + 64 hex = 71 字符` **根本不可能**写入 `String(64)` 列——旧实现（含真实摘要的 `"sha256:"` 前缀）会在 INSERT 时 DataError 报错，或（用占位符时）只能靠 71 字符的字面量从未真正落库。因此去掉前缀不是"格式漂移"，而是**与列宽对齐的必要修正**。

#### P2-1 冗余 session 锁 — ✅ 已修复，安全性成立

- `_validate_confirmable` 内的 session 读取已不再 `FOR UPDATE`（`approvals.py:146` 起）。
- 全局仅 2 个调用方：G6 `confirm_approval`（`approvals.py:336`）与 G7 `reject_approval`（`approvals.py:563`），两处在调用前都已 `_lock_session(...)`（G6 在 `:327`、G7 同序）。移除内部重锁后，session 锁仍由上层唯一持有，无锁序回归。

#### P2-2 测试名实不符 — ✅ 已修复

- 已改名 `test_evaluate_transfer_requires_approval`，文档字符串同步为 REQUIRE_APPROVAL 语义（`tests/gateway/w3b/db/test_g4_evaluate.py`）。

#### P2-3 端点测试 HMAC 密钥不一致 — ✅ 已修复，且补强了业务断言

- `tests/gateway/w3b/db/test_lock_order_endpoint.py` 改从 W3b conftest 导入 `HMAC_KEY`（与 `build_app` 同源），并新增 `verify_fn`（confirmed/rejected/otp_sent 结果校验）与 `token_kind="F_APPROVE"`（`sign_jwt(aud="gateway-app", scope=["approve"])`）。
- 这不止消除 409 VIEW_HASH_MISMATCH 假失败，还让锁获取之后的 view_hash 校验、状态迁移、审计追加真正被断言覆盖——上轮 P2-3 指出的"业务逻辑未被覆盖"问题一并关闭。

---

### 实现者测试固件改动核对（最小必要、未放宽）

| 改动 | 判定 |
|---|---|
| `tests/gateway/w4b2b/db/conftest.py:464-465` `_compute_binding(customer_id=customer)` | ✅ 必要。生产 `ApprovalBinding` 含 `customer_id`（`approval/service.py:96-106`，`_binding_payload` 于 `:140` 纳入），固件此前漏算该字段会使种子 binding_mac 与 `verify_binding` 永远不匹配。属对齐生产，非放宽。 |
| `tests/gateway/w4b2b/db/test_race.py:99` 增 `_lock_session` | ✅ 必要。测试直接调用生产 `_lock_session`/`_lock_approval`/`_validate_confirmable`，补 session 锁以匹配新的 session→approval 锁序。属对齐，非放宽。 |
| `tests/gateway/w3a/test_decision.py:244` 断言 64 位 hex | ✅ 收紧而非放宽。`assert len(pv) == 64 and all(c in "0123456789abcdef" for c in pv)` 精确锁定新格式，可防止未来回归 71 字符前缀。 |

---

### 协调者实测「改错 hmac_key 后 3 条中 1 条仍通过」——判定

仍通过的端点确认是 **G10 `otp_send`**（`approvals.py:708-837`）。核对路径：

- G6/G7 走 `_validate_confirmable`，在 `approvals.py:246` 执行 `verify_binding(key, binding, approval["binding_mac"])` → 密钥错即 409，故 2 条失败。
- G10 只做：无锁读 approval → 校验 PENDING/过期/view_hash → `_lock_session` → `_lock_approval` → 重检 → `otp_challenges` FOR UPDATE 频控 → 生成 OTP。**全程不校验 `binding_mac`**。

**评估：P2（纵深防御一致性缺口），非安全漏洞，可接受、建议后续补**。理由：

1. G10 的唯一副作用是写入一条受频控（最多 3 发、间隔 30s、错误跨重发累计）的 `otp_challenges` 行——不产生资金移动，不授予任何授权。
2. 该 OTP 码被 AEAD 加密，AAD 绑定 approval_id/plan_id/view_hash，唯一消费者是 G6 `confirm`；而 G6 在**任何 OTP 校验之前**（`_validate_confirmable` 于 `:336`，OTP 校验于 `:343+`）先校验 `binding_mac`。因此即使 approval 绑定字段被篡改，G10 发出的 OTP 也会在 G6 的 `:246` 处被拒，**无法铸造出可消费的 OTP**。
3. 若要在 G10 补 binding MAC 校验，需先锁 plan 与 evaluation（binding 含 plan_hash/evaluation_id），即把锁序扩为 session→plan→evaluation→approval——超出 OTP 发送路径的必要范围，属合理延后。

**建议**：在 G10 或其代码注释中记录「binding MAC 由 G6 消费时校验，G10 仅做归属+view_hash 门禁」，避免未来维护者误判为遗漏；或待 W3b 统一锁序时把 G10 纳入完整 binding 校验。

---

### 新问题（均 P2，不阻塞）

#### P2-N1（本轮引入的文档漂移）PRD §9.5 G4 响应示例仍写 `"sha256:…"`

**证据**：`PRD.md:1435` 响应示例 `"policy_version": "sha256:…"`。本轮把格式改为 64 位 hex 后，该示例与 `String(64)` 列及实际 wire 值不一致。

**建议**：协调者在合入时同步 PRD 为 64 位 hex（示例改 `"<64位hex>"` 并注明列宽 64 的约束来源）。

#### P2-N2（预存在，非本轮引入，因本轮定稿 policy_version 值而显性化）policy_version 未包含 profile

**证据**：`PRD.md:1650`「非 B2 时 policy_version 与 approval binding 包含 profile 名，跨 profile 不能执行审批」。但 `decision.py:60-64` `_policy_version()` 只哈希三个政策 YAML 文件，**不含 profile**。approval binding 侧已含 profile（`approval/service.py:148`），故「跨 profile 不能执行审批」在 binding 层已满足；policy_version 的 profile 维度缺失是次级一致性缺口，且 B1 剖面仅 EVAL_MODE 启用、政策文件相同，实际影响低。

**建议**：后续组件（或本轮由协调者裁定）让 `_policy_version()` 在非 B2 时把 profile 名纳入摘要输入，或明确在 PRD 中把 policy_version 的 profile 职责收窄为「仅由 binding 层承担」。

---

### 逐项核对（本轮）

| 检查项 | 结论 |
|---|---|
| P1-1 / P2-1 / P2-2 / P2-3 | ✅ 全部真实修复（证据见上）。 |
| 固件三处改动 | ✅ 均为对齐生产/新锁序的最小必要改动，无一处放宽断言。 |
| G10 缺失 binding MAC | ✅ 判定为 P2 纵深防御缺口，可接受（G6 消费时校验，资金/授权不可经此移动）。 |
| `String(64)` 列宽与 hex 格式 | ✅ 前缀移除是列宽约束下的必要修正，非回归。 |
| 迁移/contracts/冻结区 | ✅ 本轮未改；`policy_version` 属 evaluations 行内值，非冻结契约字段。 |
