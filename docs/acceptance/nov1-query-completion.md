# November 1 query completion

Base: aa7e5d26160ef3be1d7b9341a0398b2f0390d0b1. Owner: query completion worker. No real provider/API/DB/Docker/browser calls. AGENTS/PRD owned by coordinator.

## Plan and ledger

Spec: /private/tmp/sbg-nov1-repairs/read-path-audit.md, PRD 5/6/9.

- T1: Read summaries retain complete requested facts and dates, without raw protocol markers or unbounded JSON/NAV history. Test first in tests/agent/test_query_summaries.py; repair respond.py/private helper. Keep output verification unchanged.
- T2: Narrow read-only gather completion for merchant queries (explicit bounded range, complete cursor pages, exact returned merchant identity), product comparison (returned IDs -> actual compare_products), and redemption prerequisites. Tests in tests/agent/test_query_gather.py with scripted provider boundary; never authorize a write or guess IDs.
- T3: L3 trade_date timezone restoration and list-products NAV projection, independent commit and pure regression tests. Root owns true PG validation.
- T4: Targeted agent regression, review evidence and handoff. Original formal 19/16/8 unchanged; no new live success claim.

Review focus: incomplete pages must not become lifetime totals; unsupported dates must not be guessed; unavailable data not claimed absent; business time distinct from wall time; malformed/provider stop errors must propagate; wrapped untrusted text remains data and cannot authorize anything.

Execution: inline TDD as authorized by coordinator; do not await another approval. Each task red -> minimal implementation -> targeted green -> independent commit. Root coordinates fresh review/full CI.

## Results

Pending.

### T1a compact summaries

- Six new regression tests failed against aa7 for the expected missing/truncated facts; all six pass after the implementation.
- Targeted summary/respond/bill tests: 69 passed. Pure agent suite: 936 passed, 39 skipped (PG/live opt-ins remain skipped); ruff check and format pass.
- Products project comparison fields instead of full NAV-history JSON. Transaction overview omits free-form memo bulk and never silently drops an entire overflowing later block; incomplete overview is labelled. Card daily remaining, available/pending balance, subscription annual totals and scheduled reserve facts survive. Historical charges are labelled as historical.
- No provider or real service calls; no change to verification or authorization. Subsequent gather completion, holdings projection and L3 timezone work remain pending.
