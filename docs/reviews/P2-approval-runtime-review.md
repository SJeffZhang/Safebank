# P2 Approval Runtime Review: 00aca15..cb60354

**Date**: 2026-10-27  
**Scope**: `00aca15` (W4c merged, no instance keys) → `cb60354` (instance keys r2)  
**Files**: `approval/service.py`, `api/plans.py`, `api/execution.py`, `test_g4_instance_keys.py`

---

## Summary

**APPROVE** — no P0, no P1. Two P2 observations neither blocking.

---

## Checklist

### ① Production callers pass instance config (✅ PASS)

Two production callers, both updated:

- **`plans.py:379`** (`evaluate_plan`): passes `key=request.app.state.approval_hmac_key`, `profile=settings.profile`, `wall_now=wall_now` — correct.
- **`execution.py:590`** (`_handle_state_changed`): passes `key=request.app.state.approval_hmac_key`, `profile=request.app.state.settings.profile`, `wall_now=request.app.state.wall_now` — correct.

Grep confirms no other production `create_for_evaluation` or `_require_config` callers. `approvals.py:159,326` are lock-order comments, not calls.

**Missing-key-when-global-valid case**: `test_instance_missing_key_returns_503` sets `_config = None` (global cleared) **and** `app.state.approval_hmac_key = None` — coverage of "global valid but instance key missing" is *implicit*: if the global is valid but instance key is `None`, the call still only gets `getattr(state, "approval_hmac_key", None) → None`, so all three args are `None`, falling through to `_require_config()` which finds `_config` set and returns 200. This is the legacy-compat path, not a bug — the docstring says "When omitted the legacy `_require_config()` path is used". The current test only covers the "both global and instance missing" case. Adding a "global valid + instance key missing → uses global compat path, 200" test would make the contract explicit (P2, not blocking).

### ② Service validation returns 503 not 500 (✅ PASS)

All five validation paths in `service.py:333-356` raise `GatewayError(503)`:

| Condition | Line | Code |
|---|---|---|
| Non-bytes key | 336-338 | `DEPENDENCY_UNAVAILABLE` |
| Short key (<32B) | 336-338 | `DEPENDENCY_UNAVAILABLE` |
| Invalid profile | 340-341 | `DEPENDENCY_UNAVAILABLE` |
| Non-callable wall_now | 342-343 | `DEPENDENCY_UNAVAILABLE` |
| Naive or tz-less datetime | 344-350 | `DEPENDENCY_UNAVAILABLE` |
| Partial args (mixed None/not-None) | 352-356 | `DEPENDENCY_UNAVAILABLE` |

No 500 anywhere. Tests cover: missing key (`test_instance_missing_key`), short key (`test_instance_short_key`), non-bytes key (`test_instance_non_bytes_key`). Invalid profile, non-callable wall_now, naive wall_now, and partial args are **unit-coverable but not integration-tested** — but these can only occur via programming error (the production callers always pass `bytes | None`, `str | None`, `Callable | None` from app state), so this is acceptable.

### ③ HMAC format / TTL / lock order / state machine unchanged (✅ PASS)

The diff only touches the config-resolution code (lines 307-359). Below line 360, `create_for_evaluation` is **byte-identical** to the parent commit:

- `plan_id`, `customer_id`, `sid`, `evaluation_id` extraction unchanged
- ALLOW early-return unchanged (line 367)
- REQUIRE_APPROVAL check unchanged (line 368)
- View identity checks unchanged (lines 373-381)
- `binding_mac` computation unchanged
- `expires_at_wall` computation unchanged
- `view_json` finalisation unchanged
- INSERT / UPDATE SQL unchanged

No HMAC message format, TTL, lock order, or state machine changes.

### ④ Global config restored after tests (⚠️ P2)

`test_reset_global_configure` (line 458) sets `_config = None`. This test runs at some point during the session — but pytest collects tests alphabetically by function name, so `test_reset_*` runs **between** `test_instance_*` and `test_two_*`, not last. However, every other test in the file **explicitly sets `_config = None` in their own `run()` body** (lines 203, 248, 372, 404, 436), so they are self-contained. Other test modules that call `configure()` in conftest fixtures already call it at fixture-setup time, so a stale `None` from this module won't leak — the fixture re-configures it.

The ordering is fragile but not broken. P2: consider `autouse=True` fixture with `yield` for cleanup instead of a standalone test.

### ⑤ Dual-app cross-binding verification (✅ PASS)

`test_two_apps_different_keys_no_cross_interference`:
- Builds two independent ASGI apps via `build_app(app_config)`, each with its own `_runtime_db()` (separate `NullPool` engines sharing the same database).
- Sets different keys on each app's state (`b"\x01"*32` vs `b"\x02"*32`).
- Both apps create approvals successfully (200 REQUIRE_APPROVAL).
- Loads bindings from real DB rows (approvals + evaluations + plans) via `_verify_binding_from_db`.
- `verify_binding(key1, binding1, mac1)` → True
- `verify_binding(key2, binding2, mac2)` → True
- `verify_binding(key2, binding1, mac1)` → False (cross-verification fails correctly)
- `verify_binding(key1, binding2, mac2)` → False
- `mac1 != mac2` (different keys produce different MACs).

Real PG rows, real MAC verification, cross-isolation. ✅

### ⑥ No other production callers missed (✅ PASS)

```
$ grep -rn 'create_for_evaluation\|_require_config' services/gateway/app/
```

Production callers: only `plans.py:379` and `execution.py:590`. Comments in `approvals.py` reference the function name in lock-order documentation (not calls). The README is documentation.

---

## Verdicts

| Level | Count | Details |
|---|---|---|
| P0 | 0 | — |
| P1 | 0 | — |
| P2 | 2 | ① Missing test for "global valid + instance key absent → legacy compat 200" (explicit contract) ② `test_reset_global_configure` standalone-test cleanup pattern is fragile; `autouse` fixture would be more robust |

Neither P2 changes correctness or security. This component is ready to proceed.

---

## Testing verification

The coordinator reports 7 passed, and that reverting the three production files to 00aca15 causes 3 failures. This is consistent with the test structure:

- `test_g4_without_global_configure_uses_instance_keys` — needs instance config path
- `test_two_apps_different_keys_no_cross_interference` — needs instance keys
- `test_instance_missing_key_returns_503` — needs instance missing → 503
- `test_instance_short_key_returns_503` — needs key validation
- `test_instance_non_bytes_key_returns_503` — needs bytes check
- `test_g4_bad_global_configure_does_not_affect_instance_keyed_create` — needs stale-global bypass
- `test_reset_global_configure` — always passes

(Reviewer did not run pytest; the coordinator's test run was not re-executed.)