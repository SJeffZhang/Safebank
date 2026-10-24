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