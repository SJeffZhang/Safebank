# L3 Seed Mandates — Independent Review

Scope: `git diff origin/main..HEAD` — demo seed adds `MERCHANT_MANDATE` standing instructions and backfills them for already-installed customers.

## Verdict

**APPROVE** — no P0/P1. Two P2 test-coverage notes (max_amount_cents / DUES / AS_PER_DUES not directly asserted by any test).

## Findings (P0, P1, P2)

**P0** — none.

**P1** — none.

**P2**
1. `tests/obligations_data/test_seed_demo.py::test_seed_demo_installs_merchant_mandates` asserts the subscription *totals* (`total_annual_cents == 318000`, 7-day upcoming `4500`) and the billing/account id sets, but never asserts the mandate protection caps `max_amount_cents` (3600/1800/2520/23880) nor that `amount_type='DUES'` / `recurrence='AS_PER_DUES'` are persisted. The DB `SI_MANDATE_CHECK` would reject a wrong kind/amount_type/recurrence combo, so the shape is enforced; only the exact cap values are unpinned by tests.
2. Same test checks 李四's two subscriptions only by `billing_id`/`account_id` set — no assertion of their max caps or annual total. Minor coverage gap.

## Checks

- **[1] Values & schema constraints (models.py `SI_MANDATE_CHECK`)** — PASS.
  - Fixture billings (`services/bank-backend/fixtures/l1_seed_v1.json:397-446`): 张三 `blg_video` 3000/day18, `blg_music` 1500/day20, `blg_cloud` 2100/day1, `blg_gym` 19900/day25, all `acc_zs_chk`; 李四 `blg_ls_video` 3000/day20, `blg_ls_music` 1500/day22, both `acc_ls_chk`. Matches expected data (30.00/15.00/21.00/199.00; 30.00/15.00).
  - Mandate `max_amount_cents`: `si_zs_video` 3600, `si_zs_music` 1800, `si_zs_cloud` 2520, `si_zs_gym` 23880, `si_ls_video` 3600, `si_ls_music` 1800. Matches expected caps.
  - `SI_MANDATE_CHECK` (`services/bank-backend/app/models.py:372-379`) requires `kind='MERCHANT_MANDATE' AND amount_type='DUES' AND recurrence='AS_PER_DUES' AND payee_id IS NULL AND to_account_id IS NULL AND merchant_id IS NOT NULL AND billing_id IS NOT NULL AND amount_cents IS NULL AND max_amount_cents 1..100000000 AND period/anchor_day/weekday/time_of_day/next_run_at IS NULL`. The prepared dict (`services/bank-backend/app/seedlib.py`, MERCHANT_MANDATE branch) sets exactly: `status='ACTIVE'`, `amount_type='DUES'`, `payee_id=None`, `to_account_id=None`, `amount_cents=None`, `recurrence='AS_PER_DUES'`, `period/anchor_day/weekday/time_of_day/next_run_at=None`, `priority='HIGH'`, `grant_scope_hash='0'*64`, plus validated `max_amount_cents` (seedlib raises unless `1..100000000`). All CHECK clauses satisfied.
  - Time 06:00 Asia/Shanghai: `BILLING_TIME_OF_DAY = time(6, 0)` and `SHANGHAI = ZoneInfo("Asia/Shanghai")` (`seedlib.py:35-36`); billings set `time_of_day=BILLING_TIME_OF_DAY` (`seedlib.py:463`). Matches.

- **[2] Backfill idempotency & scope (never touches accounts/transfers/transactions)** — PASS.
  - Backfill block (`services/bank-backend/app/seed_demo.py:176-196`) iterates `prepared["billings"]` and `prepared["standing_instructions"]` restricted to `skipped_customer_ids` (already-installed demo customers), inserting via `pg_insert(...).on_conflict_do_nothing()`; mandates additionally filtered to `kind == "MERCHANT_MANDATE"`.
  - Idempotent by id: `merchant_billing.id` and `standing_instructions.id` are primary keys (`models.py:309`, `idcol(primary_key=True)`), so `ON CONFLICT DO NOTHING` no-ops on a duplicate id. The codebase already uses the identical pattern for global merchants/products (`seed_demo.py:129-140`).
  - Scope: only `merchant_billing` and `standing_instructions` are written in the backfill; no account/transfer/transaction write. `test_seed_demo_backfills_mandates_for_existing_customers` (`tests/obligations_data/test_seed_demo.py:460-536`) verifies account balance sums, `transactions` count and `transfers` count are byte-identical before/after backfill, and asserts `backfilled_mandates == 6` / `backfilled_billings == 2`.
  - Ordering: billings inserted before mandates (same transaction), so the `standing_instructions_billing_owner_fkey` (`models.py:443-452`) is satisfied when a customer is missing both (李四 case).
  - Ownership consistency: `seedlib.py` MERCHANT_MANDATE branch validates `billing["customer_id"] == customer_id`, `billing["account_id"] == from_account_id`, `billing["merchant_id"] == merchant_id` (raises `SeedError` otherwise), and the DB FK `standing_instructions_billing_owner_fkey` re-enforces `(billing_id, customer_id, from_account_id, merchant_id)` against `merchant_billing`.

- **[3] E0 loader / corpus path consistent** — PASS.
  - `tests/evaluation/corpus/spec.py:validate_seed` now branches: `kind == "MERCHANT_MANDATE"` validates `merchant_id`/`billing_id` membership; else falls back to `payee_id` membership. Existing `SCHEDULED_TRANSFER` entries carry no `kind` key (`instruction.get("kind")` → `None`) so they continue through the payee branch unchanged. New mandates have valid `merchant_id`/`billing_id` per the fixture, so the seed passes validation.
  - `install_demo` and the E0 loader share the same `prepare_seed(..., include_obligations=True)` / `read_seed` path (`seed_demo.py:53-54`), so the prepared rows are identical.

- **[4] Tests assert real values** — PASS (with P2 notes above).
  - `test_seed_demo_installs_merchant_mandates` (`tests/obligations_data/test_seed_demo.py:396-457`): 张三 4 ACTIVE subscriptions, `total_annual_cents == 318000` (26500×12), billing ids `{blg_video, blg_music, blg_cloud, blg_gym}`, single account `acc_zs_chk`; 7-day upcoming `charges_total_cents == 4500` with `{blg_video, blg_music}`; 李四 2 subscriptions `{blg_ls_video, blg_ls_music}` on `acc_ls_chk`. These are real, non-placeholder values.
  - `test_seed_demo_backfills_mandates_for_existing_customers` asserts the exact 6 mandate ids and 李四's 2 billing ids after re-run.

## Check summary

| # | Check | Result |
|---|-------|--------|
| 1 | Values & `SI_MANDATE_CHECK` | PASS |
| 2 | Backfill idempotency / scope / ownership | PASS |
| 3 | E0 loader / corpus consistency | PASS |
| 4 | Tests assert real values | PASS (2×P2 coverage notes) |
