# L3 funds wire — progress

## 2026-10-29 (worker session, deepseek-v4-pro)

- Committed `3e740d5` (prior session): migration `0009_funds_units_nonneg`
  (`units >= 0`, down_revision `0008_funds`, forward-only).
- Committed `0c6762c`: funds.py `_apply_holding` now returns
  `(state, holding_view)`; five call sites (MMF subscribe, MMF redeem,
  non-MMF redeem, fund_confirm, fund_settle) unpack and append
  `UpdateHolding(holding=...)`. models.py `fund_orders.units` check relaxed
  to `>= 0`.
  - Real command results: `pytest tests/bankcore/test_funds.py -q` → 15 passed;
    `pytest tests/bankcore -q` → 414 passed.
- Committed `44843f2`: state.py checkpoint — added `FundOrderRecord`,
  `HoldingView`, `InsertFundOrder/UpdateFundOrder/UpdateHolding` aliases and
  `_rebuild_holdings` / `BUSINESS_TIMEZONE` imports. Import NOT yet exercised
  (direct `python -c` failed: `safebank_bankcore` not on sys.path outside
  pytest; pytest conftest resolves it).
- Committed `efd4399`: state.py `load_bank_state` now loads holdings + fund_orders,
  calls `_rebuild_holdings` before return; `persist()` handles InsertFundOrder /
  UpdateFundOrder / UpdateHolding; reads.py adds `get_holdings` (R16) wired to
  `holdings_view`. Lint + bankcore 427 passed.
- `make test-obligations`: 161 passed / 14 failed — schema tests still expect 0008
  head, need updating to 0009 (listed below).
- NOT yet done: test_funds_schema.py 0009 version updates; test_funds_exec.py;
  full gates (make lint / make test / make test-obligations / make contracts-check).

### Fixes 2026-10-30 (coordinator diagnosis round)

- Committed `09ae4ec`: test_funds_exec.py — two raw `m.holdings.insert()`
  statements were missing `reserved_cost_cents` (NOT NULL column added by
  0009 migration), causing "null value in column" PostgreSQL errors.
- Committed `6e42b99`: demo seed now generates fund settlement accounts
  (`acc_fund_<product_id>`) for every seeded product:
  - `seedlib.py prepare`: derives `fund_settlement_accounts` from prepared
    products with INTERNAL type, NULL owner, 500k/2M limits
  - `seed_demo.py install`: inserts idempotently via ON CONFLICT DO NOTHING,
    backfills for already-installed databases
  - `test_seed_demo.py`: verifies each settlement account exists with correct
    type, customer_id=NULL, and limits
- Pending re-run: `OBLIGATIONS_DB_TESTS=1 .venv/bin/pytest tests/obligations_data/test_funds_exec.py -q`
  to confirm all 6 tests pass after these fixes.
