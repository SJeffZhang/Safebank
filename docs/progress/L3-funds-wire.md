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
- NOT yet done (next session): state.py `load_bank_state` (load holdings +
  fund_orders into BankState, `_rebuild_holdings` before return) and
  `persist()` handlers for InsertFundOrder / UpdateFundOrder / UpdateHolding;
  reads.py `get_holdings` + SUPPORTED_READS; test_funds_schema.py 0009
  updates; test_funds_exec.py; full gates (make lint / make test /
  make test-obligations / make contracts-check).
