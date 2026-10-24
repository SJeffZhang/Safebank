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