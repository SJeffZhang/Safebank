# L2 S1 System Events — 定点复审

**范围**：`dcb7b8e`..`72139c9`（`scheduled.py`、`fake.py`、`test_system_events.py`）
**日期**：2026-10-30
**结论**：**APPROVE**（无 P0/P1，2 条 P2 不阻塞）

---

## 已核对项

### 1. 事件 kind 路由（✅ 通过）

- `scheduled.py:544`：`event_kind = event.get("kind")` 取自 **L3 事件定义**（`l3.event(event_id)` 返回的不可变记录），不是回调请求体 → 回调方无法伪造 kind。
- `scheduled.py:546-554`：`FUND_CONFIRM` / `FUND_SETTLE` / `FUND_EXPIRY` → `_handle_system_event`（无 grant）；`DEMO_EVENT` → `_handle_demo_event`；其余（`SCHEDULED_TRANSFER` / `MERCHANT_CHARGE` 等）落回 grant 路径（grant 检查 + 复评均在，第 556-653 行末动）。
- 系统事件处理器 **不调用** `_load_grant`、`_backfill_grant`、`_re_evaluate`，不走 grant 路径。测试 `test_fund_confirm_success` 末尾断言 `not any(c["op"] == "execution" for c in app.fake.calls)` 验证无 grant 反查。

### 2. DEMO_EVENT 模式门禁（✅ 通过）

- `scheduled.py:342`：`if not (app.settings.demo_mode or app.settings.eval_mode)` → BLOCKED + `SCHEDULED_BYPASS`。
- **demo 关闭时**：L3 `event_execute` 不被调用（`test_demo_event_blocked_when_demo_disabled` 断言 `len(exec_calls) == 0`）。
- **demo 开启时**：正常执行（`test_demo_event_success_when_demo_enabled` 断言 `len(exec_calls) == 1`）。
- `_block` 内部（line 478）仍会调 `l3.event_block` 通知 L3——在 BLOCKED 路径下这是正确行为。

### 3. 证据类型（✅ 通过，P2 见下）

- **基金事件**：`OrderEvidence(type="SYSTEM_ORDER", order_id=event["source_id"])`（`scheduled.py:310-313`）——契约 `plan.py:164-166` 定义 `type: Literal["SYSTEM_ORDER"]`，正确。
- **DEMO_EVENT**：`DemoEvidence(type="DEMO_EVENT", kind="EXTERNAL_CREDIT")`（`scheduled.py:358-362`）。

### 4. FAILED_INSUFFICIENT 处理（✅ 通过）

- `scheduled.py:275`：`result.get("status") in ("FAILED", "FAILED_INSUFFICIENT")` → 从 `result.get("result", {}).get("code")` 取错误码，fallback `INSUFFICIENT_FUNDS`。
- `_finalize_claim` 将 `event_decisions` 行从 IN_PROGRESS 迁为 `FAILED_INSUFFICIENT`。
- **不对已终态的 job 调 `event_block`**：`_handle_system_event` 成功拿到 result 后直接走 `_finalize_system_result`，不经 `_block`。
- 测试 `test_fund_confirm_failed_on_l3_records_failure_not_success`：
  - 设 FakeL3 返回 `status: "FAILED_INSUFFICIENT"` + `result.code: SUITABILITY_MISMATCH`。
  - 断言 HTTP 响应 `status == "FAILED_INSUFFICIENT"`、`code == "SUITABILITY_MISMATCH"`。
  - 断言 `not any(c["op"] == "event_block" ...)`——未调 L3 event_block。
  - 查 DB `event_decisions` 行确为 `FAILED_INSUFFICIENT` / `SUITABILITY_MISMATCH`。
- **注意**：`_finalize_system_result` 也匹配裸 `"FAILED"`（没有 `INSUFFICIENT`），这是一个安全网，行为正确。

### 5. 占位行生命周期（✅ 通过）

全部路径均已覆盖：

| 路径 | 最终状态 | 机制 |
|---|---|---|
| 快照 L3Error | RETRYING | `_release_claim`（删 IN_PROGRESS） |
| 执行 L3Error / STATE_CHANGED | RETRYING | `_release_claim` |
| 执行成功 SUCCESS | SUCCESS | `_finalize_claim`（UPDATE IN_PROGRESS→SUCCESS） |
| 执行成功 FAILED_INSUFFICIENT | FAILED_INSUFFICIENT | `_finalize_claim`（UPDATE IN_PROGRESS→FAILED_INSUFFICIENT） |
| 外层未预期异常 | 重抛 | `scheduled.py:670-674`：`_release_claim` + re-raise |

无路径遗留 IN_PROGRESS。

### 6. 版本与 STATE_CHANGED（✅ 通过）

- `_handle_system_event` 以轻量快照（`customer_id` 仅含标识，`steps=[]`）调用 `l3.snapshot` 获取 `expected_versions` 与 `clock_version`，传给 `l3.event_execute`。
- 若 L3 因版本冲突返回 4xx（STATE_CHANGED）：`L3Client._request`（`client.py:86`）对所有 ≥400 响应抛出 `L3BusinessError(L3Error)` → `_handle_system_event` 的 `except L3Error`（`scheduled.py:324`）捕获 → `_release_claim` + `RETRYING`。
- **不会吞成 SUCCESS/500**：所有 L3Error 都显式 catch 并释放占位。
- 下一轮调度器重试时重新取快照，版本自动对齐。

### 7. 测试真实性与断言（✅ 通过）

9 条测试，覆盖：

| 测试 | 断言了什么 |
|---|---|
| `test_fund_confirm_success` | event_execute 调用次数 == 1、FakeL3 存储、无 grant 反查 |
| `test_fund_confirm_replay_returns_stored_result` | 首次 SUCCESS、二次 SUCCESS（replay）、二次无新 L3 调用 |
| `test_fund_settle_success` | 调用次数、状态 |
| `test_fund_expiry_success` | 调用次数、状态 |
| `test_demo_event_blocked_when_demo_disabled` | 状态 BLOCKED、code 正确、execute 未被调用 |
| `test_demo_event_success_when_demo_enabled` | 状态 SUCCESS、execute 调用 1 次 |
| `test_system_event_retrying_on_l3_unavailable` | 状态 RETRYING、code DEPENDENCY_UNAVAILABLE |
| `test_fund_confirm_failed_on_l3_records_failure_not_success` | HTTP 状态=FAILED_INSUFFICIENT、code=SUITABILITY_MISMATCH、无 event_block 调用、DB event_decisions 行确为 FAILED_INSUFFICIENT |
| （conftest 中原有的 grant 路径测试）| 不变，不在本次 diff 范围 |

测试使用 FakeL3（经真实 L3Client transport），每次 asyncio.run() 内独立建 app、独立 tmpfs PG，TRUNCATE 隔离。断言均验证了具体行为（调用次数、证据类型路径、决策行 DB 写入），非仅 HTTP 200。

---

## 问题清单

### P0 — 无

### P1 — 无

### P2（不阻塞，2 条）

#### P2-1：`DemoEvidence.kind` 写死 `EXTERNAL_CREDIT`

- **文件**：`services/gateway/app/internal/scheduled.py:362`
- **场景**：`_handle_demo_event` 中 `DemoEvidence.kind` 硬编码为 `"EXTERNAL_CREDIT"`。若演示种子注入的是扣款或 AA 付款类 DEMO_EVENT，证据标签会错标。契约 `plan.py:180` 允许三种 kind：`EXTERNAL_CREDIT` / `EXTERNAL_DEBIT` / `SPLIT_PAYMENT`。
- **实际影响**：**低**。DEMO_EVENT 仅在 demo/eval 模式执行，L3 的 `event_execute` 处理不看 evidence.kind（它通过 event_id 查事件定义的 kind），evidence 仅用于审计记录。因此证据中的错误 kind 不会影响执行逻辑，只会使审计日志中该字段不准确。
- **修复方向**：可从 L3 事件定义中读取 `event["kind"]` 并映射到 evidence.kind，或让 DemoEvidence 的 kind 字段与事件 kind 字段区分开。

#### P2-2：`_finalize_system_result` 对 replay 无感知

- **文件**：`services/gateway/app/internal/scheduled.py:275`
- **场景**：L3 `event_execute` 可能在 replay 时返回 `replayed: true`，当前 `_finalize_system_result` 不区分首次执行与重放——一律按 `result.status` 写入 event_decisions。
- **实际影响**：**极低**。`_claim_event` 已在 Phase A 检测终态行并直接返回存储结果，重放请求理论上不会到达 Phase B。但如果 `event_decisions` 行被 `_release_claim` 删除后（claim 超时被接管删除）L3 的 event_executions 仍保留，下一次进来会拿到 `replayed: true` 的结果，此时写入终态行为与首次一致，无错误。
- **修复方向**：可在 `_finalize_system_result` 中检查 `result.get("replayed")` 并记录到审计日志，或不修（当前行为已是幂等安全）。

---

## 命令行复跑

```bash
cd <worktree>
GATEWAY_DB_TESTS=1 uv run pytest tests/gateway/s1 -q
```
（需要 Docker 后台运行，本评审环境 Docker 不可用，未实际运行。以上结论基于静态源码分析。）