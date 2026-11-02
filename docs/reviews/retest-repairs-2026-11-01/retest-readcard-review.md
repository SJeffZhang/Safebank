# Independent review: source-bound read-card projection

Verdict: **APPROVE**. No actionable P0/P1/P2 finding was identified in the reviewed second unit.

Reviewed target: `d91fddf8bb74c98b91039576aa716d13eec3b93b`; parent `15b9a6a` (the hint-card unit was context, not a second self-review). Read-only review of `services/agent/app/llm/cards.py`, the new/changed card tests, actual `ReadRecord`, gather success/error construction and the narrow query selector. Production and tests were not edited during this review.

## Findings checked

1. **Display-only source/path extraction** — cards.py:147–181 recursively makes new dict/list containers, retains the existing confidential-field exclusion, only permits selected display-name fields when their values are strings, and removes a single exact outer wrapper only where the original untrusted JSON pointer and escaped source agree. Nonmatching source, unmarked paths and embedded fragments remain literal. It uses no global regex deletion or HTML evaluation. Original read data and untrusted_paths remain unchanged.
2. **Successful read provenance** — gather.py `_execute` and `_execute_supplemental` call the gateway read executor before wrapping/constructing ReadRecord. Failed calls do not return a successful ReadRecord. The display generator consumes those records, not model-authored names, labels or failed ToolMessages. The new hostile-string case retains the string, omits memo and creates no CardAction.
3. **Relevant-query projection** — cards.py:184–259 only enters after the existing typed, read-only, one-utterance query selector accepts the turn. Product lookup pages are hidden only when a completed catalogue uniquely matches both requested names and the actual compare call arguments and returned two IDs agree. Merchant scan pages are hidden only after a completed scan uniquely resolves the requested merchant and completed filtered_all results have the same window and integer authoritative expense total. Missing, incomplete, ambiguous, wrong-ID and wrong-window cases retain their original reads. This does not authorize a write or recalculate bank totals.
4. **Real holding/order names** — cards.py:262–279 and 413–418 join only actual successful product reads by their returned product ID, require all observed names for an ID to agree, carry the real product-name source reference, and leave unknown/conflicting IDs unnamed. No name is hardcoded or guessed from an ID. Root confirmed PRD §9.5 will explicitly document this display-only cross-read name link and prohibit its use as a write source.
5. **Bounded output/security behavior** — the existing five-card cap, source_ref, data shape, original reads and card action generation remain separate. The new read projection never creates HINT, CLARIFY, OPEN_APPROVAL, NAVIGATE, plan or execution authority. Frontend safety is independently covered by that unit's React-text/table tests; this Agent-only review makes no browser or XSS-live claim.

## Independent verification

Command from the exact target worktree (`safebank-demo-runner/WeBank`):

```text
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest tests/agent/test_cards.py tests/agent/test_retest_hint_cards.py tests/agent/test_retest_read_cards.py -q -p no:cacheprovider
55 passed in 0.75s
```

The suite includes real GatherAdapter wrapping through compiled read graphs for merchant and compare paths, source/path mismatch and JSON pointer escaping, hostile HTML-like names, original-read immutability, partial/mismatched queries, and product-name conflicts. No production LLM, real Gateway/L3, database, Docker, deployment, browser or original 43-case rerun occurred. Full CI remains root-owned. Existing recorded red runs were inspected, not represented as independently rerun here.
