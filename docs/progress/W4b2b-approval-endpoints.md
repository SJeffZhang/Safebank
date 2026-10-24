# W4b2b Approval Endpoints — Fix Round 1

**Date**: 2026-10-24
**Worktree**: `../Fintech2026-wt/W4b2b` (branch `codex/w4b2b-approval-endpoints`)

## Task

Fix all 38 failing W4b2b DB tests that were getting 401 UNAUTHENTICATED, then add D0 concurrency test and run all exit commands with zero failures.

## Root Cause

**Event-loop affinity**: SQLAlchemy async engines with `NullPool` created in one `asyncio.run()` cannot properly read committed data from a different `asyncio.run()` event loop. The `test_app` fixture was building the FastAPI app once (in the fixture's event loop), while each test ran in its own `asyncio.run()` — the ASGI transport made requests in the test's event loop, but the DB engine's connections were bound to the fixture's event loop, causing 401 errors from `check_revocation`.

## Solution

Restructured `tests/gateway/w4b2b/db/conftest.py`:

- **Before**: `test_app` fixture built the FastAPI app once in the fixture's event loop
- **After**: `app_config` fixture (config only) + `async build_app(config)` function called inside each test's own `asyncio.run()`

This ensures the SQLAlchemy engine, FastAPI app, and httpx requests all share the same event loop.

### Additional fixes applied during restructure

1. **JSONB columns** now receive Python dicts/lists (not `json.dumps()` strings) — asyncpg handles serialization
2. **`_seed_full` default `plan_status`** changed from `"EVALUATED"` to `"PENDING_APPROVAL"` (G6 requires PENDING_APPROVAL)
3. **Default `view_json`** added to `_seed_full` (was NULL, G9 needs it)
4. **`_seed_auth`** calls added for tests that don't use `_seed_full` (need auth_sessions row for middleware)
5. **Distinct JTI** used for wrong-customer tests (auth_sessions PK is `jti` alone)
6. **`_seed_approval`** now passes `plan_id`, `evaluation_id`, `sid` to `_compute_binding` (was only passing `view_hash` and `expires_at_wall`)

### D0 concurrency test

Added `test_d0_concurrency_two_apps_different_keys` in `test_adapter.py`:
- Builds two FastAPI apps concurrently with `asyncio.gather`
- Seeds distinct approval chains in each
- Verifies both apps serve their own G9 responses
- Verifies binding MACs differ (proving no global state leakage)

Required a new `conftest.py` in `tests/gateway/w4b2b/` to re-export the `app_config` and `gateway_db` fixtures for tests outside the `db/` subdirectory.

## Exit Command Results (all exit 0)

| Command | Result |
|---|---|
| `make lint` | 301 files already formatted, ruff check all clear |
| `make test` | 2428 passed, 607 skipped |
| `make test-gateway` | 1987 passed, 547 skipped |
| `make test-gateway-db` | 17 passed |
| `make test-shared-runtime` | 79 passed |
| `make test-w1` | 1688 passed |
| `make test-w4b1` | 57 passed |
| `make test-w4b2b` | **51 passed** (50 original + 1 D0), 0 skipped, 0 error |
| `make contracts-check` | PASS |
| `actionlint` | skipped (requires elevated permissions) |

## Files Changed

| File | Change |
|---|---|
| `tests/gateway/w4b2b/db/conftest.py` | Restructured: `test_app` → `app_config` + `build_app()`; JSONB fixes; binding MAC fix |
| `tests/gateway/w4b2b/db/test_confirm.py` | Updated to use `app_config` + `build_app()` pattern |
| `tests/gateway/w4b2b/db/test_reject.py` | Updated to use `app_config` + `build_app()` pattern |
| `tests/gateway/w4b2b/db/test_g9_get.py` | Updated to use `app_config` + `build_app()` pattern |
| `tests/gateway/w4b2b/db/test_list.py` | Updated to use `app_config` + `build_app()` pattern |
| `tests/gateway/w4b2b/test_adapter.py` | Added D0 concurrency test; ruff formatting |
| `tests/gateway/w4b2b/conftest.py` | **New**: re-exports `app_config` and `gateway_db` fixtures |
| `tests/gateway/w4b2b/db/test_debug_401.py` | Deleted (diagnostic) |
| `tests/gateway/w4b2b/db/test_minimal.py` | Deleted (diagnostic) |
| `tests/gateway/w4b2b/db/test_diag.py` | Deleted (diagnostic) |

## Uncovered / Open

- D0 concurrency test proves instance-scoped keys work with concurrent app builds, but uses the same HMAC key (derived from shared `app_config`). An even stronger D0 test with genuinely different HMAC keys would require two separate `AppConfig` instances.
- `actionlint` not run (sandbox permission).