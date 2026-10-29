# L3 funds wire — progress

## 2026-10-29 (worker session, deepseek-v4-pro)

- Committed `3e740d5`: migration `0009_funds_units_nonneg` (drop + recreate
  `fund_orders_units_check` as `units >= 0`, down_revision `0008_funds`,
  forward-only). Real command result: file created and committed; migration
  NOT yet executed against a DB.
- Not yet done (next session): models.py line 810 `units > 0` → `units >= 0`;
  funds.py UpdateHolding emission; state.py persist/load wiring; reads.py
  get_holdings; test_funds_schema.py 0009 updates; test_funds_exec.py.
- Gates (make lint / make test / make test-obligations / make contracts-check)
  NOT yet run.
