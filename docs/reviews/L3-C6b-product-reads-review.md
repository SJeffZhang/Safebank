# L3-C6b product reads review (R14/R15/R17 wiring)

Scope: `git diff 89d00d8..HEAD` — `services/bank-backend/app/reads.py`, `services/bank-backend/app/state.py`, `tests/obligations_data/test_product_reads.py`.

## Verdict

APPROVE

No P0/P1. The three reads conform to spec, NAV/return come from bankcore pure functions evaluated at `clock["sim_now"]`, Decimal is used end to end (no float), and the tests assert real values that fail without the wiring. Three P2 observations below (all maintainability/informational, none a current defect).

## Findings

### P0

None.

### P1

None.

### P2

- **P2-1 — R14 filter and R15 `comparison_fields` re-implemented in `reads.py` instead of delegating to bankcore.** `list_products` filtering (`risk_level <= max_risk_level`, `product_type ==`) and the `COMPARISON_FIELDS` list are duplicated inline from `packages/bankcore/.../products.py` rather than calling `bankcore.list_products` / `bankcore.compare_products`. The computed values (nav, `recent_return_bp`, `nav_history`) *do* come from bankcore via `load_bank_state`'s `get_product(...)`, so L2/L3 agreement on values holds today and the inline filter matches bankcore exactly. Risk is future drift if the bankcore filter or field list changes. Suggest a follow-up that routes R14 filtering and R15 `comparison_fields` through the bankcore functions.
- **P2-2 — `nav_history` nav round-trips through `str` inside bankcore.** `bankcore.get_product` emits history entries as `{"nav": str(n)}`, and `state.py` re-parses with `Decimal(pt["nav"])`. It works (4-dp quantized values round-trip exactly; contract `Decimal4` serializes via `format(v, ".4f")`), but a pure-function module returning Decimal/date objects everywhere else should not smuggle one field through a string. Cosmetic/robustness, not a correctness bug.
- **P2-3 — `now.date()` uses `sim_now`'s own tz, not the business timezone.** `load_bank_state` computes NAV on `now.date()` where `now = clock["sim_now"]`. This satisfies "as of sim_now, not wall clock", and is consistent with existing reads (e.g. `get_accounts` uses `state.sim_now.date()`), but a sim_now stored in UTC would pick the wrong date near midnight vs Asia/Shanghai. Pre-existing pattern, not introduced by this diff; note for the broader business-timezone pass.

## Checks

1. **Spec conformance.**
   - R14 `list_products`: optional `max_risk_level` (`<=`) and `product_type` (`==`) filters applied; no suitability filtering. Pagination reuses the shared `paginate` helper with id cursor; scope binds cursor to `(customer_id, tool, query-without-cursor/limit)`, so a filter change mid-pagination yields 422. Items ordered by id in `load_bank_state` (`order_by(m.products.c.id)`), so the id-keyed cursor is stable; last page returns `next_cursor=None`. Matches bankcore `list_products` semantics exactly.
   - R15 `compare_products`: 2–4 unique ids enforced in the `Compare` contract (`min_length/max_length` + `unique` validator → 422); unknown id → `BankError(NOT_FOUND, 404)`. `comparison_fields` matches the spec's 9-field list. Items are full `ProductView`s (contract `CompareResult` declares `tuple[ProductView, ...]`), preserving `product_ids` order.
   - R17 `get_product`: unknown id → 404; returns full `ProductView` incl. `risk_disclosure`, `disclosure_version`, and 30-point `nav_history`.
   - Contracts confirmed: `views.py` `ProductView`/`NavPoint`; `tools.py` `ProductsQuery(Page)`, `Compare` (with unique validator), `ProductQuery`.
2. **NAV/return via bankcore as of sim_now, Decimal end to end.**
   - `load_bank_state` projects each products row to `ProductDef` and calls `bankcore.get_product(tuple(product_defs), p.id, now.date())`, where `now = clock["sim_now"]` (confirmed in `main.py:119–123`: `load_bank_state(conn, scope, clock["sim_now"], ...)`); the read handler routes through the same `snapshot()`.
   - `recent_return_bp` uses `RECENT_RETURN_WINDOW_TRADING_DAYS = 30`, consistent with `nav_history(count=30)`.
   - `products.base_nav` is `Numeric(18,4)`; bankcore `nav.py` is all-Decimal (`Decimal("0.0001")` step, `quantize` 4 dp); `NavPoint.nav` rebuilt as `Decimal(...)`; contract `Decimal4` serializes via `PlainSerializer(lambda v: format(v, ".4f"))` (string on the wire, never float).
3. **load_bank_state mapping / isolation.**
   - All products rows mapped (`select(m.products).order_by(id)`), no customer predicate — the catalog is customer-agnostic by design, so no per-customer data leak. Empty products table yields `products=tuple()`; `list_products` returns `next_cursor=None` and `get_product`/`compare_products` 404 — no crash.
4. **Tests assert real values.**
   - `test_r17_full_disclosure_and_nav` compares the full `nav_history` (30 points) and `nav`/`nav_date`/`recent_return_bp`/`risk_disclosure` against bankcore-computed `_expected_view` for a BOND product — date-sensitive, so it fails if `sim_now` (2026-10-15 in the fixture, `fixtures.py:23/148`) were replaced by wall clock.
   - `test_r14_*` assert sorted ids, filter results, and last-page `next_cursor=None`; `test_r15_*` assert the exact `comparison_fields` list, MMF nav `"1.0000"`, bankcore `recent_return_bp`, 422 on duplicate/out-of-range ids, 404 on unknown id.
   - All would fail without the wiring (empty `state.products` → empty `items` / 404).

## Test run

`OBLIGATIONS_DB_TESTS=1 .venv/bin/pytest tests/obligations_data/test_product_reads.py -q` was **not executed in this session**: the command requires a permission approval that was not granted, and the reviewer is read-only. Static verification above is consistent; the coordinator's immediately preceding commits (`fix UTC import in product read tests`, `supply required nullable ProductsQuery fields in R14 tests`) show these tests were run and iterated to green. Please re-run the target command to record the machine number.
