# L2-scheduled-authorize (S1 endpoint) — progress

**Branch**: `feat/l2-scheduled-authorize` (from main 0b9684e). Worktree GW-s1.
**Status**: exploration + design complete; NO code/tests written this session (tool budget exhausted at 98/100). This file is the checkpoint.

## What the task is
Implement gateway `POST /internal/scheduled/authorize` (stub at `services/gateway/app/internal/scheduled.py`,
registered on internal bank-net app :8201, auth `SCHEDULER_SERVICE_TOKEN` → caller kind S).

## Flow to implement (exact)
1. `event_decisions` terminal row for `event_id` → return stored result, no re-eval.
2. GET L3 event definition (never trust callback amounts).
3. Load grant from `scheduled_grants` by `instruction_id == event.source_id`.
   - missing grant → look up L3 committed execution of `source_plan_id` + gateway evaluations to backfill idempotently.
   - L3 unreachable → 200 `RETRYING DEPENDENCY_UNAVAILABLE`, write nothing.
   - REVOKED or `scope_hash` mismatch → BLOCKED via /block.
4. Re-run normal evaluation stages on fresh snapshot for the event's steps:
   - reuse `pipeline.evaluate` mirroring `execution/replay.py::replay_with_context`.
   - **skip provenance+intent** (provenance re-verifies sensitive args against consumed utterances → would REPLAN).
     Construct a fresh `transfer` PlanDraft from the event definition.
     Stage tuple: `(contract, entity, snapshot, preflight, workflow, decision)`.
   - risk higher than `approved_risk` → SKIPPED_REAPPROVAL (SCHEDULED_REAPPROVAL_REQUIRED) via /block.
   - insufficient funds → FAILED_INSUFFICIENT via /block.
   - DENY/REPLAN → BLOCKED.
   - else POST /execute with expected versions, clock_version, payload hash, authorization_evidence.
5. Persist event_decisions terminal row + audit. All HTTP outside DB transactions.

## Key facts (verified this session)
- `tables.scheduled_grants` cols: instruction_id PK, customer_id, source_plan_id, scope_json, scope_hash(64),
  approved_risk (GREEN/YELLOW/RED), status (ACTIVE/REVOKED), created_at, revoked_at. **Never written anywhere in
  gateway code yet** — S1 must implement backfill.
- `tables.event_decisions` cols: event_id PK, status (SUCCESS/BLOCKED/FAILED_INSUFFICIENT/SKIPPED_REAPPROVAL),
  code(64), decided_at_wall.
- `pipeline.evaluate(ctx, *, stages=None, mode=None)`; `STAGES=(contract,entity,provenance,intent,snapshot,preflight,workflow,decision,audit)`.
- `EvalContext` required ctor args: request, identity(Caller), authorization(AuthorizationInputs), plan_draft(PlanDraft),
  plan_id, origin, customer_id, session_id, profile, wall_now, conn, l3, audit.
- `Decision` has: plan_id, evaluation_id, revision, decision(ALLOW/REQUIRE_APPROVAL/REPLAN/DENY), risk(GREEN/YELLOW/RED),
  approval_id, findings, replan_hints, preflight, versions, clock_version, policy_version.
- L3Client: `_request(method, path, json=...)`; errors L3NotDelivered / L3OutcomeUnknown / L3Unavailable /
  L3BusinessError(code, status, details). Needs new methods `event`, `event_execute`, `event_block`.
- FakeL3 (`services/gateway/app/l3/fake.py`): `_op_for(method, path)` prefix routing; `app()` nested FastAPI endpoints
  append to `self.calls`, `_require_token`, `_gate`, canned results. Needs event definition storage + 3 endpoints + routing.
- `app.state` has .settings/.db/.l3/.wall_now/... ; `new_id(prefix)` from `utterance.service`.
- Auth matrix already has `("POST","/internal/scheduled/authorize"): (S_ONLY,)`.

## Request/event wire shapes (chosen)
- S1 request: `{"event_id": str, "request_id": str?}`.
- L3 event def: `{event_id, kind, customer_id, account_id, payee_id|to_account_id, amount_cents,
  source_id(=instruction_id), source_plan_id, grant_scope_hash, payload_hash}`.
- /execute body: `{expected_versions, clock_version, expected_payload_hash,
  authorization_evidence: {type: SCHEDULED_GRANT, grant_id, scope_hash, source_plan_id, approved_risk}}`.
- /block body: `{status, code, expected_versions, clock_version, expected_payload_hash}`.

## Tests to write (tests/gateway/s1/test_scheduled_authorize.py)
terminal replay; missing-grant backfill; RETRYING-on-unavailable writes nothing; revoked→BLOCKED;
risk increase→SKIPPED_REAPPROVAL; insufficient→FAILED_INSUFFICIENT; happy path SUCCESS calls execute once;
wrong token 401/403; same-event replays never execute twice.

## Gates (not yet run)
`make lint`, `make test`, `make test-gateway`, `make test-gateway-db`; add `make test-s1` target + CI line in
`.github/workflows/checks.yml` p1-postgres.

## Next steps for resumer
1. Read PRD.md line 1495 (§9.5a S1 spec) to confirm request/response wire shapes before coding.
2. Write handler, L3Client methods, FakeL3 extensions, tests (red first), then gates.
