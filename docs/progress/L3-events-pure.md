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
