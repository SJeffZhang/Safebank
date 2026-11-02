# November 1 MCC/history and query evidence independent review

Initial exact target: e42d2de9786bb2e16a053cc950a06384d8a89415. Corrected exact target: b9980d3e43049b1dd2e1b2aca3ebfeab41b9deee. Base: fa9708d. Reviewed from git archive in /private/tmp/sbg-nov1-repairs/review-categories-e42. No worktree/WIP/product modification; pure tests and private probes only, no real API/DB/provider/Docker/browser.

## Verdict

APPROVE at b9980d3e43049b1dd2e1b2aca3ebfeab41b9deee after the loader P2 was corrected and independently rechecked; initial REQUEST_CHANGES retained below.

## Historical and new transaction category projection

Check deterministic MCC mapping, canonical consumer kinds, owned-customer isolation, filtered totals and input immutability; free merchant/transaction text cannot choose category.

## Anomaly baseline

Check historical/new same-category reference treatment without extending trust to free text or other customers.

## Business-date and compiled case12 evidence

Assess test fidelity and stated limits. Actual PG not rerun by this reviewer. Do not conflate synthetic compiled graph result with original provider/fallback root cause or live acceptance.

## Independent checks

See the executed checks and P2 below.

## Findings

See the executed checks and P2 below.

## Finding P2 — the real loader omits known card-only merchant metadata

`services/bank-backend/app/state.py:162–170` loads `obligations.merchants` from billing and mandate merchant IDs only. New `transaction_category` at merchant_categories.py:42 consults only this map. A normal CARD_SPEND merchant that exists in the authoritative merchants table but has no subscription billing/mandate is therefore treated as missing, falling back to its old transaction category. The pure new tests inject a fully populated merchant snapshot and do not exercise this normal loader boundary.

Independent actual-loader reproduction (only SQL execute replaced): known ordinary restaurant mer_food, MCC5812, stored legacy transaction category数码,4550 cents, no subscription. load_bank_state omits the real merchant and the summary returns数码 instead of餐饮; amount4550 is preserved. Reproduction: archived `tests/test_review_category_boundaries.py::test_real_loader_includes_known_non_subscription_merchant_for_history_classification`, line81. Command from the archive: `PYTHONDONTWRITEBYTECODE=1 /Users/jet/.codex/worktrees/safebank-runtime-replay/WeBank/.venv/bin/python -m pytest tests/test_review_category_boundaries.py -q -p no:cacheprovider`. Result **1 failed /4 passed**, log `/private/tmp/sbg-nov1-repairs/review-categories-e42-loader.log`.

Fix direction: include authoritative merchant metadata for the requesting customer's loaded historical transactions (and retain existing obligation merchants), or an equivalent read projection before category mapping. Preserve customer isolation, raw transaction immutability, exact amounts and the missing-record fallback. Do not infer MCC from counterparty/memo or load other customers' transaction data. Coordinator owns state.py and was notified; reviewer did not edit it.

## Independent checks executed

- `tests/bankcore` plus new six category tests, compiled case12 and existing query gather tests, date PG test collection/skip, and first4 reviewer category controls: **521 passed /1 skipped**. The skip is the actual PG test; no database was started. Log `/private/tmp/sbg-nov1-repairs/review-categories-e42-pure.log`.
- Additional real-loader control then exposed the P2 above: **4 passed /1 failed**. Private controls verify previous/current amounts and filter totals, unchanged raw input, no free-text MCC inference, transfer-category retention, same authoritative category for anomaly baseline/view, and exclusion of foreign-customer/future rows from the candidate's baseline.
- Ruff check over all changed production Python and author test files exits0.
- Compiled case12 test correctly uses production graph/read/respond/verify adapters with synthetic G20/understanding and read executor, asserts no write ports and distinguishes95050 all-outflow from55050 consumption+40000 transfers. Repeated cursor remains bounded and incomplete detail is disclosed. The test/doc explicitly leave original production fallback trigger unknown; no evidence overclaim found.
- PG date test was inspected, not executed: it uses production persist, explicit UTC PostgreSQL session, actual loader and public get_holdings, asserting storage10/14 16Z versus business trade_date10/15 and unchanged confirmation/settlement instants. Fixture admin product seeding is separated from runtime writes. Root's supplied red/green run evidence remains root-owned.

## Disposition

**REQUEST_CHANGES** for e42d2de9786bb2e16a053cc950a06384d8a89415 due to the ordinary-merchant loader P2. No new funds-write or authorization path found. Original formal43 counts19/16/8 remain unchanged. Root owns a correction and final PG/combined-head CI.

## Correction recheck — b9980d3e43049b1dd2e1b2aca3ebfeab41b9deee

Reviewed a separate exact git archive at `/private/tmp/sbg-nov1-repairs/review-categories-b998`; original e42 archive, failing loader probe and REQUEST_CHANGES remain above unchanged.

The correction supplies `_load_obligations(transaction_merchant_ids=...)` with merchant references from already-loaded transactions only when customer_id matches the request, account_id is currently owned and kind is one of CARD_SPEND/MERCHANT_CHARGE/MANDATE. This set is unioned with existing billing/mandate merchants before the existing IN-filtered query. No global-all catalogue load, free-text classification or historical transaction mutation was added.

The original failing loader reproduction now passes. A new independent SQL-boundary probe additionally asserts the exact requested merchant ID set, excluding a foreign customer's row, a mismatched/nonowned account, a non-consumption transfer and an unrelated catalogue row, while preserving the own known restaurant. The author new-loader test also passes. All previous category/anomaly controls remain green.

Final combined archive command includes six private reviewer controls, all bankcore, six category author tests, the new loader author test, compiled case12/query-gather tests and skipped PG date test. Result **524 passed /1 skipped**. Log `/private/tmp/sbg-nov1-repairs/review-categories-b998-final.log`. Ruff check for changed loader/author loader test exits0. Actual PG was not run by the reviewer. The five S1 PG tests bundled as root evidence do not change the category production scope and are not counted as reviewer-run PG success.

### Final disposition

**APPROVE** at exact b9980d3e43049b1dd2e1b2aca3ebfeab41b9deee for this category/date/compiled-query evidence component. The original merchant-loader P2 is closed by the independently rerun same probe and additional scope negative. Original e42 REQUEST_CHANGES and red evidence are retained. This does not replace root's actual PG/serial combined-head CI, provider/browser acceptance or formal43 results.
