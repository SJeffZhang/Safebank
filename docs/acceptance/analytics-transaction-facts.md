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

Pending implementation, red/green evidence and coordinator-run PG acceptance.
