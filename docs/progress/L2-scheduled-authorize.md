# L2-scheduled-authorize (S1 endpoint) — progress

**Branch**: `feat/l2-scheduled-authorize` (from main 0b9684e). Worktree GW-s1.

## Status (session r3)
- Handler steps 1–5 fully implemented in `services/gateway/app/internal/scheduled.py`
  (committed `4b9d2c9`):
  - Step 1 terminal replay: stored `event_decisions` row returned without touching L3.
  - Step 2 event fetch: any `L3Error` → 200 `RETRYING DEPENDENCY_UNAVAILABLE`, nothing persisted.
  - Step 3 grant lookup by `instruction_id == event.source_id`; missing → backfill from
    `l3.execution(customer_id, source_plan_id)` (COMMITTED required, `approved_risk` read
    from the execution result with fail-closed YELLOW default); L3 unreachable → RETRYING;
    REVOKED / `scope_hash != event.grant_scope_hash` → BLOCKED via `l3.event_block`.
  - Step 4 re-evaluation: fresh single-step `transfer` `PlanDraft` (origin="app_form",
    profile="B2", `EvalMode.EXECUTE_REPLAY`, stages contract/entity/snapshot/preflight/
    workflow/decision; provenance/intent excluded).
  - Step 5 decision mapping: risk > approved → SKIPPED_REAPPROVAL; INSUFFICIENT_FUNDS →
    FAILED_INSUFFICIENT; DENY/REPLAN → BLOCKED; else `l3.event_execute` with
    `ScheduledEvidence(SCHEDULED_GRANT, ...)`; event_decisions + audit persisted in a
    write transaction **after** the HTTP call.
- `make test-s1` → **2 passed** (existing terminal-replay + RETRYING tests; new code
  compiles/imports cleanly).

## Remaining for resumer (unchanged from brief)
1. Write the 7 new tests in `tests/gateway/s1/test_scheduled_authorize.py`:
   revoked→BLOCKED; missing-grant backfill; risk increase→SKIPPED_REAPPROVAL;
   insufficient→FAILED_INSUFFICIENT; happy path SUCCESS executes once; wrong token
   401/403; same-event replays never execute twice. **Open questions to verify while
   writing tests:** FakeL3 `_minimal_bank_state()` account/payee IDs owned by `cus_a`
   (happy-path transfer target must be owned), and FakeL3's `execution` endpoint result
   shape for seeding `approved_risk` in the backfill test.
2. Add CI line `.github/workflows/checks.yml` p1-postgres for `make test-s1`.
3. Gates: `make lint`, `make test`, `make test-gateway`, `make test-gateway-db` (tail -1).

## Key facts (verified)
- `tables.event_decisions`: event_id PK, status String(32), code String(64), decided_at_wall.
- `tables.scheduled_grants`: instruction_id PK, customer_id, source_plan_id, scope_json,
  scope_hash(64), approved_risk GREEN/YELLOW/RED, status ACTIVE/REVOKED, created_at, revoked_at.
- `L3Client.event/event_execute/event_block/execution` exist (client.py:123-171); FakeL3 has
  the three event endpoints and `_gate` → 503 for "unavailable_503".
- Caller: `request.state.caller` (S → `ServiceKind.S`, no JWT, enforced by auth matrix).
- `app.state.db/.l3/.wall_now` set by `runtime.install(app)`; success returns a plain dict
  (no envelope); errors via `GatewayError` → `{error:{...}}` (middleware).
- `entity.py` calls `load_records(conn, session_id, customer_id)`; `ctx.origin=="app_form"`
  avoids ENTITY_UNSEEN; `plan_draft is None` short-circuits the entity stage.
