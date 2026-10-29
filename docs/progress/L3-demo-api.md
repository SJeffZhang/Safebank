# L3 demo/eval API routes

## Scope

Add the demo/eval-only L3 routes from PRD §9.9:

- `POST /internal/demo/reset {seed_id}` — re-run the idempotent demo seed installer
  (`app.seed_demo.install_demo`) on the owner role, return `{dataset_revision}`.
- `POST /internal/demo/prepare {scenario_id}` — same installer, return `{dataset_revision}`.
- `POST /internal/demo/events {kind, customer_id, account_id, amount_cents, ...}` — insert
  exactly one `event_jobs` row with `kind='DEMO_EVENT'`, idempotent on the payload hash.

All three are mounted only when `create_app(demo_mode=...)` is true (defaults to
`DEMO_MODE` / `EVAL_MODE` env truthiness); otherwise they 404.  They are still
`/internal/*` routes, so the global service-token dependency applies.

## Invariants

- **The ledger is append-only: no route ever deletes or rewinds rows.**  Reset/prepare
  re-run the seed installer, which only inserts *new* customers and otherwise converges
  reference data (payee creation times) / backfills obligations idempotently — it never
  deletes.
- Unknown seed/scenario id → `422 CONTRACT_INVALID`.
- Events account must belong to the given customer, else `404 NOT_FOUND`.

## Implementation

- `services/bank-backend/app/demo.py` — request models, seed/scenario whitelist,
  `_install_demo` (owner engine + advisory lock 814001), and route registration.
- `services/bank-backend/app/main.py` — `create_app(..., demo_mode=None, admin_db=None)`
  keyword; when demo_mode is true, lazily builds an owner/admin engine (from
  `admin_db` or `BANK_MIGRATION_DATABASE_URL` / `BANK_DATABASE_URL`) and registers the
  demo routes.

## Tests

`tests/obligations_data/test_demo_api.py` (run with `make test-obligations`):

- demo routes 404 when `demo_mode=False` (reset / prepare / events)
- reset idempotent: two runs both 200 with `dataset_revision == "l1_seed_v1.0"`,
  customer count unchanged; unknown seed/scenario id → 422
- events creates exactly one job; identical retry returns the same event id;
  row assertions (kind/status/period_key/source_id)
- events invalid kind / amount_cents=0 / naive due_at → 422
- events account belonging to another customer → 404
