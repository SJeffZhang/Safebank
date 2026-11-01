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

### T2 bounded query completion

- Six new regressions observed RED before implementation: comparison never called; merchant page completion absent; invalid/404 holdings attempts suppressed prerequisites; monthly/merchant verified replies fell back to generic failure. All pass after implementation.
- Narrow read-only selectors use fresh business time and actual gateway IDs; two products must match uniquely, then real compare_products is read. Merchant lookup discloses the otherwise unspecified recent-180-day interval, follows cursor pages with a 16-page ceiling/repeated-cursor stop, then reads actual merchant_id filtered_all totals. Incomplete or ambiguous lookup explicitly avoids a zero/absence claim. No invented read refs/IDs or writable scope.
- Monthly consumption reads an authoritative get_spending_summary and separates transfers; verifier-inclusive 550.50/400.00 and filtered merchant45.50 pure chains pass unchanged OutputVerifier. This closes the demonstrated missing-source/analysis-output failure mechanisms; original case12's precise production fallback stage is still unconfirmed without its prior intermediate checkpoint/provider trace.
- Failed validation/NOT_FOUND redemption holdings calls no longer count as successful prerequisite reads; dependency/provider errors still propagate. Success is deduplicated.
- Final pure agent suite: 947 passed,39 skipped; focused gather/redemption/format suite:23 passed. Ruff check/format pass. Continuous required-read failure at402/403/429 stops after one call in boundary tests. No real services/provider/DB.

### T3 L3 business-date and catalog projection

- Two new REDs reproduced through actual load_bank_state/read boundaries with only SQL execute replaced: UTC10/14 16:00 storage incorrectly yielded10/14 trade_date instead of Shanghai10/15, and list_products included history. Both pass with business timezone restoration and history-free catalog projection; detail history and in-memory source remain unchanged.
- Focused pure domain/loader/read suite: see latest command output; real PG opt-in tests remain skipped. Root must run actual PostgreSQL persistence/reload plus concurrency/consistency gates before publishing. No DB or migration/state rewrite performed here.
- Production change is exactly trade_date restoration in state.py and list-only nav_history projection in reads.py, separate from L1 changes.

### T4 display semantics and final default suite

- Eight new REDs observed before implementation: holdings/orders and scheduled data fell back to raw IDs/JSON/UTC; protocol wrappers leaked through analysis output; risk labels implied OTP could bypass limits; holdings names and future-charge clock window were not deterministic; ACTIVE/FROZEN card capacity query lacked exact read/computation. All eight pass.
- Holdings query reads actual catalog names and renders current holdings separately from Chinese order states; account available/pending facts remain distinct. Scheduled query joins actual payee IDs to read names and converts execution time to Shanghai. Future-charge query reads fresh profile and expresses the exact open/closed business interval, merchant fees, scheduled transfers and total reserve. Card capacity states daily remainder, funds-limited daily capacity and funds/daily/single-limited single capacity; inactive cards never claim spend availability.
- Display-only protocol stripping does not alter ReadRecord/source metadata or write authorization. At B's request only the three finding labels changed; no REPLAN/hint or verification logic changed.
- Final focused query suite:25 passed. Full default repository pytest:4504 passed,1121 skipped in40.26s. All real provider/PG opt-ins remain unrun. Ruff check/format on touched Python files pass.
- Original formal43 results remain19/16/8; no new live or frozen-success claims. Root owns independent review, PG persist/reload, concurrency/consistency and exact-head full CI. Original12 fallback provenance still requires previously saved intermediate evidence; tests prove the new authoritative-source reply passes the unchanged verifier, not which original provider failure occurred.

All assigned implementation units are committed. Shared AGENTS/PRD untouched.

### Independent review corrections (P2 value label and reply budget)

B's exact889488f review found two display defects. New tests first reproduced five REDs: the reserved-unit holding label and unbounded holdings/scheduled/upcoming/long-name comparison collections. The fix labels unchanged market_value_cents as holding total value, then budgets deterministic collection replies to600 characters by complete financial rows/sections. Every section reports its full read count and, when omitted, the exact displayed-prefix count; fixed native-page guidance gives access to all records. Long rows are omitted whole, never string-sliced through names/amounts/dates. Underlying ReadRecords and financial amounts remain unchanged. No authorization/verification/provider retry change.

Verification after correction:

- New regression plus existing query/gather/summaries:30 passed.
- Original independent probes copied unchanged to a neutral test-only directory:3 passed. An initial invocation from inside B's original archive imported that original archive's production modules and reproduced its expected old2 failures; it was not testing the corrected worktree. The neutral directory avoids that import shadowing and leaves B's original red archive untouched.
- Pure agent suite:960 passed,39 skipped.
- Full default pytest:4509 passed,1121 skipped in41.97s; no real PG/provider opt-in.
- Ruff check and format pass. B will independently recheck the new exact commit; coordinator still owns PG and final integration gates.
