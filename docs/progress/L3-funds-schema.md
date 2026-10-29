# L3-funds-schema

Task: add migration `0008_funds` (down_revision `0007_products`) creating
`holdings`, `fund_orders`, `event_executions`, mirror the three `Table`
definitions in `app/models.py`, advance `db.ready()` head to `0008_funds`, and
update every schema test asserting `0007_products` to `0008_funds`.

## What changed

- `services/bank-backend/migrations/versions/0008_funds.py` (new)
  - `holdings`: customer_id/account_id/product_id, units/reserved_units
    numeric(18,4), cost_cents/reserved_cost_cents bigint; CHECKs
    `units >= reserved_units >= 0` and `cost_cents >= reserved_cost_cents >= 0`;
    composite `UNIQUE(customer_id, account_id, product_id)`; composite FK
    `(account_id, customer_id) → accounts`; product FK → products.
  - `fund_orders`: id PK, SUB/RED type, five-state status, amount_cents/units/
    reserved_cost_cents + nullable nav/pending_transfer_id/confirm_at/settle_at/
    expires_at/failure_code; CHECKs type, status, amount_cents>0, units>0,
    reserved_cost_cents>=0; composite account FK + product FK.
  - `event_executions`: event_id PK FK→event_jobs.id, terminal status enum,
    authorization_evidence/result_json jsonb, committed_at timestamptz;
    `immutable_event_executions` forbid_mutation trigger (UPDATE/DELETE blocked).
  - Grants: holdings/fund_orders `SELECT,INSERT,UPDATE`; event_executions
    `SELECT,INSERT` (append-only); all `SELECT` for bank_ro.
- `services/bank-backend/app/models.py`: appended `holdings`, `fund_orders`,
  `event_executions` tables + `FUND_ORDER_TYPE_CHECK`/`FUND_ORDER_STATUS_CHECK`/
  `EVENT_EXECUTION_STATUS_CHECK` constants (constraint names match migration for
  the `compare_metadata` drift test).
- `services/bank-backend/app/db.py`: `ready()` head `0007_products` → `0008_funds`.
- Tests asserting the head revision → `0008_funds`: `tests/integration/
  test_database.py`, `test_products_schema.py` (×2), `test_splits_schema.py`
  (×2), `test_event_jobs_schema.py` (exact + tuple now includes `0008_funds`),
  `test_risk_schema.py` (×2), `test_obligations_data.py`, `test_0002_upgrade.py`.

## Test results

- RED (before implementation, commit `f4ebabb`): `make test-obligations`
  → **8 failed, 156 passed** — the 8 new funds-schema tests failed because
  `0008_funds` did not exist (head was still `0007_products`).
- GREEN: **not run in this session** — the launcher tool budget was exhausted
  after the implementation commit `5bdbb37`. Coordinator must run
  `make test-obligations` (expect 164 passed) and the gates
  `make lint`, `make test`, `make contracts-check`.

## Commits

- `f4ebabb` — red tests for 0008 funds schema.
- `5bdbb37` — migration + models + db.py head + test assertion updates.
