# L3 Events Pure — Independent Review

Scope: `origin/main..HEAD` (events.py, test_events.py, docs/progress/L3-events-pure.md).
Read-only review; no code edits. Reviewed `apply_scheduled_transfer` / `apply_merchant_charge`
against banking.py, ledger.py, reserve.py and the obligation contracts.

## Verdict
REQUEST_CHANGES

## Findings (P0, P1, P2)

### P0
None.

### P1
1. **External scheduled transfers bypass the transfer controls `apply_transfer` enforces**
   (`events.py`, `apply_scheduled_transfer`). For `target.ownership == "EXTERNAL"` the function
   builds a `LedgerTransfer` and calls `post_transfer` directly, crediting `acc_external_clearing`.
   It never applies the rules that user-initiated external transfers go through in
   `banking.py::apply_transfer` (lines 78–137):
   - card state check — `source.current_card_id` must be ACTIVE, else `CARD_STATE_CONFLICT`;
   - single / daily account-or-card limits — `CARD_SINGLE_LIMIT` / `CARD_DAILY_LIMIT`;
   - customer daily outflow limit — `DAILY_OUTFLOW_LIMIT`;
   - counter increments — `daily_outflow_cents`, `rolling_outflow_cents`, `daily_used_cents`.
   Consequences: (a) freezing a card does **not** stop an already-scheduled external transfer from
   the same account (freeze-to-stop silently fails); (b) system-driven external outflows are never
   counted into `daily/rolling_outflow_cents`, so L2's 30-day outflow analytics (R11) and
   `RISKY_SEQUENCE` under-count them; (c) the per-day outflow limit is bypassed. The progress file
   documents the clearing-account credit choice but does not mention these limits/counters, so this
   is not an acknowledged deferral.

### P2
1. **Non-ACTIVE block branch does not advance `next_run_at`** (`events.py`). The
   `inst.status != "ACTIVE"` path records a `BLOCKED` run and returns, but never updates the
   instruction, contradicting the module docstring ("next run is advanced regardless of success")
   and progress item 7 ("advance happens once regardless of outcome"). Idempotency still holds via
   the run record, but a DISABLED instruction later re-activated with a stale `next_run_at` would
   be permanently "due" (each pass no-ops on `_run_exists` and never advances). Low likelihood if
   re-activation sets a fresh `next_run_at`, but the stated invariant is false for this branch.
2. **Merchant billing successor job asymmetry** (progress item 4). No `ScheduleEventJob` is emitted
   for the next merchant period because the frozen `ScheduleEventJob.kind` literal
   (`SCHEDULED_TRANSFER` | `MERCHANT_MANDATE`) does not allow the `MERCHANT_CHARGE` value the
   persist/CHECK layer expects. Documented as a coordinator gap, but merchant scheduling then relies
   solely on `billing.next_due_at` while scheduled transfers rely on `event_jobs` — resolve before
   wiring the merchant scheduler.

## Checks

- **Test command**: `.venv/bin/pytest tests/bankcore/test_events.py -q` → **9 passed** (0.12s).
- **No IO**: events.py imports only `hashlib`, `datetime`, `safebank_contracts`, and `.ids`/`.ledger`/
  `.reserve`. No DB, HTTP, wall clock, or filesystem. PASS.
- **Int cents only**: amounts are `PositiveCents` (strict int); `test_cents_are_strict_ints` asserts
  `type(...) is int` on run, transfer, and account fields. PASS.
- **Effects reuse existing types**: `PostTransfer`/`AppendTransaction` (via `post_transfer`),
  `ScheduleEventJob`, `SetInstructionStatus` — all pre-existing in `contracts.state`; diff adds no
  effect/schema types (ADR-011 additive). PASS.
- **Idempotency by period**: `_run_exists` matches `(instruction_id|billing_id, due_at)` against
  `snap.runs`; replay returns `(state, (), None)` with no ledger/runs growth
  (`test_replay_same_period_is_idempotent`). PASS.
- **Period advance once**: success and insufficient-funds paths advance `next_run_at`/`next_due_at`
  exactly once; merchant billing advances independently of mandate lifecycle. PASS except the
  non-ACTIVE branch (P2-1).
- **Insufficient → FAILED_INSUFFICIENT, no money moved**: `post_transfer` returns unchanged state with
  `INSUFFICIENT_FUNDS`; `_status_for` maps it to `FAILED_INSUFFICIENT`; test asserts accounts/
  transfers/transactions unchanged and `transfer_id is None`. PASS.
- **Revoked mandate/instruction → BLOCKED**: DELETED instruction → `BLOCKED`/`SCHEDULED_BYPASS`;
  non-ACTIVE mandate → `BLOCKED`/`MANDATE_NOT_ACTIVE`. PASS.
- **Charge above mandate max → BLOCKED**: `billing.amount_cents > mandate.max_amount_cents` →
  `BLOCKED`/`MANDATE_AMOUNT_EXCEEDED`, billing still advances. PASS.
- **06:00 execution / month-end clamp**: `_business_time` normalizes to Asia/Shanghai; `_next_month`
  clamps day via `monthrange`; `test_monthly_transfer_executes_and_clamps_next_run` asserts Jan 31 →
  Feb 28 06:00. PASS.
- **No catch-up**: each call executes exactly one `due_at`; no recurrence loop. PASS.
- **Ledger conservation**: `test_conservation` asserts Σcredits == Σdebits. PASS.
- **Consistency with banking.py transfer rules (limits, card state, daily outflow) for scheduled
  external transfers**: FAIL — see P1-1.
