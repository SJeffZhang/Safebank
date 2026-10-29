# L1 Card Map Review

## Verdict

APPROVE

## Findings (P0, P1, P2)

- **P0**: none.
- **P1**: none.
- **P2**:
  1. **Test-coverage gap for the "no sensitive leak" property on newly mapped tools.** `tests/agent/test_card_map.py` asserts only `cards[0].type == …` for each of the nine newly mapped tools; it does not assert that untrusted/sensitive fields are stripped from the card `data` for those specific tools (e.g. `merchant_name`/`instruction_id` on subscription items, `name`/`risk_disclosure` on product items, `memo`/`counterparty` inside anomaly transactions). The property is still enforced generically by `_filter_untrusted` (tool-agnostic) and by the masked contract views, so this is a coverage note, not a defect.
  2. **Other real read tools remain unmapped** (informational, out of scope for this change): `list_scheduled`, `get_split_status`, `list_payees`, `get_risk_questionnaire`, `get_risk_profile`, `get_profile` have no entry in `_READ_TOOL_CARD_TYPE`, so e.g. a scheduled-transfer read produces no card. Not a regression; the intent scope did not require them.

## Checks

- **Mapping keys are real READ_ARGS tool names** — `_READ_TOOL_CARD_TYPE` keys verified against `packages/contracts/src/safebank_contracts/tools.py:258-277` (`READ_ARGS`). All 12 keys present: `get_accounts`, `get_transactions`, `get_spending_summary`, `get_cards`, `list_subscriptions`, `upcoming_charges`, `detect_subscriptions`, `list_products`, `compare_products`, `get_product`, `get_holdings`, `detect_anomalies`. Guarded by `TestMappingGuard::test_every_mapping_key_is_a_real_read_tool` (`set(_READ_TOOL_CARD_TYPE) <= set(READ_ARGS)`). ✓
- **Old bogus keys removed** — `get_balance_history`, `get_subscriptions`, `get_products`, `get_portfolio`, `get_anomalies` are gone from the mapping and none of them exist in `READ_ARGS`, so the previous "subscription/product/holding/anomaly cards never produced" bug is fixed. ✓
- **Builders read real result shapes (views.py READ_RESULTS)** — card generation is via the generic `_cards_from_reads` → `_safe_data` (handles both `dict` and `model_dump(mode="json")`), so there is no per-field name access that can mismatch. Result shapes for the nine intent tools confirmed against `views.py:311-385`: `get_spending_summary`→`SpendingResult`, `detect_anomalies`→`AnomaliesResult`, `detect_subscriptions`→`DetectedSubscriptionsResult`, `list_subscriptions`→`page(SubscriptionsResult, SubscriptionView)` (+`total_monthly_cents`/`total_annual_cents`/`normalization_basis`), `upcoming_charges`→`UpcomingResult` (`items`/`scheduled_items`/`charges_total_cents`/`scheduled_transfers_total_cents`/`reserve_total_cents`/`by_account`), `list_products`→`page(ProductsResult, ProductView)`, `compare_products`→`CompareResult(items, comparison_fields)`, `get_product`→`ProductView` (direct model), `get_holdings`→`HoldingsResult(holdings, orders)`. ✓
- **5-card cap kept** — `generate_cards` returns `tuple(cards[:5])`; `TestCardCapWithRealTools::test_cap_still_5` exercises 8 reads → 5 cards. ✓
- **No-visible-text CardAction kept** — `CardAction` has only `kind` and `ref`; no label/text field. ✓
- **No sensitive numbers leak into card fields** — the contract views are the public/masked boundary: `AccountView.account_no_masked`, `PayeeView.phone_masked`/`account_no_masked`, `CardView.last4` (no PAN). `_UNTRUSTED_FIELDS` recursively strips `name`, `merchant_name`, `memo`, `alias`, `description`, `note`, `address`, `phone`, `email`, `id_number`, `passport`. Internal opaque IDs (`acc_*`, `crd_*`, `txn_*`, `prd_*`, `mch_*`, `ins_*`, `billing_*`) do appear in card `data`, but these are public identifiers needed for cross-reference/actions, not sensitive numbers. ✓
- **Test run** — `.venv/bin/pytest tests/agent -q`: **547 passed, 38 skipped** (tail shown). ✓

Reviewed scope: `docs/progress/L1-card-map.md`, `services/agent/app/llm/cards.py` (mapping diff), `tests/agent/test_card_map.py`.
