# L3 Funds Schema (migration `0008_funds`) — Independent Review

Review scope: `git diff origin/main..HEAD` limited to the funds schema work —
`0008_funds.py` (holdings, fund_orders, event_executions), `app/models.py`
funds tables, `app/db.py` head bump, and `tests/obligations_data/test_funds_schema.py`.
Static review, no Docker. Reviewer read PRD.md lines 1238–1245 for the schema contract.

## Verdict

**APPROVE** — no P0/P1. Schema matches PRD §8.3, models.py and the migration are
byte-for-byte consistent in column sets, CHECKs, FKs and the composite unique key;
grants, forward-only downgrade and head-version bump all follow the established
0001–0007 patterns. Tests are meaningful (version+columns, every CHECK, unique
violation, trigger immutability, runtime DDL/DELETE denial, 0002→head data
preservation). Only minor P2 test-coverage and branch-hygiene observations below.

## Findings

### P0
None.

### P1
None.

### P2 (non-blocking)

- **P2-1 — branch is behind `origin/main`; diff contains unrelated reversals.**
  The funds branch is based on `0b9684e`; `origin/main` has since merged
  L1-default-account and L2-parser-coverage-c. The review diff therefore shows
  deletions of those files (`tests/agent/test_default_account.py`,
  `docs/reviews/L1-default-account-review.md`, `tests/gateway/w1/test_parser_coverage_c.py`,
  etc.) and reversals of `services/agent/**` and `services/gateway/**` changes.
  These are not part of the funds work. The branch must be synced with
  `origin/main` before the PR is opened, or the PR will appear to delete main's
  newer work. (Branch hygiene, not a schema defect.)
- **P2-2 — no test asserts `bank_ro` SELECT-only on the new tables.**
  `test_bank_app_cannot_ddl_funds_tables` proves bank_app cannot DDL/DELETE, but
  no test asserts bank_ro can SELECT and cannot INSERT/UPDATE. Grant statements
  are correct (`GRANT SELECT ... TO bank_ro`); this is coverage parity with the
  sibling migrations, which also omit a bank_ro test.
- **P2-3 — no positive-path test that bank_app can perform its intended writes.**
  Grants give bank_app SELECT/INSERT/UPDATE on holdings and fund_orders and
  SELECT/INSERT on event_executions, but no test inserts/updates via the runtime
  role. Consistent with sibling migrations; the C6 fund components will exercise
  this path.
- **P2-4 — `fund_orders.nav` has no positivity CHECK.** PRD only requires the
  column to be nullable; it does not require `nav > 0`. Noted as a data-integrity
  consideration for the fund components, not a PRD deviation.

## Checks

- ✅ **Columns/types/enums vs PRD 1238–1239, 1245.** `holdings` has exactly the 7
  PRD columns (customer_id, account_id, product_id, units, reserved_units,
  cost_cents, reserved_cost_cents); units/reserved_units `numeric(18,4)`,
  costs `bigint`. `fund_orders` has exactly the 19 PRD columns; `type` CHECK
  `('SUB','RED')`, `status` CHECK
  `('PENDING_CONFIRM','PENDING_SETTLEMENT','SETTLED','FAILED','EXPIRED')`,
  `amount_cents` bigint, `units` numeric(18,4), `nav`/`pending_transfer_id`/
  `confirm_at`/`settle_at`/`expires_at`/`failure_code` nullable. `event_executions`
  matches PRD 1245: event_id PK, status CHECK
  `('SUCCESS','FAILED_INSUFFICIENT','BLOCKED','SKIPPED_REAPPROVAL')`,
  authorization_evidence/result_json JSONB non-null, committed_at non-null.
- ✅ **CHECKs.** holdings: `units >= reserved_units AND reserved_units >= 0` and
  `cost_cents >= reserved_cost_cents AND reserved_cost_cents >= 0` (reserved ≤ total,
  non-negative). fund_orders: `amount_cents > 0`, `units > 0`,
  `reserved_cost_cents >= 0`.
- ✅ **holdings composite unique.** `UniqueConstraint(customer_id, account_id,
  product_id)` = `holdings_owner_product_key`; enforced in models.py and migration.
- ✅ **event_executions PK = event_id → event_jobs.id** (`event_executions_event_id_fkey`).
  Terminal rows immutable via `BEFORE UPDATE OR DELETE ... EXECUTE FUNCTION
  forbid_mutation()`; `forbid_mutation()` is defined in `0001_core.py` and already
  reused by 0003/0006/0007, so this is the established pattern, not a new function.
  Status set matches PRD exactly (4 values).
- ✅ **Grants.** `REVOKE ALL ... FROM bank_app, bank_ro` then
  `GRANT SELECT, INSERT, UPDATE ON holdings, fund_orders TO bank_app`,
  `GRANT SELECT, INSERT ON event_executions TO bank_app` (append-only, no UPDATE/DELETE),
  `GRANT SELECT ON ... TO bank_ro`. No DDL, no TRUNCATE, no DELETE anywhere —
  matches "bank_app read/write as needed, bank_ro read only, no DDL".
- ✅ **down_revision = `0007_products`.** Linear chain 0001→…→0007→0008; origin/main
  has no competing 0008 migration (0008_funds.py is the only added migration file).
- ✅ **Head version updated everywhere.** `app/db.py` `ready()` now requires
  `0008_funds`; every test asserting the bank head was bumped to `0008_funds`
  (`test_database.py`, `test_products_schema.py` ×2, `test_obligations_data.py`,
  `test_risk_schema.py` ×2, `test_0002_upgrade.py`, `test_event_jobs_schema.py`,
  `test_splits_schema.py` ×2, `test_funds_schema.py` ×2). No stale code reference
  to `0007_products` as head remains (the one tuple in `test_event_jobs_schema.py:234`
  intentionally accepts `0004`/`0007`/`0008` for the shared-fixture upgrade test).
- ✅ **downgrade safe.** `downgrade()` raises
  `RuntimeError("Financial core migrations are forward-only")`, identical to
  0001–0007 — forward-only is the safe choice for financial core.
- ✅ **models.py matches migration.** Verified column-for-column: holdings,
  fund_orders, event_executions have identical column sets, types
  (`idcol` = `String(256)`, `timestamp` = `DateTime(timezone=True)`), CHECK
  constraints (same names/sql), the composite FK
  `(account_id, customer_id) → (accounts.id, accounts.customer_id)` (valid — accounts
  has `UniqueConstraint("id","customer_id")` at models.py:85, and this FK pattern
  is already used by merchant_billing/transfers), and the same unique constraint.
- ✅ **Tests meaningful.** `test_funds_schema.py` (9 tests, real tmpfs PG) verifies:
  upgrade-head version + exact column sets for all three tables; reserved>units
  CHECK; negative/inverted cost CHECKs; (customer,account,product) unique violation;
  fund_orders type/status CHECKs; non-positive amount/units CHECKs; event_executions
  UPDATE/DELETE blocked by the trigger; bank_app CREATE/DROP/TRUNCATE/DELETE denied
  on all three tables; 0002→head migration preserves merchants seed data with the
  three new tables empty. Test helpers use valid event_jobs kind `FUND_CONFIRM`
  (present in `EVENT_KIND_CHECK`, 0004), so the immutability test genuinely reaches
  the trigger rather than failing on the job insert.
