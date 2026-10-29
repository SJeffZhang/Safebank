# L3 seed completion — 李四 fund holdings & risk-date pre-states

Task: complete `fixtures/l1_seed_v1.json` + `seedlib.py` + `seed_demo.py` with the
remaining PRD §2.2 pre-states. Gap list first (PRD §2.2 vs current fixture).

## Gap list

- **GAP 1 — 李四's 安心货币 holding is missing.**
  PRD §2.2: 李四 (`cus_ls`) holds 「安心货币」 (`prd_mmf`), `units = 10000.0000`,
  `cost_cents = 1000000` (bought before the seed window, so no ledger rows — the
  holding row carries only cost). The fixture has **no `holdings` key** and the
  installer writes no holdings rows, so redemption/product state for 李四 cannot
  be demonstrated. Settlement account = `acc_ls_chk` (活期).

- **GAP 2 — risk assessment dates diverge from PRD.**
  PRD §2.2: 李四 is R3 assessed `2026-03-01`; 孙八 (`cus_sb`) is R2 assessed
  `2026-05-01`. Fixture has **both** `risk_assessed_at = 2026-09-01T10:00:00+08:00`
  and `risk_valid_until = 2027-09-01T10:00:00+08:00`. `risk_level` values are
  already correct (3 and 2). Converge dates to PRD (valid-until +1 year:
  `2027-03-01` / `2027-05-01`) and sync on reinstall.

- **孙八 blocked-from-R4 (适当性拦截) needs no new data.** `cus_sb.risk_level = 2`
  and `prd_growth.risk_level = 4` are already present; the suitability rule lives
  in gateway/bankcore. The seed just has to keep those rows intact.

- **No other §2.2 rows are missing.** 张三 has no holdings; no other customer
  holds fund units. Products (6) are already installed idempotently.

## Design

- `holdings` stays **optional** in `seedlib` (not added to `_REQUIRED_KEYS`): E0's
  `evaluation/runner/seed.py` loads `mini_seed.json` and does not write products
  or holdings, so assembly is purely additive.
- `seedlib.prepare_seed` validates + assembles holdings (units/reserved_units as
  Decimal strings, cost_cents/reserved_cost_cents ints, account/product ownership
  checked) and returns them under `"holdings"`.
- `seed_demo.install_demo`:
  - inserts holdings for **new** customers;
  - backfills holdings for **skipped** customers via
    `pg_insert(...).on_conflict_do_nothing()` (idempotent, non-ledger);
  - syncs `risk_assessed_at` / `risk_valid_until` for skipped customers using the
    payee `created_at` update pattern;
  - adds summary keys `added_holdings`, `backfilled_holdings`,
    `synced_risk_assessments`.
- No new ledger rows: holdings bought before the seed window carry `cost_cents`
  only; balances stay conserved.

## Tests (red first)

In `tests/obligations_data/test_seed_demo.py`:

- add `"holdings"` to `COUNTED_TABLES`;
- 李四 holding row exists with exact PRD values (units 10000.0000, cost 1000000,
  reserved 0, account acc_ls_chk, product prd_mmf);
- reinstall adds nothing (row counts unchanged, second summary zero);
- backfill for an already-installed customer whose holding was deleted;
- risk dates converge to PRD on reinstall, then 0 on the next run.

## Results (all gates run)

- `make lint` — PASS (ruff check + format)
- `make test` — 3407 passed, 918 skipped
- `make test-obligations` — 170 passed (includes the two new holdings/risk-date tests)
- `make test-e0b` — 119 passed
- `make contracts-check` — PASS: contracts v1 match frozen schemas

Implementation: fixture 李四 holding row (prd_mmf, units 10000.0000, cost 1000000,
account acc_ls_chk) + cus_ls/cus_sb risk dates converged to PRD; `seedlib` parses/
validates/assembles `holdings` (optional key, E0 loader unaffected); `seed_demo`
inserts holdings for new customers, backfills for skipped customers idempotently,
and syncs risk-assessment dates (summary keys `added_holdings`,
`backfilled_holdings`, `synced_risk_assessments`). No new ledger rows.

