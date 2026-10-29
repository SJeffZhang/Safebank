# L3-events-pure — pure system-event execution

Task: implement pure bankcore functions for system events
(`apply_scheduled_transfer`, `apply_merchant_charge`) in
`packages/bankcore/src/safebank_bankcore/events.py`. No write tools, no IO —
L3 scheduler events consuming an immutable payload (id + due_at).

## Result

- New file `packages/bankcore/src/safebank_bankcore/events.py` (338 lines).
- New red tests `tests/bankcore/test_events.py` (9 tests, commit `61b4735`).
- Implementation commits `9671b01` (feat) + `402116e` (style wrap).

## Design decisions (recorded per task brief)

1. **Return convention** `(new_state, effects, run)` where `run` is the
   terminal `InstructionRun` or `None` on idempotent replay. Matches the
   `(state, effects, violations)` applier style, with the run record as an
   extra tuple element for later persist wiring.
2. **No new effect type / schema change.** Money movement reuses
   `post_transfer` (emits `PostTransfer`/`AppendTransaction`); the run record
   and `next_run_at`/`next_due_at` advance live in the returned
   `obligations` snapshot. `make contracts-check` stays green (additive,
   ADR-011 — coordinator regenerates the frozen snapshot at merge if needed).
3. **Scheduled transfer successor** emits a `ScheduleEventJob`
   (`kind="SCHEDULED_TRANSFER"`) for the next period; ONCE/`end_at`-bounded
   instructions terminate via `SetInstructionStatus(ACTIVE → DELETED)` and no
   successor job.
4. **Merchant billing successor emits no `ScheduleEventJob`.** Frozen
   `ScheduleEventJob.kind` literal allows `MERCHANT_MANDATE` but the persist
   layer and `event_jobs` DB CHECK expect `MERCHANT_CHARGE`
   (`EVENT_KIND_RANK` also uses `MERCHANT_CHARGE`). Emitting `MERCHANT_MANDATE`
   would raise in persist(). The billing's `next_due_at` advance in state is
   the authoritative source of the next period. **Gap for coordinator**: align
   the frozen literal with the persist/CHECK value before wiring the merchant
   scheduler.
5. **Block vs fail mapping**: DELETED scheduled instruction → `BLOCKED` /
   `SCHEDULED_BYPASS`; missing/inactive mandate → `BLOCKED` /
   `MANDATE_NOT_ACTIVE`; amount > `max_amount_cents` → `BLOCKED` /
   `MANDATE_AMOUNT_EXCEEDED`; insufficient funds → `FAILED_INSUFFICIENT` /
   `INSUFFICIENT_FUNDS`; any other `post_transfer` error → `BLOCKED` with that
   code.
6. **Idempotency key** is `(instruction_id|billing_id, due_at)` via
   `snap.runs`; replay returns `(state, (), None)`.
7. **Period advance happens once regardless of outcome** (success, block, or
   insufficient): ONCE instructions are consumed even on failure; merchant
   billings advance independently of the mandate lifecycle.
8. **External target** credits `acc_external_clearing`; OWN/OTHER credits the
   resolved `InstructionTarget.target_account_id` directly.

## Test results (actual)

- `pytest tests/bankcore/test_events.py -q` → **9 passed**.
- `make lint` → 431 files already formatted (ruff check + format clean).
- `make test` → **3379 passed, 903 skipped**.
- `make contracts-check` → **PASS: contracts v1 match frozen schemas**.

## Not done / boundaries

- No persist wiring (runs still need to be written by the L3 scheduler);
  `run` is returned as the third tuple element for that.
- No scheduler/event-job producer; these functions only consume due payloads.
- Merchant `MERCHANT_MANDATE` vs `MERCHANT_CHARGE` kind mismatch (item 4) is
  left for the coordinator.

## Fix round 1

Independent review (`docs/reviews/L3-events-pure-review.md`) returned
REQUEST_CHANGES. Addressed:

- **P1-1 — external scheduled transfers now enforce the same transfer controls
  as `apply_transfer`.** `apply_scheduled_transfer` resolves the payee from
  `state.payees`, then runs `transfer_control_violation` (card state
  `CARD_STATE_CONFLICT`, card/account single-daily `CARD_SINGLE_LIMIT` /
  `CARD_DAILY_LIMIT`, customer `DAILY_OUTFLOW_LIMIT`) before posting; on
  external success it calls `bump_outflow_counters` so scheduled outflows feed
  `daily_outflow_cents` / `rolling_outflow_cents` / `daily_used_cents`. Hard
  violations at due time are `BLOCKED` with no money moved; insufficient funds
  still map to `FAILED_INSUFFICIENT`. Credit resolution now reuses the payee's
  `target_account_id` (or the clearing account) instead of the derived
  `InstructionTarget`, matching banking.py.
- **P2-1 — non-ACTIVE block branch advances `next_run_at`.** The DISABLED/
  DELETED branch now consumes the stale period (advances `next_run_at` via
  `_next_scheduled_due`), so re-activation can't leave an instruction
  permanently due.
- **P2-2 — merchant `MERCHANT_MANDATE` vs `MERCHANT_CHARGE` kind mismatch.**
  Not fixable here (frozen `ScheduleEventJob.kind` literal vs persist/CHECK
  value); remains a documented coordinator gap (item 4).

Red tests first (commit `f444602`), then the rewrite. `_finish_scheduled`
extracts the shared "advance once + terminal run" path for ACTIVE
instructions.

Actual results after fix:

- `.venv/bin/pytest tests/bankcore/test_events.py -q` → **13 passed**.
- `.venv/bin/pytest tests/bankcore -q` → **394 passed**.
- `make lint` → all checks passed / 431 files already formatted.
- `make test` → **3383 passed, 903 skipped**.
- `make contracts-check` → **PASS: contracts v1 match frozen schemas**.
