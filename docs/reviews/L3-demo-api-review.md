# L3 Demo API Review — `f04cc9f` (origin/main..HEAD)

Scope: `services/bank-backend/app/demo.py` (new), `services/bank-backend/app/main.py` (route
mounting + `demo_mode`/`admin_db` wiring), `tests/obligations_data/test_demo_api.py` (new).
Static, read-only review. Did not run the test suite (would need tmpfs PG + docker).

## Verdict

**APPROVE** — no P0, no P1. Checklist items all pass; findings below are P2 (cosmetic /
deployment-verification / future-executor obligations only).

---

## Checks

| # | Check | Result |
|---|---|---|
| 1 | Routes absent (404) unless DEMO_MODE/EVAL_MODE | **PASS.** `register_demo_routes` is called only under `if demo_mode:` (main.py:236); `demo_mode` defaults to `_env_flag("DEMO_MODE") or _env_flag("EVAL_MODE")` (main.py:55-56). Without it the three paths are never added → 404. Test `test_demo_routes_404_when_demo_mode_false` covers it. |
| 2 | Still behind the service token | **PASS.** `FastAPI(..., dependencies=[Depends(authenticate)])` (main.py:79-87) applies the `authenticate` dependency to every route on the app, including the demo routes registered later via `@app.post(...)` on the same `app`. `authenticate` uses `secrets.compare_digest` on the full token (main.py:75-77). |
| 3 | reset/prepare never delete/rewrite ledger rows; idempotent | **PASS.** Both call `_install_demo` → `install_demo` (pre-existing, not in this diff) which: computes `new_customer_ids = prepared − existing` and skips existing customers entirely; only inserts transfers/transactions for *new* customers; the only `UPDATE`s touch `SEED_EQUITY` / `INTERNAL_CLEARING` / payee `created_at` and are either driven by new-customer funding or reference-data convergence, never a delete or a rewrite of customer ledger rows (ledger is append-only). Idempotency confirmed by `test_reset_idempotent` (customer count unchanged across two runs). |
| 4 | /events only inserts a PENDING DEMO_EVENT job (no execution) | **PASS.** Handler performs exactly one `INSERT` into `event_jobs` with `status="PENDING"`, `kind="DEMO_EVENT"`; no other write, no call into any executor. |
| 5 | Payload hash canonical | **PASS.** `json.dumps(payload, sort_keys=True, separators=(",", ":"))` then SHA-256 (demo.py:78-80), with `payload = body.model_dump(mode="json")`. Key ordering and separators are fixed. |
| 6 | Idempotent retry | **PASS.** `event_id` is deterministic from the hash; INSERT uses `on_conflict_do_nothing(constraint="event_jobs_payload_hash_key")` (the unique constraint exists — models.py:598). `source_id` and `period_key` are both derived from the payload (`demo:<hash[:16]>`, `due_at.isoformat()`), so the second unique constraint `event_jobs_source_period_key` (models.py:599) cannot collide across *different* payloads (barring a 64-bit prefix collision). Same payload → same id → same existing row → same `event_id` returned. Covered by `test_events_creates_one_job_and_retry_returns_same_id`. |
| 7 | Ownership validated (no cross-customer account) | **PASS.** `SELECT id FROM accounts WHERE id = :account_id AND customer_id = :customer_id` → 404 if empty (demo.py:90-98). Covered by `test_events_foreign_account_404`. |
| 8 | Amounts int cents > 0 | **PASS.** `amount_cents: int = Field(gt=0)` (demo.py:52). Zero/negative/unknown-kind rejected by Pydantic → 422. Covered by `test_events_bad_kind_and_amount_422`. |
| 9 | Whitelisted kinds only | **PASS.** `kind: Literal["EXTERNAL_CREDIT","EXTERNAL_DEBIT","SPLIT_PAYMENT"]` (demo.py:50). Any other kind → 422. (The `DEMO_EVENT_KINDS` tuple at demo.py:34 is dead code — see P2-3.) |
| 10 | No SQL built from user input | **PASS.** All queries use SQLAlchemy parameterized `select`/`pg_insert`; the only `text()` is the advisory lock with a bound parameter. `seed_id`/`scenario_id`/`kind` are validated against fixed dicts/`Literal`, never interpolated into SQL. |

---

## Findings

### P0
None.

### P1
None.

### P2

- **P2-1 — reset/prepare require owner credentials at runtime (deployment verification).**
  `_install_demo` runs on `admin_database`, which in `factory()`/`create_app()` resolves to
  `BANK_MIGRATION_DATABASE_URL or BANK_DATABASE_URL` (main.py:62-65). If `DEMO_MODE` is enabled
  on the bank-backend container but `BANK_MIGRATION_DATABASE_URL` is not plumbed in, the routes
  fall back to the runtime role URL and `install_demo` will fail (fail-closed, 503/403 — not a
  security hole, but the demo D1/D2 routes won't work in the deployed stack). Verify the
  compose/`scripts/keys.py` env for the bank-backend container actually sets both
  `DEMO_MODE`/`EVAL_MODE` *and* owner-role `BANK_MIGRATION_DATABASE_URL`. (The tests pass
  `admin_db=` explicitly, so they don't exercise this path.)

- **P2-2 — service token temporarily exercises owner-role writes in demo/eval mode.**
  `reset`/`prepare` run the seed installer on the owner role, and `/events` inserts jobs — a
  wider write surface than the normal L3 runtime role gives the gateway's service token. This
  is the intended demo surface and is gated by `DEMO_MODE`/`EVAL_MODE` plus the fixed
  `DEMO_SEEDS`/`DEMO_SCENARIOS` whitelist (only `l1_seed_v1`, reading a fixed fixture path —
  no arbitrary data installable), so it is not a blocker. Worth keeping in mind as
  defense-in-depth if `DEMO_MODE` ever ships in a non-demo deployment.

- **P2-3 — `DEMO_EVENT_KINDS` is dead code.** The tuple (demo.py:34) is never referenced; the
  real whitelist is the `Literal` type. Either remove it or have the validator reference it so
  the two can't drift.

- **P2-4 — `due_at` has no bounds.** Only "timezone-aware" is enforced. A far-past event would
  be picked up immediately by the (future) scheduler and a far-future one lingers. No security
  impact for this insert-only endpoint, but the eventual DEMO_EVENT executor must re-validate
  `kind`/`amount_cents`/per-kind required fields (`split_item_id` for `SPLIT_PAYMENT`,
  `card_id`/`counterparty` semantics), which are currently stored in `immutable_payload`
  without per-kind validation (correctly deferred — this endpoint executes nothing).

- **P2-5 — semantic (not byte) idempotency.** Two "same" events differing only in `due_at`
  timezone offset (e.g. `+08:00` vs `Z` for the same instant) hash to different rows. Byte-level
  idempotency is the stated contract and is fine for demo use; just noting the hash is
  representation-sensitive.

- **P2-6 — `dataset_revision` response is not read back from the DB.** `reset`/`prepare` return
  the hardcoded `l1_seed_v1.0` without verifying the installed `dataset_revision`. Cosmetic;
  the installer deliberately never touches `clock.sim_now`/`dataset_revision`.
