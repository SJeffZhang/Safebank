# D4 card-spend category facts

Date: 2026-10-31. Separate branch `fix/external-debit-categories` from root `8625f09d42ef8955cf077c5ba967233c104adde9`, then event-fact production commit `382d9ba` applied locally as `50d3cbb`.

The root baseline already contains the independently reviewed D4 card/channel/merchant implementation; this commit changes only its category selection. `_post_simple` no longer emits the non-bucket label 消费. CARD_SPEND with an existing merchant ID uses the shared deterministic MCC/category helper. Missing/unknown merchant and non-CARD_SPEND kinds remain 其他; counterparty/memo/name cannot choose a category. Financial amounts, existing card/channel/limit controls, transaction IDs and the untrusted text values are unchanged. The exact generated Transaction and AppendTransaction effect have the same category.

Five new actual D4 event-applier tests on the old category branch: **5 failed**. Fixed `.venv/bin/pytest tests/bankcore tests/test_demo_event_bridge.py -q --tb=short -p no:cacheprovider`: **490 passed**. Tests cover MCC priority, authoritative category aliases, missing/unknown merchant, exact balance/card-used change, no external-transfer aggregate, unchanged memo/counterparty, and emitted effect equality. Changed files pass Ruff check/format; diff check passes.

No schema/seed change or actual API/DB/Docker/LLM/browser/deployment/push/merge. Independent safety review and final integrated real PG/full CI remain coordinator gates. To integrate after `382d9ba`, cherry-pick this final category-only commit; do not duplicate local prerequisite `50d3cbb`.
