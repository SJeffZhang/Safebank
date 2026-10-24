# C1c2 Gateway Graph Routing — Implementation Progress

**Date:** 2026-10-24

**Branch:** codex/c1c2-gateway-routing (main b281023 merged as 22dbb48)
**Owner:** C1c2 routing only; C1a/b/C1c1 frozen

## Files Changed

### New files

| File | Purpose |
|---|---|
| `services/agent/app/graph/routing.py` | Pure routing functions (PRD §6.1.1) — no side effects, no HTTP |
| `services/agent/app/gateway/adapters.py` | Gateway-backed ports factory (`make_gateway_ports`, `ingest_via_gateway`) |
| `tests/agent/routing/__init__.py` | Package init |
| `tests/agent/routing/test_ingest.py` | Task 1: 7 tests — G20/G21 ingest, null hint, clarification parent, INTENT_UNCLEAR, token isolation |
| `tests/agent/routing/test_reads_replan.py` | Task 2: 21 tests — REPLAN whitelist, 422 shared budget, ALLOW/DENY/REQUIRE_APPROVAL, error code routing |
| `tests/agent/routing/test_routes.py` | Task 3: 26 tests — execute routes (200, 202, 409, STATE_CHANGED re-entry, 401/403/404, transport error, templates) |

### Modified files

| File | Change |
|---|---|
| `services/agent/app/graph/__init__.py` | Added `build_gateway_graph`, `invoke_gateway_input` exports |
| `services/agent/app/graph/state.py` | Added C1c2 gateway fields: `parse_status`, `echoes_untrusted`, `parent_ids`, `clarification_id`, `clarification`, `hints`, `route_error`, `evaluate_reentry_count` |
| `services/agent/app/graph/graph.py` | Added `build_gateway_graph` with G20/G21 ingest + gateway-aware evaluate/execute + shared node builders |
| `services/agent/app/graph/invoke.py` | Added `invoke_gateway_input` (IDs-only input, TYPE_CHECKING lazy import to avoid circular dep) |

## Red → Green Flow

### Task 1: Authoritative ingest
- Wrote 7 red tests → all now green (ingest_via_gateway, GatewayUtterance fields, clarification parent matching, INTENT_UNCLEAR, token isolation)
- Implementation: `ingest_via_gateway`, `gateway_utterance_to_ref` in adapters.py, `build_gateway_graph` ingest node

### Task 2: Context-safe reads and replan
- Wrote 21 routing tests → all green (replan_whitelist_tools, route_evaluate, route_evaluate_error)
- Implementation: `replan_whitelist_tools`, `route_evaluate`, `route_evaluate_error`, `respond_template` in routing.py
- NOTE: Read bridge through GatewayClient (G2) is at the routing level only; actual GatherAdapter+GatewayClient integration is tested via scripted transport in C1c1 tests, not duplicated here

### Task 3: Decision/execute routes and uncertainty
- Wrote 26 routing tests → all green (route_execute_success, route_execute_error with STATE_CHANGED/POLICY_CHANGED/409 variants/503/transport errors)
- Implementation: `route_execute_success`, `route_execute_error` in routing.py
- STATE_CHANGED re-entry limit: max 1, enforced via `evaluate_reentry_count`
- UNKNOWN: fixed template, no poll, no retry

## Exit Commands (all actual runs, 2026-10-24)

| Command | Result |
|---|---|
| `make lint` | ✅ All checks passed (ruff check + ruff format) |
| `.venv/bin/pytest tests/agent -q -k "not test_llm_live"` | ✅ 253 passed, 13 deselected, 0 skipped, 0 xfail |
| `make test` | ✅ 2457 passed, 561 skipped |
| `make contracts-check` | ✅ PASS: contracts v1 match frozen schemas |
| `make demo` | ❌ exit 2 — "demo is not implemented in P1" (expected, not faked) |

## Fake / Unimplemented Boundaries

1. **G4 (approval panel / build_view / create_for_evaluation)**: NOT implemented in L2 as of 2026-10-24. `make_gateway_ports` creates evaluate/execute ports that call `GatewayClient` but the actual G4/G8 handlers on L2 are not yet wired. The routing functions handle `REQUIRE_APPROVAL` → `await_approval` but the approval_id binding and view_hash verification are not exercised.

2. **G8 (execution pipeline)**: NOT implemented in L2. `GatewayClient.execute` returns a placeholder; actual prepare/execute with customer locks, preflight replays, and atomic submission is not available. The routing handles UNKNOWN/transport errors correctly but these paths cannot be tested with real L2.

3. **Read bridge through GatherAdapter+GatewayClient**: The routing functions (Task 2) define the whitelist and budget logic. The actual integration of `GatherAdapter` with a gateway-backed `read_executor` is tested at the routing level; end-to-end with scripted LLM+GatewayClient is deferred to C1d (PG saver / service wiring).

4. **REPLAN after STATE_CHANGED re-entry**: The routing allows it (respond with template) but the budget is tracked via `evaluate_reentry_count` separately from `replan_count`. These two counters do not share a budget — they track different things (evaluate re-entry vs plan revision).

5. **C1d (PG saver, SSE, service lifecycle)** and **C1e (output, cards, replay)**: Not implemented. `build_gateway_graph` uses InMemorySaver for testing; production Postgres saver and SSE event streaming are C1d concerns.

6. **`make demo`**: Not implemented yet (exits 2). Not faked to pass.

## Design Decisions

1. **Lazy imports in graph.py and invoke.py**: To avoid circular import (`graph.graph` ↔ `gateway.client` ↔ `graph.state`), all gateway-sourced imports in `build_gateway_graph` use `TYPE_CHECKING` or lazy `import` inside the function body. This preserves the C1c1 frozen module structure.

2. **`UtteranceRef` stays L1-local**: G20 returns `GatewayUtterance` with full wire models; `ingest_via_gateway` converts them to L1-only `UtteranceRef` (with text="" for null). The full `GatewayUtterance` data (parse_status, echoes, etc.) is stored in AgentState for routing, not in `UtteranceRef`.

3. **Fixed reply shortcut**: When `route_error` is set and `reply_draft` is pre-populated by the routing function, the `respond` node skips the LLM port and returns the template directly. This avoids calling LLM on protocol/transport errors.

4. **Routing functions are pure**: `routing.py` has no imports from gateway, state, or LLM modules (except `replan_whitelist_tools` which needs `READ_ARGS` from contracts). All functions are testable without any HTTP or graph infrastructure.

## Open / Pending

1. G4/G8 real L2 services → W4b/W3b completion needed before end-to-end gateway graph can be exercised with real L2
2. GatherAdapter gateway bridge → needs C1d for PG saver + service wiring before real end-to-end
3. `build_gateway_graph` `_route_evaluate` uses `GatewayDecision.model_validate` which may raise on unexpected wire shapes → handled as "respond"
4. STATE_CHANGED + REQUIRE_APPROVAL → await_approval: approval_id from `details.decision` is passed via state, but the resume value check (`interrupt({"approval_id": approval_id})`) needs real BFF to provide the matching value

## Commit Plan

Each task as a small atomic commit, Co-Authored-By: DeepSeek V4 Pro via Claude Code <noreply@anthropic.com>.
Final SHA + workspace state reported after freeze.

## 修复轮 1 (2026-10-24)

**评审报告:** `docs/reviews/C1c2-gateway-routing-review.md` (REQUEST_CHANGES, P0×2 P1×4)

### P0-1: 422 CONTRACT_INVALID → gather 被 `_route_evaluate` 拦截

- **红测:** `test_p01_422_contract_invalid_routes_to_gather_via_compiled_graph` 经编译图确认 422 后 `plan_id == None`（路由到 respond 而非 gather）
- **修复:** `route_error` 增加 `target` 字段记录错误处理器的目标节点；`_route_evaluate_gw` 检查 `target=="gather"` 时返回 `"gather"` 而非无条件 `"respond"`
- **绿:** 编译图测试已通过（422→gather→plan→evaluate→ALLOW→execute）

### P0-2: execute→respond 静态边阻止 STATE_CHANGED 重入

- **红测:** `test_p02_state_changed_allow_reenters_execute_via_compiled_graph` 确认 STATE_CHANGED 后 `execution is None`（静态边到 respond）
- **修复:** `builder.add_edge("execute", "respond")` 替换为 `add_conditional_edges("execute", _route_execute, {"execute":"execute","await_approval":"await_approval","respond":"respond"})`；`_route_execute` 检查 `route_error.target`
- **绿:** 编译图测试已通过（STATE_CHANGED+ALLOW→execute, STATE_CHANGED+REQUIRE_APPROVAL→await_approval, 二次 STATE_CHANGED→respond）

### P1-1: 缺 clarify 节点

- **红测:** `test_p11_open_clarification_routes_to_clarify` 确认 G21 澄清数据无路由
- **修复:** 新增 `clarify` 节点（生成含候选人标签的固定草稿）+ `_route_understand_gw` 增加 `clarify` 路由（unclear+needs_write+clarification 或 CLARIFYING+clarification）+ `clarify→respond` 边
- **绿:** 编译图测试已通过（`clarification.id=="clr_1"`, reply 含 "王五"）

### P1-2: `_GatewayExecute.request_id` 死代码

- **红测:** 静态审查确认（adapters.py:99-105）
- **修复:** 简化为 `draft = state.get("plan_draft"); request_id = draft.request_id if draft is not None else "req_unknown"`
- **绿:** 编译图测试通过，`client.execute` 调用正确传入 `request_id`

### P1-3: 读桥接已定义但未接入图

- **红测:** `test_p13_gateway_read_bridge_wired_into_gather` 验证 `make_gateway_read_executor` 通过 GatewayClient 桥接读并保留元数据
- **修复:** `build_gateway_graph` 新增 `gateway_read_client` 参数；当提供时 gather 节点注入 gateway-backed executor（monkey-patch `_read_executor` 每次调用后恢复）；适配器单元测试使用 `READ_CASES` 已验证契约
- **绿:** 单元测试通过（source_ref/src_read_1, versions/observed_at 保留, 契约匹配）

### P1-4: ExecutionResult FAILED 与 EXECUTED 未区分

- **红测:** `test_p14_execution_failed_uses_fixed_template_not_llm` 确认 FAILED 调用了 LLM respond（`calls["respond"]==1`）
- **修复:** `route_execute_success` 增加 `exec_status` 参数，FAILED 返回 `use_fixed_reply=True`；graph execute 节点在 FAILED 时设置 `route_error` 使 respond 节点使用固定模板
- **绿:** `calls["respond"]==0`, reply 含 "执行失败"

### Exit commands (修复轮 1)

| Command | Result |
|---|---|
| `make lint` | ✅ All checks passed |
| `.venv/bin/pytest tests/agent -q -k "not test_llm_live"` | ✅ 269 passed (+16 from 253), 13 deselected |
| `make test` | ✅ 2473 passed (+16), 561 skipped |
| `make contracts-check` | ✅ PASS |
| `make demo` | ❌ exit 2 (not implemented, not faked) |

### 新增测试

16 项编译图集成测试 (`tests/agent/routing/test_graph_compiled.py`):
- P0-1: 2 tests (422→gather, 422 twice→block)
- P0-2: 3 tests (STATE_CHANGED re-entry, await_approval, double block)
- P1-4: 1 test (FAILED vs EXECUTED)
- P1-1: 1 test (clarification→clarify node)
- P1-3: 1 test (gateway read bridge)
- Regression: 8 tests (UNKNOWN, APPROVAL_EXPIRED, 503, ALLOW, DENY, REQUIRE_APPROVAL interrupt, REPLAN, INTENT_UNCLEAR)