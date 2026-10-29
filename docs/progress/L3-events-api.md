# L3 events API — progress

Branch: `feat/l3-events-api` (worktree `L3-events-api`, base `feat/l3-events-pure`).
Brief: `GET /internal/events/{id}`, `POST /internal/events/{id}/execute`,
`POST /internal/events/{id}/block` in `services/bank-backend` + pure block
functions in bankcore + real-PG tests.

## Done

- [x] bankcore pure functions `block_instruction` / `block_billing`
  (`packages/bankcore/src/safebank_bankcore/events.py`). Terminal
  non-success run recording, period consumed exactly once, no IO/clock/auth.
  Committed `78af514` (113 insertions).

## Pending

- [ ] `services/bank-backend/app/events.py` — `EventService` with
  `get_event` / `execute_event` / `block_event` (customer derivation, payload
  hash check, customer advisory lock → clock FOR UPDATE → state_versions
  FOR UPDATE → load_bank_state → apply bankcore event → persist effects →
  insert instruction_runs row → insert terminal event_executions row → mark
  job DONE, following `app/execution.py` lock order).
- [ ] Wire three routes inline in `app/main.py` `create_app` (mirror existing
  `/internal/*` service-token auth).
- [ ] `tests/obligations_data/test_events_api.py` — 6 real-PG tests.
- [ ] Gates: `make lint`, `make test`, `make test-obligations`,
  `make contracts-check`.

## Gates

Not run yet.
