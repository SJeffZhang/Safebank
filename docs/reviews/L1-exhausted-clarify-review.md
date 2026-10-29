Independent Read-Only Review: L1 `ExhaustedClarify`

_Date: 2026-10-28 | Reviewer: DeepSeek V4 Pro (read-only, headless)_
_Worktree: `../R-L1clar`, detached `05f65e1` | Diff: `5e11bb5..05f65e1`_

---

Conclusion: **APPROVE** — no P0 or P1 findings. Five P2 observations (all non-blocking).

---

## P0 — Correctness / Safety

None.

The core safety invariant holds: when all remaining validation problems are user-only
missing parameters (`amount_cents`, `account_no`, `name`, `bank_name`) and retries
are exhausted, the plan node produces a clarification response **without creating a
plan_draft, routing to evaluate, or calling L2**.  There is no path for the model to
bypass validation by exploiting this code path.

## P1 — Edge-case Defect or Spec Violation

None.

---

## P2 — Minor / Latent / Polish

### P2-1. `_exhausted_clarify_fields_from_ve` skips container-level errors

**File:** `services/agent/app/llm/plan.py:118–121`

When iterating `ValidationError.errors()`, errors with `"args" not in loc` (e.g.
a step-index-level error like `('steps', 0)`) are silently `continue`-d.  If such
an error coexists with user-only null fields on other steps, the function may return
those user-only fields and produce `ExhaustedClarify` — even though a structural
error (dropped child step) also requires model correction, not just user input.

In practice this is unlikely (the model must simultaneously drop a step AND use
`__NULL__` for user-only fields on another), and the ReferenceError handler's
`not ref_problems and not sem_problems and not ch_problems` precondition further
narrows the window.  Still, the `continue` is a latent inconsistency: the function's
contract ("ALL errors are user-only null") is violated when a container-level error
is present but ignored.

### P2-2. Non-gateway-graph ingest does not reset `clarification`

**File:** `services/agent/app/graph/graph.py:145–158`

The non-gateway graph's `ingest` node (used by `build_graph`, line 129–158) does
not include `clarification` in its return dict.  If someone later adds
`ExhaustedClarify` catching to the non-gateway graph's `plan` node (currently
line 177–178, which has no try/except), the `clarification.source = "l1_exhausted"`
from a previous round would persist across rounds.  The gateway graph is safe:
its `ingest` (line 358–390) resets `clarification` from G21.

Currently this is **not exploitable** because the non-gateway graph's plan node
never raises `ExhaustedClarify`.

### P2-3. ExhaustedClarify path does not clear stale `plan_draft`

**File:** `services/agent/app/graph/graph.py:710–718`

When the plan node catches `ExhaustedClarify`, it returns `reply_draft` and
`clarification` but does **not** explicitly set `plan_draft: None` (or
`decision: None`, `approval_id: None`, etc.).  A `plan_draft` from a prior
round (if somehow still in checkpoint) would persist in memory until the next
ingest.  Currently harmless — no node between plan→respond→verify_output reads
`plan_draft` — but would become a correctness issue if a future routing or
respond variant depends on `plan_draft is None` as a "no plan produced" signal.

### P2-4. Single-field clarification text is awkward

**File:** `services/agent/app/graph/graph.py:715`

```python
"reply_draft": f"请提供以下信息：{'、'.join(ec.labels)}。"
```

For a single missing field (e.g. "开户银行"), this produces:

> 请提供以下信息：开户银行。

A more natural phrasing would be "请提供开户银行。" for single fields.  The
multi-field case ("请提供以下信息：收款人姓名、收款账号。") is fine.  This is
a UX polish issue; no correctness or security impact.

### P2-5. `_route_plan_after` requires exact string match on `source`

**File:** `services/agent/app/graph/graph.py:63–66`

```python
clarification = state.get("clarification") or {}
if clarification.get("source") == "l1_exhausted":
    return "respond"
```

If the `clarification` dict were ever mutated or processed by another component
(e.g. a middleware that normalizes or re-wraps the dict), the exact string match
would silently fall through to the `"evaluate"` branch.  In the current codebase
this cannot happen — the only producer of `"l1_exhausted"` is the plan node
and the only consumer is `_route_plan_after`.  Worth noting for future maintainers.

---

## Items Verified

| # | Claim | Verdict |
|---|-------|---------|
| 1 | `_USER_ONLY_PARAMS` = `{name, account_no, bank_name, amount_cents}` — all truly user-only | ✓ Confirmed — none can come from gather reads |
| 2 | `_exhausted_clarify_fields` returns `None` when any problem is NOT a null/empty-string issue | ✓ Confirmed — regex guards, non-user field guards, returning `None` |
| 3 | `_exhausted_clarify_fields_from_ve` returns `None` when any non-null-ish value is provided | ✓ Confirmed — `input_val is not None and input_val != "__NULL__" and input_val != ""` gate |
| 4 | Mixed user-only + non-user errors → still `ReferenceError` | ✓ Confirmed — both `_exhausted_clarify_fields` and `_exhausted_clarify_fields_from_ve` return `None` on mixed |
| 5 | ExhaustedClarify path does NOT call `/plans/evaluate` | ✓ Confirmed — `_route_plan_after` routes to `"respond"`; no `plan_draft` is produced; compiled-graph test asserts zero evaluate URLs |
| 6 | `reply_draft` is pre-set so respond node does not call LLM | ✓ Confirmed — `respond` (line 578–580) returns `reply_draft` directly |
| 7 | `clarification` is reset by ingest on next round | ✓ Confirmed for gateway graph (line 382–384); gap in non-gateway graph noted as P2-2 |
| 8 | `_route_plan_after` distinguishes `l1_exhausted` from L2-sourced clarification | ✓ Confirmed — `source == "l1_exhausted"` check; L2's source would be `"form_required"` or similar |
| 9 | `_route_understand_gw` does not misroute `l1_exhausted` to `clarify` node | ✓ Confirmed — in the same round, plan→_route_plan→respond bypasses understand; next round ingest resets clarification |
| 10 | Non-null invalid value (e.g. `amount_cents="abc"`) → still hard error | ✓ Confirmed — `_exhausted_clarify_fields_from_ve` returns `None` for non-null-ish input |
| 11 | ID prefix error (`from_account_id="crd_zs_debit"`) → `ReferenceError`, not `ExhaustedClarify` | ✓ Confirmed — regex match fails in `_exhausted_clarify_fields` |
| 12 | All 12 tests pass | ✓ Confirmed — `test_exhausted_clarify.py` 11 passed, `test_graph_compiled.py` 1 passed |

## Test Run Evidence

```
$ .venv/bin/python -m pytest tests/agent/test_exhausted_clarify.py -q
11 passed in 1.22s

$ .venv/bin/python -m pytest tests/agent/routing/test_graph_compiled.py::test_exhausted_clarify_routes_to_respond_no_evaluate_via_compiled_graph -q
1 passed in 0.76s
```