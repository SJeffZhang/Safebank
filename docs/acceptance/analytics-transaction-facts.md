# Canonical transaction facts, counters and subscription savings

Date: 2026-10-31. Isolated branch `fix/analytics-live-kinds`, based on split-read candidate `cbc744b9d4f30b5f9478d043f1c71f08c2f834c2`.

## A. Canonical analytics kinds

The 29-requirement audit reproduced actual pure transfer/merchant execution followed by spending_summary: persisted P2P_INTERNAL/P2P_EXTERNAL transfers were absent from transfer_out, and MERCHANT_CHARGE was absent from consumption. Old tests used historical TRANSFER_EXTERNAL/MANDATE spellings. Recognition also missed canonical merchant charges and could mistake them for usage.

Analytics now recognizes canonical and historical kinds, counts only outgoing consumption/transfer rows, and treats both charge spellings as deductions rather than usage. Self-transfer, actual MMF subscribe/redeem and salary controls remain excluded; each actual transaction is counted once. A new mixed canonical/historical recurrence test is explicitly historical input, not proof that the old event producer supplied merchant metadata.

Red finalized six regressions on old code: **5 failed, 1 passed** (actual transfer producers, actual merchant event producer, mixed-kind sums, canonical recurrence). Repaired `.venv/bin/pytest tests/bankcore/test_analytics_live_kinds.py tests/bankcore/test_analytics.py -q --tb=short -p no:cacheprovider`: **78 passed**. Ruff and diff checks pass.

No DB/API/Docker/LLM/browser/deployment/push/merge. Producer merchant metadata, historical scheduled-transfer classification/fresh counters and weekly savings are separate authorized follow-up units; this first commit does not claim those are closed. Shared AGENTS/PRD are reserved for the coordinator.

## B. Period savings

Shared `periods.normalize_period` (also exported by analytics through its used import) returns integer monthly/annual cents for only frozen MONTHLY/WEEKLY. Banking cancellation and detected-charge annualization reuse it without an analytics↔banking import cycle. Weekly monthly savings use floor(annual/12); no-op/inactive billing stays zero. No new period/schema is introduced.

New actual cancellation versus production L3 read tests: old code **3 failed, 3 passed** (two wrong weekly amounts plus missing normalization entry). Repaired `.venv/bin/pytest tests/bankcore/test_period_savings.py tests/bankcore/test_analytics.py tests/bankcore/test_standing.py -q --tb=short -p no:cacheprovider`: **123 passed**. Tests compare MONTHLY and two WEEKLY amounts with actual `list_subscriptions` monthly/annual totals and reject float/bool/string/negative/unfrozen-period inputs. Ruff/diff checks pass.

## C. Producer metadata and fresh counters

The audit identified a P1 fresh-counter loss: old scheduled journals wrote `SCHEDULED_TRANSFER`, while reload counted only P2P kinds. New scheduled journals use the existing `P2P_INTERNAL` / `P2P_EXTERNAL` / `SELF_TRANSFER` codes and carry the source card/payee on the actual outgoing transaction effect. A shared pure `transaction_facts.is_external_transfer(tx, transfers, owned_account_ids)` is used by analytics and fresh L3 counters. Historical generic scheduled rows use their immutable ledger debit/credit identities to distinguish current-customer own targets from external ones. The loader passes only the requesting customer's `owner_ids`, not all customers in a multi-customer snapshot. Missing/mismatched historical ledger evidence stays conservative for limits. Existing posted/day/24h/CARD_SPEND windows remain unchanged.

Merchant events now pass authoritative merchant ID and deterministic category through optional `ledger.post_transfer` keyword parameters into real Transaction/AppendTransaction effects; frozen LedgerTransfer/tool/schema models are unchanged. The category helper uses a narrow project MCC catalogue first (7997/5812), then fixed aliases of the saved authoritative merchant.category (including existing seed labels), and unknown→其他. It never reads merchant.name or memo for classification. Three real pure merchant periods now feed recurring detection without test-injected merchant fields.

- New pure C regressions on old production: **13 failed** (scheduled canonical/metadata, actual merchant effects/categories, three real recurring charges and absent legacy classifier entry).
- PG red checkpoint `52391ed223954da267a2d287bb6a54ac22fb083a`, tests `tests/obligations_data/test_transaction_fact_reload.py`: coordinator personally ran fresh tmpfs PostgreSQL and reported **3 failed, 2 passed / 3.76s**, no fixture error. External fresh daily/rolling/card-used were zero instead of 100000; merchant ID was missing; own-transfer positive controls passed. Log `/tmp/sbg-transaction-reload-red.log`. This is coordinator evidence, not a subagent DB run.
- Repaired pure command `.venv/bin/pytest tests/bankcore -q --tb=short -p no:cacheprovider`: **471 passed**. Changed files pass Ruff and diff checks.
- Coordinator must now run the five PG regressions on the exact repaired commit, then relevant consistency/concurrency and full required local CI. No fresh-PG green is claimed by this subagent. D4 external debit category enrichment is a separate authorized follow-up branch to avoid duplicating the already-reviewed D4 patch.


### Coordinator PG green-attempt fixture correction

On the first repaired-code PG attempt the coordinator reported **3 passed / 2 failed**: current external/SELF and merchant-metadata cases passed; only historical-case setup attempted UPDATE of append-only ledger/transaction rows and was correctly rejected by the database guard. This is a fixture error, not evidence that the repaired P1 remains broken. The fixture now substitutes only the old scheduled producer's command code before its first real post_transfer/persist, preserving real customer/account identities, amount, ledger arithmetic, transaction effects and all append-only guards. It never UPDATEs historical money records or disables triggers. No PG rerun was performed by this subagent; coordinator must rerun the same five cases on the fixture-corrected commit. Original old-code **3 failed/2 passed** remains a valid independent red result.

最终legacy fixture修正47267a2集成6e8f327后，协调者真实tmpfs PG五条全绿，同组命令12例中本组5通过；其他单元4红/3正控不记本单元失败。日志/private/tmp/sbg-reserve-peer-reload-pg.log，完整CI与独立数学/账本评审另记。
