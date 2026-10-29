# L1-card-map — real read-tool → card-type mapping

**Branch**: `fix/l1-card-map` (from main)
**Status**: done (lint + agent tests + test-cards + full test suite green)

## Bug

`services/agent/app/llm/cards.py` `_READ_TOOL_CARD_TYPE` keyed off five tool
names that are not real read tools in `safebank_contracts.tools.READ_ARGS`:

- `get_balance_history` (real balance history ships inside `get_accounts`)
- `get_subscriptions` (real name: `list_subscriptions` / `detect_subscriptions` / `upcoming_charges`)
- `get_products` (real name: `list_products` / `compare_products` / `get_product`)
- `get_portfolio` (real name: `get_holdings`)
- `get_anomalies` (real name: `detect_anomalies`)

Result: subscription / product / holding / anomaly cards were never produced
from analysis reads, even though the corresponding reads exist.

## Fix

Rewrote the mapping to key off real `READ_ARGS` names:

| tool | card type |
|---|---|
| `get_accounts` | ACCOUNT |
| `get_transactions` | TRANSACTIONS |
| `get_spending_summary` | TRANSACTIONS |
| `get_cards` | CARD |
| `list_subscriptions`, `upcoming_charges`, `detect_subscriptions` | SUBSCRIPTIONS |
| `list_products`, `compare_products`, `get_product` | PRODUCTS |
| `get_holdings` | HOLDINGS |
| `detect_anomalies` | ANOMALIES |

5-card cap and no-visible-text `CardAction` rule untouched (both already
enforced in `generate_cards` / `CardAction`).

## Tests

`tests/agent/test_card_map.py` (new, 15 tests):

- `TestMappingGuard` — every mapping key ∈ `READ_ARGS`; previously-broken
  real tools are mapped.
- `TestCardTypesFromReads` — one realistic read result per card type
  (shapes from `views.py` `READ_RESULTS`), asserts the produced card type.
- `TestCardCapWithRealTools` — 8 real read tools still capped at 5 cards.

Red-first verification: on the old mapping the guard + 9 card-type/cap tests
failed (12 failed / 3 passed); after the fix all 15 pass.

## Gates

```text
make lint                                   → All checks passed
.venv/bin/pytest tests/agent -q             → 547 passed, 38 skipped
make test-cards                             → 19 passed
make test                                   → 3385 passed, 903 skipped
```

## Commits

- `28d0949` test(agent): red tests for real read-tool -> card-type mapping
- `a380cca` fix(agent): map real read tools to card types
- `…` style(agent): format test_card_map.py
