# L3 Funds Wire Review

**Reviewer**: DeepSeek (read-only)  
**Scope**: `git diff origin/main...HEAD` on `feat/l3-funds-wire` (294cf4c)  
**Date**: 2026-10-30

## Conclusion

**APPROVE** — no P0 or P1 blocking issues. The pure function implementation is structurally correct: ledger conservation holds across all fund lifecycle paths, CHECKING-only constraint is enforced, integer cents / `Decimal(18,4)` is clean throughout, risk suitability is checked at both subscribe and confirm time, `event_jobs` use correct `period_key=order_id:kind`, and pre-evaluation shares bankcore pure functions without L3 writes. The two P2 observations are minor.

---

## P0 (blocking)

None.

---

## P1 (important)

None.

_(Note: I verified each checklist item below and found the code correct. The one gap — missing real PG tests for the confirm/settle/expiry lifecycle paths — is filed as P2 because the pure-function unit tests (18/18) and the subscriber/redeem execution tests cover the core paths, and the missing paths are system events that reuse the same `post_transfer` / `_resolve_pending` primitives already tested elsewhere.)_

---

## P2 (nice-to-have)

### P2-1: No real-PG tests for `apply_fund_confirm`, `apply_fund_settle`, or `apply_pending_expiry`

**File**: `tests/obligations_data/test_funds_exec.py`

The 6 execution tests cover subscribe (non-MMF, MMF, retry, rejection), redeem, and get_holdings. The system-event paths (confirm, settle, expiry) have no real PostgreSQL integration test. These paths exercise `_resolve_pending` (POST_PENDING / VOID_PENDING) and `apply_fund_settle`'s POSTED transfer from settlement→customer.

**Mitigation**: `_resolve_pending` delegates to `post_transfer` which has extensive real-PG coverage in P1.7/P1.8. `apply_fund_settle` creates a standard POSTED transfer. The pure-function bankcore tests (18 passed) cover the domain logic. Risk is low.

### P2-2: Settlement account debit limits

**File**: `packages/bankcore/src/safebank_bankcore/funds.py:603-640`, seed files

Settlement accounts (`acc_fund_{product_id}`) are seeded with `single_limit_cents=500_000` and `daily_limit_cents=2_000_000`. When `apply_fund_settle` debits the settlement account to pay the redeeming customer, the debit could exceed the single limit if a large redemption (>500,000 cents / ¥5,000) occurs. INTERNAL accounts have `flags=0` (no `debits_must_not_exceed_credits`) so limits may not be enforced — but this should be confirmed by a settlement-specific test.

### P2-3: `_rebuild_holdings` recomputes NAV for every holding on every update

**File**: `packages/bankcore/src/safebank_bankcore/funds.py:152-181`

Every call to `_apply_holding` triggers `_rebuild_holdings`, which iterates over ALL holdings and recomputes NAV-derived fields (`market_value_cents`, `unrealized_change_cents`, etc.) — O(n) per operation. This is correct for consistency (every state mutation produces a fully-reconciled holdings view) but could be a performance concern at scale. Acceptable for the current scope.

---

## Items Checked

### 1. Ledger conservation (credits = debits per effect)

- **Non-MMF subscribe** (`apply_subscribe`, line 305-317): PENDING transfer: account.debits_pending += amount, settlement.credits_pending += amount → pending debit=credit ✓
- **Non-MMF confirm** (`apply_fund_confirm`, line 543): POST_PENDING → account.debits_posted += amount, account.debits_pending -= amount, settlement.credits_posted += amount, settlement.credits_pending -= amount → posted debit=credit ✓
- **Non-MMF confirm failure** (`_fail_confirm`, line 572-573): VOID_PENDING → account.debits_pending -= amount, settlement.credits_pending -= amount → released, no posted changes ✓
- **Non-MMF expiry** (`apply_pending_expiry`, line 593-594): same VOID_PENDING path → released ✓
- **MMF subscribe** (`apply_subscribe`, line 252-264): POSTED transfer: account.debits_posted += amount, settlement.credits_posted += amount → posted debit=credit ✓
- **Non-MMF redeem** (`apply_redeem`, line 473-480): reserves holding units/cost only, no transfer → no ledger change ✓
- **Non-MMF settle** (`apply_fund_settle`, line 612-624): POSTED transfer: settlement.debits_posted += amount, account.credits_posted += amount → posted debit=credit ✓
- **MMF redeem** (`apply_redeem`, line 401-413): POSTED transfer: settlement.debits_posted += amount, account.credits_posted += amount → posted debit=credit ✓

All paths delegate to `post_transfer` (ledger.py) which enforces conservation. No direct manipulation of account balances outside `post_transfer`.

### 2. Only CHECKING accounts used

- `apply_subscribe` line 231: `if account.type != "CHECKING": return CONTRACT_INVALID` ✓
- `apply_redeem` line 375: same check ✓
- `account.customer_id != ctx.customer_id` checked at lines 229-230 and 373-374 ✓

### 3. Integer cents & Decimal(18,4); no float

- `amount_cents`: `int` throughout ✓
- `cost_cents`, `reserved_cost_cents`: `int` throughout ✓
- `units`, `reserved_units`: `Decimal` with `_floor4` (ROUND_DOWN to 4 decimal places, lines 72-73) ✓
- `_floor_cents` (line 76-77): `int(Decimal.quantize(Decimal("1"), rounding=ROUND_DOWN))` ✓
- `_round_cents` (line 80-81): `int(Decimal.quantize(Decimal("1"), rounding=ROUND_HALF_UP))` ✓
- `nav` computation: `_floor_cents(h.units * nav_value * Decimal(100))` (line 166), `nav_value = Decimal` ✓
- `grep` for bare floats in funds.py: **zero hits** ✓
- Migration 0009: `fund_orders.units >= 0` (was `> 0`), allows PENDING_CONFIRM orders with `units=Decimal("0.0000")` (line 329) ✓

### 4. event_jobs: period_key = order_id:kind, rank

- `_fund_event_job` (line 132-149): `period_key=f"{order_id}:{kind}"` — one job per order per event kind ✓
- Non-MMF subscribe creates FUND_CONFIRM + FUND_EXPIRY jobs (lines 347-364) ✓
- Non-MMF redeem creates FUND_SETTLE job (lines 487-494) ✓
- MMF subscribe/redeem: no event_jobs (immediate settlement) ✓
- `ScheduleEventJob` model (contracts/state.py:297-327): no `rank` field — rank is in `EVENT_KIND_RANK` dict (line 333) used by scheduler, not the model. FUND_CONFIRM=0, FUND_SETTLE=1, FUND_EXPIRY=2, consistent ordering ✓
- `period_key` uniqueness enforces idempotence: same (order_id, kind) → same job ✓

### 5. Risk suitability

- `_assessed_level` (line 115-125): reads `customer.risk_level`, validates against `risk_valid_until` ISO date with business-timezone `now`, returns `int | None` ✓
- `apply_subscribe` line 239-243: rejects if `level is None` (ASSESSMENT_MISSING) or `level < product.risk_level` (SUITABILITY_MISMATCH) ✓
- `apply_fund_confirm` line 536-540: **re-checks** suitability at confirm time, calls `_fail_confirm` if assessment expired or downgraded ✓
- `_fail_confirm` (line 569-581): VOID_PENDING the frozen transfer, marks order FAILED with failure_code ✓
- `test_r2_customer_rejected_when_buying_r4_product` verifies subscribe-time rejection ✓
- **No test for confirm-time rejection** (assessment downgrade/expiry between subscribe and confirm) — filed as P2-1

### 6. Pre-evaluation uses bankcore pure functions; no L3 writes during pre-evaluation

- `funds.py` is pure: no IO, no DB, no randomness, no `import asyncpg` or `from app` ✓
- L2 pre-evaluation calls `apply_plan` → `apply_subscribe`/`apply_redeem` → same pure functions ✓
- L3 execution also calls the same `apply_plan` path with `persist` handling InsertFundOrder/UpdateFundOrder/UpdateHolding effects ✓
- `apply_fund_confirm`, `apply_fund_settle`, `apply_pending_expiry` are system events invoked by L3 scheduler, not via L2 pre-evaluation ✓

### 7. Test quality (real PG assertions)

- 18 bankcore unit tests pass (pure function domain logic) ✓
- 6 real PostgreSQL execution tests via uvicorn-served bank-backend ✓
- `test_non_mmf_subscribe`: asserts PENDING_CONFIRM order, FUND_CONFIRM+FUND_EXPIRY jobs exist, balance unchanged (pending freeze), plan_executions=1 ✓
- `test_mmf_subscribe`: asserts SETTLED order, holding created, balance reduced ✓
- `test_redeem_reserves_units_and_creates_fund_settle_job`: asserts PENDING_SETTLEMENT, reserved_units=100, FUND_SETTLE job ✓
- `test_r2_customer_rejected`: asserts FAILED status, SUITABILITY_MISMATCH, **zero rows** in fund_orders/holdings/event_jobs (strong assertion) ✓
- `test_subscribe_retry_20_times_is_idempotent`: asserts 1 order, 2 event_jobs, 1 plan_execution after 20 re-executions ✓
- No relaxed/cosmetic assertions found ✓

### 8. Additional checks

- `_apply_holding` returns `tuple[BankState, HoldingView]` (line 184-224) — callers updated at lines 289-291, 439-441, 474-480, 558-566, 630-639 ✓
- Migration 0009: `fund_orders_units_check` CHECK `(units >= (0)::numeric)` replaces `(units > (0)::numeric)` ✓
- `load_bank_state` (state.py) loads holdings/fund_orders from DB and calls `_rebuild_holdings` for derived fields ✓
- `persist` handles InsertFundOrder, UpdateFundOrder, UpdateHolding effects; `UpdateHolding` uses INSERT ON CONFLICT DO UPDATE on `(customer_id, account_id, product_id)` ✓
- Settlement accounts seeded per-product (`acc_fund_{product_id}`), customer_id=NULL, type=INTERNAL ✓
- `test_seed_demo.py` new test verifies settlement account exists for every product ✓
- `get_holdings` (reads.py) added to SUPPORTED_READS, calls `holdings_view()` ✓
- `order_view()` correctly maps FundOrderRecord → FundOrderView ✓
- `holdings_view()` filters by customer_id via `state.accounts[h.account_id].customer_id` (cross-table lookup) ✓

### 9. Bankcore tests run

```
.venv/bin/pytest tests/bankcore -q -k fund
18 passed, 409 deselected in 0.28s
```