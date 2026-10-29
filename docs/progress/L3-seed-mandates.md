# L3-seed-mandates

## Task

Fix the live gap where 张三 (cus_zs) has 4 merchant billings in the demo seed but no
MERCHANT_MANDATE standing instructions, so subscription views (`list_subscriptions`,
`upcoming_charges`, `cancel_subscription`) derive nothing. Add one mandate per existing
billing, plus 2 billings + 2 mandates for 李四 (cus_ls), and make `seed_demo` backfill
already-installed customers idempotently.

## Changes

- `services/bank-backend/fixtures/l1_seed_v1.json`
  - 张三 mandates: `si_zs_video` (blg_video, max 3600), `si_zs_music` (1800),
    `si_zs_cloud` (2520), `si_zs_gym` (23880), all from `acc_zs_chk`.
  - 李四 billings: `blg_ls_video` (mer_video, 3000, day 20), `blg_ls_music`
    (mer_music, 1500, day 22) on `acc_ls_chk`; mandates `si_ls_video` (3600),
    `si_ls_music` (1800).
- `services/bank-backend/app/seedlib.py`
  - Validates/prepares MERCHANT_MANDATE instructions: kind/amount_type DUES/
    recurrence AS_PER_DUES, merchant_id + billing_id cross-checked against billings
    (merchant, customer, from-account all match), max_amount_cents 1..100000000,
    rejects payee_id/to_account_id/amount_cents/period/day_of_month/weekday/next_run_at,
    and sets time_of_day NULL (required by `standing_instructions_kind_combo_check`).
- `services/bank-backend/app/seed_demo.py`
  - Backfills skipped (already-installed) customers' missing billings and
    MERCHANT_MANDATE instructions idempotently by id via `ON CONFLICT DO NOTHING`;
    reports `backfilled_billings` / `backfilled_mandates`. Accounts, transfers and
    transactions are never touched.
- `tests/evaluation/corpus/spec.py`
  - `validate_seed` now checks merchant_id/billing_id for MERCHANT_MANDATE
    instructions (instead of demanding a payee_id).

## Tests

Red tests written first (commit `b1cffd1`): fresh install → 4 ACTIVE subscriptions for
张三, `total_annual_cents == 318000`, `upcoming_charges` 7 days == 4500; backfill →
`backfilled_mandates == 6`, `backfilled_billings == 2`, ledger/transactions unchanged.

## Gate results (real commands, this worktree)

- `make lint` → All checks passed; 424 files already formatted.
- `make test` → **3319 passed, 900 skipped**.
- `make test-obligations` → **152 passed** (1 pre-existing SAWarning re table sort cycle).
- `make test-e0b` → **119 passed**.
- `make contracts-check` → **PASS: contracts v1 match frozen schemas**.

## Not covered / notes

- `time_of_day` for MERCHANT_MANDATE is NULL by schema (`SI_MANDATE_CHECK`); the
  billing's own `day_of_month` + BILLING_TIME_OF_DAY drives `next_due_at`, not the
  instruction.
- Backfill only touches `merchant_billing` and `standing_instructions`; the
  `standing_instructions_billing_owner_fkey` composite FK means a mandate insert
  requires its billing row to already exist (hence billings backfilled first).
