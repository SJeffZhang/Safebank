# NOV1 query changes independent review

Target: `889488f90e235c4f927e0089abd23092b0439036`, commits `9173457 → ca82b7c → ba8652b → 889488f`. Exact `git archive` at `/private/tmp/sbg-nov1-repairs/review-query-889488f`. Repository/worktree/WIP unchanged. No DB/API/model/browser/Docker calls. All checks used in-process synthetic boundaries.

## Findings

### [P2] Holding total value is labelled as available-unit value

`services/agent/app/llm/respond.py:875` labels `market_value_cents` as “可用份额估值”. Production bankcore `packages/bankcore/src/safebank_bankcore/funds.py:181` computes this field from **all `h.units`**, while available_units is `units - reserved_units` (:185). Non-MMF redemption reserves units before settlement (`funds.py:490`), so the values differ on a normal supported lifecycle.

Independent pure reproduction uses actual `_rebuild_holdings` and `holdings_view`: units1000/reserved100 → available900, NAV1.2310, market_value123100 cents. The new deterministic response says `可用900.0000份；冻结100.0000份；…可用份额估值1231.00元`. The 1231 value includes the 100 frozen units; available-unit value would1107.90. Rename the label to total holding value (no new computation), or obtain a distinct authoritative available-unit valuation. Do not repurpose the stored field or alter fund accounting to satisfy copy.

Minimal failing test: archived `tests/test_review_query_independent.py::test_frozen_holding_total_value_is_not_claimed_to_be_available_units_value`.

### [P2] Deterministic query responses bypass both existing size budgets

`respond.py:175` returns `_query_reply` directly. Holdings (:962–968), scheduled (:969–973), upcoming (:974–984), and comparison (:985–989) build arbitrary-length strings and never pass through `_call_llm`'s600-character cap or `_join_capped`'s6000-character overview budget. `get_holdings` returns all customer orders, so this has a reachable growth source even without malformed data.

Independent pure reproduction with200 recent/settled order rows and one real product name returns **6832 characters**, no truncation/page marker. More rows grow linearly. Cap the deterministic display by complete rows/sections and append a clear omitted-record notice; do not silently drop an entire financial fact or claim the truncated display is complete. Keep full ReadRecord/source metadata intact.

Minimal failing test: archived `tests/test_review_query_independent.py::test_deterministic_holdings_query_keeps_a_bounded_user_reply`.

No new P0/P1 authorization or funds-write path found in the reviewed changes. Both findings are display behavior, not evidence of unauthorized execution.

## Reviewed boundaries that held

- Redemption prerequisites now deduplicate **successful** ReadRecords. A schema-rejected call, a valid call cancelled with its invalid batch, or404/NOT_FOUND does not satisfy get_holdings. Other gateway/provider errors propagate before supplemental reads; no added quota retry. Production supplement flags remain configured in main.py (unchanged by A).
- Merchant queries disclose their otherwise unspecified recent180-day interval. Cursor scanning is bounded at16 pages, repeats stop with a nonempty next_cursor; ambiguous/missing merchant IDs and incomplete unfiltered scans never cause a guessed merchant filter or a false zero/absence total. Confirmed filtered_all totals are authoritative across the filtered page set.
- Product comparison resolves two unique returned names to real product IDs, then calls compare_products. Duplicate name, duplicate ID, no match, or incomplete catalog avoids comparison instead of guessing a product. Unknown writes are not authorized by query selectors.
- Card reply distinguishes remaining daily limit, available funds, daily capacity, and single capacity; FROZEN/LOST do not claim spend availability. Minima are existing integer fields, so unchanged numeric verification can validate them.
- Upcoming fees use fresh profile business time, keep merchant fees and scheduled transfers separate, and show total reserve. last_charged_at is labelled historical instead of next charge. Risk escalation descriptions no longer imply OTP overrides hard limits.
- Display stripping leaves original ReadRecord and untrusted_paths intact. The generic analysis System explicitly says names/merchants/memos are untrusted business data, not instructions. A diagnostic showed per-value XML markers are removed in the summary; because that is accompanied by the explicit System rule and quoted data, this review does not treat marker removal alone as an exploit or independent blocker. No real model attack was attempted.
- Trade-date loader now restores Shanghai date from the aware stored timestamp. The unchanged writer persists business-date midnight with BUSINESS_TIMEZONE. The pure loader test exercises the real load/read code with only SQL execute substituted; actual PG timestamptz roundtrip is still a root gate.
- list_products overwrites only the returned projection's nav_history with[] after model_dump; get_product detail and the in-memory source are unchanged. Read/source envelopes remain actual gateway results, not invented refs.

## Checks and evidence

Exact command from the archived directory:

`PYTHONDONTWRITEBYTECODE=1 /Users/jet/.codex/worktrees/safebank-demo-runner/WeBank/.venv/bin/python -m pytest tests/agent/test_query_gather.py tests/agent/test_query_summaries.py tests/obligations_data/test_query_read_projection_unit.py tests/test_review_query_independent.py -q -p no:cacheprovider`

Result: **28 passed,2 failed**. All27 author-added query/gather/projection checks passed; the independent trust/source-retention check passed; the two tests above failed for the stated production behavior. No real provider/DB opt-in tests were enabled. The archive additionally contains the private reviewer test file; it is not part of the proposed branch.

## Disposition and root gates

**REQUEST_CHANGES** — two independently reproduced P2 display defects above.

Recommend fixing the two P2s and a narrow recheck before final integration. No whole-PR/full-six-scene approval is given here. Parent owns actual PostgreSQL order persist/reload and consistency/concurrency, precise combined-head CI, model/runtime acceptance if later authorized, AGENTS/PRD updates, and merge decisions. Original formal43 results19/16/8 remain immutable.

## Follow-up exact head bc01392ff69984061773fcb2ed698753458bac3c

**APPROVE (the two reviewed P2s closed).** The original889488f archive and red results above remain unchanged. The new head was separately archived at `/private/tmp/sbg-nov1-repairs/review-query-bc01392`; the unchanged independent test file was copied into that private archive.

Diff review confirms market_value_cents is unchanged and is now labelled “持仓总估值”. Deterministic holdings, scheduled, upcoming and comparison replies use complete-entry section budgeting within600 characters, preserve all ReadRecords, count total/omitted rows explicitly, and direct the user to existing business pages. Long rows are omitted whole; amounts and dates are not sliced. Prefix totals/window and complete section headings reserve budget before row selection. No authorization/source/DB change was added.

Independent focused command over author query/gather/projection/bounds tests and the unchanged private reviewer test file: **35 passed in0.82s**. Both original private failures now pass; explicit untrusted-System/source-retention control remains passed. This is a narrow pure re-review, not root's combined CI/PG or formal-live acceptance.
