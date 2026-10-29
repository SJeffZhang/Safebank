# L3-seed-history — 张三 synthetic transaction history

Branch `feat/l3-seed-history` (built on the merchant-mandate seed). Single task, fresh session.
sim_now = `2026-10-15T09:00:00+08:00`. Authoritative data spec (6 points) and real-PG tests
(`tests/obligations_data/test_seed_history.py`) are fixed by the coordinator.

## Status

- [x] Red tests written and committed (`1e934f5`). `make test-obligations` shows
      `2 failed, 156 passed` — the two analytics tests are red (seed has no history yet),
      the balance/memo/ledger test is already green (balance assertion reads
      `credits_posted_cents - debits_posted_cents == 600000`, memo `txn_zs_013` verbatim,
      ledger conservation sums).
- [x] Production seed changes (`scripts/generate_seed_history.py` + regenerated
      `l1_seed_v1.json`, commit `3655de8`; lint/format fix `aa9099b`).
- [x] Gates green: `make lint`, `make test`, `make test-obligations`, `make test-e0b`,
      `make contracts-check` (tail -1 each).
- [x] Review REQUEST_CHANGES resolved (`docs/reviews/L3-seed-history-review.md`,
      commit `6ebc64b`): P1-1 (branch behind main) fixed by the coordinator merging
      origin/main; P1-2 fixed by moving the everyday spends to the spec window
      2026-07-17..2026-10-12 (commit `f09c37c`) + monthly-coverage test (`e37da24`);
      P2-1 fixed by listing the generator's actual categories in this doc.

## Design (validated against source, do not re-derive)

### Balance recomputation (CONFIRMED against seedlib.py 640-754, seed_demo.py 80-117)
- Add **only OUT transactions** totalling `N` cents on `acc_zs_chk`. Keep the single IN
  refund `txn_zs_013` (120000) unchanged.
- New account numbers: `credits_posted_cents = 1207950 + N`,
  `debits_posted_cents = 607950 + N`, `ledger_balance_cents = 600000` (unchanged).
- `install_demo` opening funding (`funding = credits_posted_cents - in_total_by_account`)
  auto-debits `acc_seed_equity` by `N`; INTERNAL_CLEARING credits increase by `N`
  (`out_delta`). Global conservation holds: total credits +2N, total debits +2N.
- Each new txn produces one transfer on INTERNAL_CLEARING (OUT = debit customer /
  credit clearing).

### Transaction shape (seedlib.py 640-731)
`id, account_id, occurred_at, direction, amount_cents, counterparty, kind (free-form str),
category (default "其他"), merchant_id, card_id, memo, balance_after_cents`.
`latest_by_account` tracks `(occurred_at, txn_id)` — the chronologically latest txn's
`balance_after_cents` must equal `ledger_balance_cents` (600000).

### Anomalies (analytics.py 190-324) — exactly 3, all card_id crd_zs_1
- 2026-09-07 02:13 夜色酒吧 128000 (NIGHT_LARGE, also NEW_MERCHANT_LARGE — merged to one item)
- 2026-09-15 美味外卖 two charges of 26800, 4 minutes apart (DUPLICATE flags the later one)
- 2026-09-22 星耀电子 349900 (NEW_MERCHANT_LARGE, first-seen, >100000)
All kind=CARD_SPEND. Categories: 夜色酒吧/美味外卖/星耀电子 = 娱乐/外卖/数码.
Test asserts `sorted(amounts) == [26800, 128000, 349900]` and codes ⊇ the three codes.

### Everyday spends (spec window 2026-07-17 .. 2026-10-14)
- 140 CARD_SPEND rows on `crd_zs_1`, spread across 2026-07-17 .. 2026-10-12 via
  `day_offset = (i * 88) // 140` (offsets 0..87), at daytime hours 09:00-16:59,
  so **every month Jul/Aug/Sep/Oct has everyday spending**.
- Categories **日用 / 零食 / 茶饮 / 生鲜** (`mer_daily` 日用百货 / `mer_snack` 便利零食 /
  `mer_tea` 街角茶饮 / `mer_fresh` 社区生鲜) — disjoint from the anomaly categories
  (娱乐/外卖/数码) and the enriched October rows (餐饮/出行/零售).
- Constant per-merchant amounts (1500 / 800 / 1800 / 3200) give σ=0, so
  CATEGORY_OUTLIER (`amount > μ + 3σ`) can never fire; daytime hours avoid
  NIGHT_LARGE; amounts < 100000 avoid NEW_MERCHANT_LARGE; `(i * 7) % 60` minute
  spacing keeps same-merchant charges >= 44 minutes apart (no DUPLICATE).

### Subscriptions (analytics.py 343-525) — exactly 4, gym idle
- MANDATE charges (kind="MANDATE", merchant_id set), 3 each at 06:00, MONTHLY intervals
  (28-31 days): 爱看视频 3000 day18 (07/08/09), 乐听音乐 1500 day20 (07/08/09),
  云盘 2100 day01 (08/09/10), 力健健身 19900 day25 (07/08/09).
- Gym idle: 6 non-charge in-store visits (kind=CARD_SPEND at mer_gym) 07-19..08-15,
  none after 08-16 → previous_usage_count=6, recent_usage_count=0, is_idle.
- **mer_gym must have `usage_expected: true`** in the seed (the test fixture sets this;
  `detect_subscriptions` reads it). Other 3 merchants `usage_expected: false`.
- `detect_subscriptions` groups by merchant_id and ignores card_id for MANDATE.

### Existing rows to keep
All 13 existing transactions stay. Set explicit `card_id` (crd_zs_1) and `category` on
txn_zs_010/011/012 (currently CARD_SPEND 兰州拉面 4550 / 地铁 600 / 超市 12800 with no
card_id/category). txn_zs_013 (refund IN 10-14T10:20, verbatim memo) is the last txn and
must remain the latest (balance_after 600000).

## Remaining steps (for resume session)

1. Locate merchant preparation from JSON (search app/seedlib.py + app/seed_demo.py for
   merchant list assembly) and confirm how `usage_expected` / `mcc` flow into the
   `Merchant` model (model fields: id, name, mcc, category, usage_expected — see
   `tests/obligations_data/fixtures.py` lines 153-166).
2. Write `scripts/generate_seed_history.py`: deterministic generator emitting concrete
   rows (existing 13 + ~140 daily CARD_SPEND 日用/零食/茶饮/生鲜 across 07-17..10-12 +
   12 MANDATE charges + 6 gym visits + 4 anomaly txns), recomputing the balance_after
   chain and acc_zs_chk credits/debits, adding new everyday merchants +
   夜色酒吧/美味外卖/星耀电子, setting `usage_expected: true` on mer_gym, explicit
   category/card_id everywhere.
3. Regenerate `services/bank-backend/fixtures/l1_seed_v1.json`; run `make test-obligations`
   (tail) until the two red analytics tests turn green; commit after each passing step.
4. Gates: `make lint`, `make test`, `make test-obligations`, `make test-e0b`,
   `make contracts-check` (tail -1 each).

## Real command results (this session)

- `make test-obligations` (after red-test commit 1e934f5): **2 failed, 156 passed** —
  `test_detect_anomalies_exactly_three_on_crd_zs_1` FAILED, `test_detect_subscriptions_four_with_gym_idle`
  FAILED (both because the seed has no history yet), `test_zhangsan_checking_balance_refund_memo_and_ledger`
  PASSED.
- `.venv/bin/python scripts/generate_seed_history.py`: `wrote 175 transactions (new OUT 889924 cents,
  credits 2097874, debits 1497874)` — 13 original + 162 generated (140 daily CARD_SPEND + 12 MANDATE +
  6 gym visits + 4 anomaly txns).
- `make test-obligations` (after regeneration, commit 3655de8): **158 passed, 1 warning** —
  both analytics tests green, balance/memo/ledger test still green.
- `make lint` (before format fix): 1 error — E501 line too long in `test_seed_history.py:144`
  (pre-existing). Fixed by wrapping the credits_pending assertion and running `ruff format` on
  `scripts/generate_seed_history.py` + `tests/obligations_data/test_seed_history.py` (commit `aa9099b`).
- `make lint` (final): **pass** (ruff check "All checks passed!", format "428 files already formatted").
- `make test`: **3332 passed, 906 skipped**.
- `make test-obligations`: **158 passed, 1 warning**.
- `make test-e0b`: **119 passed**.
- `make contracts-check`: **PASS: contracts v1 match frozen schemas**.

### Review-fix round (this session)

- `.venv/bin/python scripts/generate_seed_history.py` (after P1-2): `wrote 175
  transactions (new OUT 891480 cents, credits 2099430, debits 1499430)` — everyday
  spends now span 2026-07-17..2026-10-12 in 日用/零食/茶饮/生鲜.
- `make test-obligations` (after P1-2 + monthly-coverage test): **159 passed, 1 warning**.

Note: the E0 runner suite uses `mini_seed.json`, not `l1_seed_v1.json`, so no old transaction-count
assertion needed updating.
