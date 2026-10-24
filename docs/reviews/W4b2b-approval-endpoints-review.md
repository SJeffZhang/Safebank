# W4b2b Approval Endpoints — Independent Review

**Reviewer**: DeepSeek V4 Pro (只读)
**Date**: 2026-10-24
**Reviewed**: commit `4aa690e` (range `45b9b45..4aa690e`)
**Method**: 静态代码阅读 + 定点 pytest + `git diff` 追溯

---

## 结论：APPROVE

P0 无。P1 1 条（会话撤销竞态，不影响资金安全）。P2 3 条。

---

## P1

### P1-1：`_validate_confirmable` 读 sessions 行未加 `with_for_update()`，存在会话撤销竞态

- **File**：`services/gateway/app/api/approvals.py:162-168`
- **Failure scenario**：G6 confirm 与 `cancel_session`（W1）并发：confirm 读取 `revoked_at_wall=NULL` 通过检查，`cancel_session` 提交 `revoked_at_wall=now()`，confirm 继续将 approval 转为 CONFIRMED——已撤销会话下存在已确认审批。
- **Impact**：G6 不执行 L3（D1），无资金风险；但违反了「会话已撤销拒绝且 DB 不变」的规格约定。W4b1 `create_for_evaluation` 在 350 行锁了 sessions 行（`with_for_update()`），说明这是有意的锁序设计；G6/G7 应保持一致。
- **Fix**：在 `_validate_confirmable` 的 session 查询（163 行）加上 `.with_for_update()`。该行已在 `request_idempotent` 事务内，锁开销可接受。

---

## P2

### P2-1：缺少 SUPERSEDED / USED 状态的 G6/G7 拒绝测试

- **Files**：`tests/gateway/w4b2b/db/test_confirm.py`、`tests/gateway/w4b2b/db/test_reject.py`
- **Detail**：当前有 `test_confirm_already_confirmed_returns_409`（CONFIRMED）、`test_reject_already_rejected_returns_409`（REJECTED），但没有测试 SUPERSEDED 或 USED 审批状态的 confirm/reject。代码在 `approvals.py:148` 通过 `status != PENDING` 兜底，行为正确但无显式回归覆盖。
- **Recommendation**：补充一条 SUPERSEDED confirm → 409 的测试（一行 seed 参数变化）。

### P2-2：`ConfirmRequest` 缺少 `otp` 字段

- **File**：`services/gateway/app/api/approvals.py:51-52`
- **Detail**：PRD §9.5 G6 body 规格为 `{request_id, view_hash, otp: string?, risk_ack: bool}`，当前 `ConfirmRequest` 只有 `request_id`、`view_hash`、`risk_ack`。D6 已裁定 W4b2c 前红级一律拒，所以此缺口不阻塞当前组件。W4b2c 实现时需在模型中补 `otp: str | None = None`。
- **Mitigation**：Pydantic v2 默认 `extra="ignore"`，即使前端发送 `otp` 字段也不会导致 422。

### P2-3：G9 handler 未交叉校验 `view_json.approval_id` 与审批行 ID

- **File**：`services/gateway/app/api/approvals.py:232-237`
- **Detail**：G9 从 evaluations 读 `view_json`，覆盖 `status`/`expires_at_wall` 后直接返回。不验证 `view_json.approval_id == approval["id"]`。由于 W4b1 在同一事务中写入两者，实际不会不一致；但缺少防御性断言。
- **Recommendation**：加一行 `assert view.get("approval_id") == approval_id` 或无副作用跳过（防御性编程，非阻塞）。

---

## 已验证正确的关键点（抽样）

| 检查项 | 证据 | 结论 |
|---|---|---|
| 调用方矩阵 | `auth/matrix.py:74-78`：G9/G6/G7 只允许 `F_APPROVE`，G17 只允许 `F_APP` | ✅ A 令牌无法访问 |
| origin/customer_id 不来自请求体 | `api/approvals.py:204-206,252-256,440-442`：全部从 `request.state.caller` 读取 | ✅ |
| view_hash 精确逐字节匹配 | `api/approvals.py:156-157` + `test_confirm.py:192-209` | ✅ |
| 红级审批 503 D6 | `api/approvals.py:274-277`：risk=="RED" → 503 `DEPENDENCY_UNAVAILABLE`，DB 不变 | ✅ `test_confirm_red_returns_503` 断言 approval 仍 PENDING |
| risk_ack D4 | `api/approvals.py:284-285`：只在 `risk_ack_required=true` 时要求 | ✅ 两测试覆盖 true/false |
| D1 同一短事务 | `api/approvals.py:288-301`：`state.transition(approval)` + `state.transition(plan)` 在同一 `produce()` 内 | ✅ |
| D2 不覆盖哈希域字段 | `planview.py:7-8,376` 确认 status/expires_at_wall 是 envelope；G9:236-237 只覆盖这两个 | ✅ `risk_ack_required`/`otp_required` 在哈希域内，未被覆盖 |
| D3 risk 通过 JOIN | `api/approvals.py:458-464`：`approvals JOIN evaluations ON evaluation_id + plan_id` | ✅ `test_list_shows_risk_via_join` 断言 RED |
| D5 cursor 按 approval_id DESC | `api/approvals.py:479-481`：`WHERE id < cursor ORDER BY id DESC` | ✅ `test_list_cursor_pagination` 验证 DESC 顺序 |
| request_idempotent | `api/approvals.py:323-331`：与 produce 同事务，异常时行被清理（`idempotency.py:61-68`） | ✅ `test_confirm_idempotent` 验证审计只写入一次 |
| 审计 append_tx | `api/approvals.py:334-343`：事务外，仅非回放 | ✅ 测试验证 audit_events 行存在 |
| 确认后不执行 L3 | `api/approvals.py:288-308`：没有 L3 调用 | ✅ |
| 未新增迁移 | `git diff --stat` 中无 `migrations/` 文件 | ✅ |
| 错误码来自 contracts | 所有 `raise GatewayError(Code.X)` 均用 safebank_contracts.codes | ✅ 无新增 code |
| D0 实例隔离 | `adapter.py:17-18`：从 `request.app.state` 读取；`test_adapter.py:79-197` 两不同 key app 自确认成功、交叉确认被拒 | ✅ 协调者实测：临时全局化 adapter → 10 failed |
| D0 adapter 无全局 configure | `adapter.py` 全文不引用 `service.configure` | ✅ |
| D5 无新增迁移 | `git diff --stat` 0 行在 `migrations/` | ✅ |

---

## 未确认（非发现）

- G17 `next_cursor` 当 `len(items) == limit` 且最后一条恰好是末端时，`next_cursor` 非 None 导致调用方多一次空请求。语义正确，不是 bug。
- 测试中 `view_json={"risk_ack_required": False}` 配合 YELLOW risk 是人为构造（生产 `planview.py:389` 对 YELLOW 设为 True），但测试意图是验证 D4「只看 view_json」，无问题。

---

## 专项测试结果

| 命令 | 结果 |
|---|---|
| `.venv/bin/pytest tests/gateway/w4b2b/test_adapter.py -q` | 5 passed, 1 skipped (纯函数) |
| `.venv/bin/pytest tests/gateway/w4b2b/db/test_confirm.py -q` | 16 skipped (需 `GATEWAY_DB_TESTS=1`，协调者已跑全量 51 passed) |
| `.venv/bin/pytest tests/gateway/w4b2b/db/test_reject.py -q` | 12 skipped (同上) |
| `.venv/bin/pytest tests/gateway/w4b2b/db/test_g9_get.py -q` | 9 skipped (同上) |
| `.venv/bin/pytest tests/gateway/w4b2b/db/test_list.py -q` | 9 skipped (同上) |