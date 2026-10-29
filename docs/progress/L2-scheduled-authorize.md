# L2-scheduled-authorize (S1 endpoint) — progress

**Branch**: `feat/l2-scheduled-authorize` (from main 0b9684e). Worktree GW-s1.

## Status (session r2)
- Handler step 1+2 implemented in `services/gateway/app/internal/scheduled.py`:
  - Step 1 terminal replay: stored `event_decisions` row returned without touching L3.
  - Step 2 event fetch: any `L3Error` → 200 `RETRYING DEPENDENCY_UNAVAILABLE`, nothing persisted.
  - Steps 3–5 (grant/backfill, re-eval, block/execute, persist) still stubbed (503) — next.
- `make test-s1` → **2 passed** (terminal replay + RETRYING-on-unavailable both green).
- Committed `f1fb918` (docs checkpoint) preceded this; handler commit is the next commit.

## Next steps for resumer
1. Steps 3–5: grant lookup by `event.source_id == scheduled_grants.instruction_id`;
   missing → backfill from L3 committed execution of `source_plan_id`; L3 unreachable →
   200 RETRYING (nothing written); REVOKED / scope_hash mismatch → BLOCKED via `l3.event_block`.
2. Re-evaluation: fresh `transfer` `PlanDraft` from event def, `origin="app_form"`,
   `authorization=None`, `profile="B2"`, `pipeline.evaluate(ctx, stages=(contract, entity,
   snapshot, preflight, workflow, decision), mode=EvalMode.EXECUTE_REPLAY)`.
3. Decision mapping: risk > approved_risk → SKIPPED_REAPPROVAL; INSUFFICIENT_FUNDS finding
   (REPLAN) → FAILED_INSUFFICIENT; DENY/REPLAN → BLOCKED; else `l3.event_execute` with
   `authorization_evidence={type: SCHEDULED_GRANT, ...}`; persist event_decisions + audit
   (HTTP outside DB transactions).
4. Remaining tests: revoked→BLOCKED; missing-grant backfill; risk increase→SKIPPED_REAPPROVAL;
   insufficient→FAILED_INSUFFICIENT; happy path SUCCESS executes once; wrong token 401/403;
   same-event replays never execute twice.
5. Add CI line `.github/workflows/checks.yml` p1-postgres for `make test-s1`.
6. Gates: `make lint`, `make test`, `make test-gateway`, `make test-gateway-db` (tail -1).

## Key facts (verified)
- `tables.event_decisions`: event_id PK, status String(32), code String(64), decided_at_wall.
- `tables.scheduled_grants`: instruction_id PK, customer_id, source_plan_id, scope_json,
  scope_hash(64), approved_risk GREEN/YELLOW/RED, status ACTIVE/REVOKED, created_at, revoked_at.
- `L3Client.event/event_execute/event_block` exist (client.py:152-171); FakeL3 has the three
  event endpoints and `_gate` → 503 for "unavailable_503".
- Caller: `request.state.caller` (S → `ServiceKind.S`, no JWT, enforced by auth matrix).
- `app.state.db/.l3/.wall_now` set by `runtime.install(app)`; success returns a plain dict
  (no envelope); errors via `GatewayError` → `{error:{...}}` (middleware).
- `entity.py` calls `load_records(conn, session_id, customer_id)`; `ctx.origin=="app_form"`
  avoids ENTITY_UNSEEN; `plan_draft is None` short-circuits the entity stage.
