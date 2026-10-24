# C1c2 Gateway Graph Routing — Independent Review

**Reviewer:** DeepSeek V4 Pro (read-only, headless CLI)
**Date:** 2026-10-24  
**Scope:** `git diff 22dbb48 478b673` (main 8ad47d0 merged as 22dbb48)  
**Verdict:** REQUEST_CHANGES

## Summary

Routing functions (`routing.py`) are correct and well-tested for every PRD §6.1.1 row (54 routing tests pass, 0.47s). The critical bugs are at the **graph topology level** — `_route_evaluate` unconditionally blocks non-respond routes from error states, and `execute → respond` is a static edge that prevents STATE_CHANGED re-entry. Both break PRD §6.1.1 requirements.

---

## P0 — Must fix before merge

### P0-1: `_route_evaluate` blocks 422 CONTRACT_INVALID → gather

**File:** `services/agent/app/graph/graph.py:540-543`

`_route_evaluate` returns `"respond"` whenever `state.get("route_error")` is non-None. Every error path in the evaluate node sets `route_error` (lines 423, 429, 435), including the 422 → gather case. `route_evaluate_error(422, "CONTRACT_INVALID", 0)` correctly returns `RouteResult("gather", ...)` but the graph never reaches "gather" because `_route_evaluate` clamps all errors to "respond".

**Failure scenario:** L2 returns 422 CONTRACT_INVALID. Graph's evaluate node catches `GatewayResponseError`, computes `r.node = "gather"`, returns `{replan_count: 1, route_error: {...}, decision: None}`. `_route_evaluate` reads updated state, sees `route_error != None`, returns `"respond"`. User sees error template instead of plan revision.

**Fix:** `_route_evaluate` must not block routes when the error handler explicitly chose a non-respond node. Options: (a) check `r.node` before setting `route_error` and skip it when routing to gather/execute; or (b) replace `_route_evaluate`'s blanket check with per-code logic that mirrors `route_evaluate_error`.

### P0-2: `execute → respond` static edge prevents STATE_CHANGED re-entry

**File:** `services/agent/app/graph/graph.py:595-596`

```python
builder.add_edge("execute", "respond")  # static edge
```

PRD §6.1.1 requires: 409 STATE_CHANGED with ALLOW decision → execute again (at most once). Same for REQUIRE_APPROVAL → await_approval. But the graph has no conditional edge from execute — it always goes to respond. `route_execute_error` correctly computes `r.node = "execute"` / `r.node = "await_approval"`, but the graph topology cannot follow those routes.

**Failure scenario:** L2 returns 409 STATE_CHANGED with ALLOW decision. Execute node sets new decision/plan_id. Graph follows static edge to respond. User sees template text instead of re-execution.

**Fix:** Replace `add_edge("execute", "respond")` with a conditional edge `add_conditional_edges("execute", _route_execute, {...})` that checks state for re-entry routing. The routing function must handle: normal 200 → respond; 202 UNKNOWN → respond; 409 with decision → execute/await_approval; 409 without decision → respond; transport/503 → respond.

---

## P1 — Should fix in this component

### P1-1: No `clarify` node in gateway graph

**File:** `services/agent/app/graph/graph.py`

G21 clarification data is ingested and stored in state (`clarification_id`, `clarification`, `parse_status`), but the graph has no `clarify` node. PRD §6.1 includes a clarify route for OPEN clarifications. The `_route_understand` function handles INTENT_UNCLEAR → respond, but an existing OPEN clarification (FORM_REQUIRED/SLOTS) has no routing edge to a clarify handler. Deferring to C1d is plausible but the progress doc doesn't document this gap.

### P1-2: `_GatewayExecute.request_id` has dead code

**File:** `services/agent/app/gateway/adapters.py:99-105`

```python
request_id = (
    state.get("plan_draft", {}).request_id          # block 1
    if hasattr(state.get("plan_draft", {}), "request_id")
    else "req_unknown"
)
if hasattr(state.get("plan_draft", {}), "request_id"):
    request_id = state["plan_draft"].request_id      # block 2 (overwrites block 1)
```

Block 2 unconditionally overwrites block 1's result when the condition holds. Both blocks have the same condition — only block 2 is effective, block 1 is dead code. Also calls `state.get("plan_draft", {})` 3 times. Net result is correct but misleading.

### P1-3: Read bridge defined but not wired into graph

**File:** `services/agent/app/gateway/adapters.py:169-192`

`make_gateway_read_executor` creates a `ReadExecutor` bridging `GatewayClient.read_tool` but is never called by `build_gateway_graph`. The gather node uses whatever `ports.gather` was passed in. The progress doc says this is deferred to C1d — acceptable for scope, but the function's existence implies wiring that doesn't exist. Progress doc should explicitly note the gap rather than saying "at the routing level only."

### P1-4: `ExecutionResult.status=FAILED` not distinguished from EXECUTED at routing level

**File:** `services/agent/app/graph/routing.py:156-166`

`route_execute_success()` returns `RouteResult("respond")` without `use_fixed_reply` for both EXECUTED and FAILED results. PRD §6.1.1 says FAILED → respond with BLOCKED card. The respond node calls the LLM port for both cases. A FAILED execution should use a fixed template ("执行失败：{error_code}"), not the LLM. The progress doc says C1e handles cards, but the routing should at minimum flag FAILED for fixed template.

---

## P2 — Nice to fix

### P2-1: `invoke_gateway_input` double-calls G20

**File:** `services/agent/app/graph/invoke.py:38-70`

Pre-fetch G20 for validation, then graph ingest calls G20 again. Acknowledged in docstring. Wasteful but not harmful unless G20 responses race (unlikely for same-session read).

### P2-2: `route_evaluate`'s `r.state` dead code on success path

**File:** `services/agent/app/graph/routing.py:79-115`

For REPLAN → gather, `route_evaluate` returns `r.state = {"replan_count": replan_count + 1}` but `_route_evaluate` only returns `r.node` — the state update is discarded. The gather node independently increments replan_count. Works correctly by accident; confusing for maintainers.

### P2-3: `AgentState.decision` type vs runtime type

**File:** `services/agent/app/graph/state.py:122`

Annotated as `Decision | None` but gateway graph stores `dict` (from `model_dump(mode="json")`). `_build_gather_node` and `_route_evaluate` handle both shapes, but any code accessing `state["decision"].findings` expecting a `Decision` object crashes. P2 until code paths exist that access decision without isinstance/validation.

### P2-4: `parse_status` extraction fragile nested ternary

**File:** `services/agent/app/graph/graph.py:362-367`

`gateway_utts[0]` indexing before the empty check on `gateway_utts` — safe in practice (G20 returns non-empty for valid IDs) but the empty-guard `if gateway_utts` on the last line only protects `str(gateway_utts[0].parse_status)`, not the enum `.value` access on the line above. Would IndexError on truly empty response.

---

## Checks passed (no issues found)

| Check | Result |
|---|---|
| Frozen files unchanged (C1c1 client/wire, contracts, gateway, bankcore, L3, dependencies, Makefile, CI, compose) | ✅ PASS |
| pyproject.toml unchanged — no new marker/deselect/live config | ✅ PASS |
| 253 vs 266 diff = `-k "not test_llm_live"` (13 C1b live tests deselected) | ✅ PASS (no config issue) |
| JWT/assistant_token never in AgentState or checkpoint | ✅ PASS (only in AgentContext/runtime) |
| G20 authoritative data replaces caller-supplied text/scope in ingest | ✅ PASS |
| null hint/action text preserved as kind/scope, not typed | ✅ PASS |
| Clarification parent matching enforced in `ingest_via_gateway` | ✅ PASS |
| INTENT_UNCLEAR blocks plan via understand node override | ✅ PASS |
| Two contexts no token leak (tested via header assertions) | ✅ PASS |
| REPLAN whitelist enforces evidence_refs ∩ READ_ARGS | ✅ PASS |
| REPLAN user-resolver → respond, not gather | ✅ PASS |
| 422 + REPLAN shared budget (max 2) tested | ✅ PASS |
| STATE_CHANGED re-entry limit (max 1) enforced in routing function | ✅ PASS |
| UNKNOWN template no poll/retry instructions | ✅ PASS |
| 401/403/404/429/503 all route to respond with fixed templates | ✅ PASS |
| G4/G8 fake boundaries clearly labelled in docstrings and tests | ✅ PASS |
| Owner whitelist respected (only graph/, gateway/adapters, tests/routing, docs) | ✅ PASS |
| All 54 routing tests pass (0.47s) | ✅ PASS |
| `respond_template` mapping covers all PRD §6.1.1 error codes | ✅ PASS |
| `route_error` dict is safe — no httpx Exception, raw message, or traceback | ✅ PASS |

---

## Unconfirmed

- **422 → gather path integration**: Whether the `_route_evaluate` intercept (P0-1) is the ONLY blocker or whether there's a secondary issue in how LangGraph handles the state transition from evaluate error → gather (e.g., gather node seeing `decision: None`). The routing function is correct; the graph integration is the issue.
- **G8 not implemented**: Cannot verify whether `GatewayClient.execute` properly returns 202/UNKNOWN vs raising for transport errors since L2 G8 is not available. The routing handles both paths in theory.

---

## 修复轮 1 定点复审（2026-10-24，478b673..1e303e7）

**Reviewer:** DeepSeek V4 Pro (read-only, headless CLI)  
**Verdict:** APPROVE — all 6 P0/P1 findings closed, no new P0/P1 introduced.

### 逐条核对

| # | 原发现 | 修复方式 | 证据 | |
|---|---|---|---|---|
| P0-1 | `_route_evaluate` 拦截 422→gather | `route_error` 字典新增 `target` 字段；`_route_evaluate_gw` 检查 `target=="gather"` 时返回 `"gather"` 而非 `"respond"` | `graph.py:414` (`err["target"] = r.node`)，`graph.py:586-588` (target 检查)；编译图 `test_p01_422_*` 2 条全部通过 | ✅ |
| P0-2 | `execute→respond` 静态边阻止 STATE_CHANGED 重入 | `add_edge("execute","respond")` 替换为 `add_conditional_edges("execute", _route_execute, {"execute":..., "await_approval":..., "respond":...})`；`_route_execute` 按 `route_error.target` 路由 | `graph.py:772-779` (条件边)，`graph.py:600-613` (`_route_execute` 函数)；编译图 `test_p02_state_changed_*` 3 条全部通过 | ✅ |
| P1-1 | 缺 clarify 节点 | 新增 `clarify` 节点（从 G21 `candidates[].label` 生成固定补充草稿）+ `_route_understand_gw` 路由（unclear+needs_write+clarification 或 CLARIFYING+clarification → clarify）+ `clarify→respond` 边 | `graph.py:514-527` (节点)，`graph.py:549-567` (路由)，`graph.py:734,754` (边)；`test_p11_*` 通过，reply 含"王五" | ✅ |
| P1-2 | `_GatewayExecute.request_id` 死代码 | 简化为 `draft = state.get("plan_draft"); request_id = draft.request_id if draft is not None else "req_unknown"` | `adapters.py:98-99`（diff 确认 7行→2行，无遗留） | ✅ |
| P1-3 | 读桥接未接入图 | `build_gateway_graph` 新增 `gateway_read_client` 可选参数；提供时 gather 节点 monkey-patch `_read_executor` 每次调用后恢复 | `graph.py:617-651` (gateway gather)；`test_p13_*` 通过 — `source_ref="src_read_1"`、versions/observed_at 保留、契约 READ_CASES 匹配 | ✅ |
| P1-4 | FAILED 与 EXECUTED 未区分 | `route_execute_success` 增加 `exec_status` 参数，FAILED 返回 `use_fixed_reply=True` + "执行失败" 模板；graph execute 节点在 FAILED 时设置 `route_error` 使 respond 跳过 LLM | `routing.py:160-162,170-171` (exec_status 分支)，`graph.py:507-511` (FAILED 路径)；`test_p14_*` 通过 — `calls["respond"]==0`，reply 含"执行失败" | ✅ |

### 关键路径回归（编译图实测通过）

| 路径 | 测试 | 断言 |
|---|---|---|
| 422→gather→plan→ALLOW→execute | `test_p01_422_contract_invalid_routes_to_gather_via_compiled_graph` | replan_count=1, execution EXECUTED |
| 422 两次后第三次 blocked | `test_p01_422_twice_then_respond` | replan_count=2, "暂时无法处理" |
| STATE_CHANGED+ALLOW 重入 execute | `test_p02_state_changed_allow_reenters_execute_via_compiled_graph` | evaluate_reentry_count=1, execution EXECUTED |
| STATE_CHANGED+REQUIRE_APPROVAL 重入 await | `test_p02_state_changed_require_approval_reenters_await` | interrupt approval_id="apv_new" |
| STATE_CHANGED 二次重入 blocked | `test_p02_state_changed_second_reentry_blocked` | reentry_count=1, "重新发起" |
| 202 UNKNOWN | `test_execute_202_unknown_via_compiled_graph` | "确认中", 无重试指令 |
| 503→UNKNOWN | `test_execute_503_via_compiled_graph` | "确认中", execution=None |
| 409 APPROVAL_EXPIRED | `test_execute_409_approval_expired_via_compiled_graph` | "审批", execution=None |
| ALLOW 全路径 | `test_allow_path_full_graph` | execution EXECUTED |
| DENY | `test_deny_path_full_graph` | "无法执行", execution=None |
| REQUIRE_APPROVAL interrupt+resume | `test_require_approval_interrupt_full_graph` | interrupt→resume→EXECUTED |
| REPLAN agent→gather→ALLOW | `test_replan_agent_full_graph` | replan_count=1, EXECUTED |
| INTENT_UNCLEAR | `test_intent_unclear_full_graph` | understanding.unclear=True |

### 回归核查

- **P2-1~P2-4（未修，限定范围内）**: 双 G20 调用、r.state 死代码、decision 类型不一致 — 均未恶化，C1d 解决。P2-4（parse_status 三元嵌套）已改善：加 `len(gateway_utts) > 0` 守卫（`graph.py:360`）。
- **冻结文件**: diff 确认 contracts/gateway/bankcore/L3/pyproject/Makefile/CI 零改动
- **JWT 不进 state**: 修复中无新增 `assistant_token` 写入 AgentState 路径
- **REPLAN 共享预算（自动修订 + 422 共享 2 次）**: `test_p01_422_twice_then_respond` 验证 422 两次后第三次 blocked；`route_evaluate_error(422, ...)` 正确检查 `replan_count >= 2`
- **STATE_CHANGED 有界重入（最多 1 次）**: `test_p02_state_changed_second_reentry_blocked` 验证第二次 STATE_CHANGED 被 block
- **UNKNOWN 不重提**: `test_execute_202_unknown_via_compiled_graph` 验证模板"确认中"、无 poll/"重试"字样
- **FAILED 走固定模板**: `test_p14_*` 验证 LLM respond 零次调用
- **clarify 不洗白**: clarify 节点只从 L2 G21 `candidates[].label` 生成补充文本，不创建 typed 原话、不授权

### 新发现问题

**P2: evaluate 成功路径两次调用 `route_evaluate`**（`graph.py:434` + `graph.py:595`）：evaluate 节点预计算 `reply_draft` 时调用一次，`_route_evaluate_gw` 路由时再调用一次。输入相同、结果一致，仅冗余。P2。

**P2: 读桥接 monkey-patch 非线程安全**（`graph.py:629-637`）：`adapter._read_executor` 的临时替换在并发调用同一 compiled graph 时会竞态。C1c2 单线程测试不受影响，C1d 需改为每次调用新建 adapter 实例。P2。

### 测试结果

| 命令 | 结果 |
|---|---|
| `.venv/bin/pytest tests/agent/routing -q` | 70 passed (54 原始 + 16 编译图) |
| `.venv/bin/pytest tests/agent/routing/test_graph_compiled.py -q` | 16 passed (0.91s) |
| `.venv/bin/pytest tests/agent -q -k "not test_llm_live"` | 269 passed, 13 deselected |

### 结论

所有 6 条 P0/P1 已修复并经验证。两处新 P2 不阻塞合并（C1d 可自然解决：双调用属去重优化，线程安全属 PG 服务层职责）。APPROVE。