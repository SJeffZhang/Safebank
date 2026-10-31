# Partial redemption source acceptance (2026-10-31)

The planner previously required units from holdings, so an explicit partial redemption
was rejected and a whole-holding quantity could replace it locally. New agent regressions
on74baf71:3 failed /3 negative cases passed. Quantity now requires an explicit typed
utterance span; holdings are required availability data, not quantity authorization.

The strict adapter converts only a Python int to an exact four-place string; float/bool
remain invalid. Frozen wire contracts and strict tool schema are unchanged. Gateway
units matching uses exact Decimal values; scope, product/account/customer/session/expiry
and actual available shares remain separately enforced. No generic scope-value trust.

Real G3/JWT/PostgreSQL source tests old1 failed /5 passed, corrected6 pass, including
fractional units, wrong quantity/product span, expired and other-session sources. Full
IntentStage also matches valid quantities and rejects wrong quantity. Agent/contracts
718 passed /39 conditional skips. Independent read-only security review APPROVE,
reviewer personally11 focused tests. Funds execution/event and W2 combination pending.

Current l1_seed_v1 fixture gives Li Si10000.0000 units of prd_mmf; actual case34 can
redeem1000 without inventing a holding. Clean-stack L1 approval, pending order, simulated
settlement, final holdings/cash/ledger SQL and final exact-head full CI are still required.

Resumed after FORM merge9484424: rebased to ec822ea, preserving both AGENTS histories.
Personally verified old main implementations in isolated test processes: the new
agent regressions produce3 failures/3 negative passes; real G3/JWT/PG produces
1 failure/5 passes. Rebased implementation focused12 passed.

First new-generation live attempt timed out in L1 streaming, not login (SQL:
1 session/utterance, profile read, no plan/job/order). Request stopped and no
automatic retry occurred. A single bounded specified-model probe returned200.
Next actual live encountered malformed gather arguments; another diagnostic run
reached plan validation but lacked get_holdings and referenced a nonexistent
product read. All refused before any plan or bank write. These are failures,
not business success.

Added real gather regressions: three failed on the old implementation, one
read-only control passed. Redemption now supplements missing account, product
and holdings reads using exact runtime envelopes, never invented references or
quantity authorization; failed reads remain errors, successful reads are not
repeated, and read-only questions do not trigger write prerequisites. Further
independent review, new candidate live and final complete CI remain pending.

Supplemental-read candidate: combined72 tests passed; independent read-only
review APPROVE. Malformed model tool-call JSON remains an observed unresolved
LLM failure; this change does not claim to fix it. No quantity/source/approval
checks were relaxed. Full unit, candidate live and final exact-head CI pending.

Candidate99df716: standard-entrypoint new generation confirmed Li Si has
10000.0000 MMF units and1200000-cent cash before real L1 case34. That run failed
again in gather malformed tool-call arguments; no plan/order was created.
Seven format-boundary regressions: old6 failed/1 forbidden-tool control passed.
New bounded feedback stays within3 rounds, executes none of a malformed mixed
batch, omits malformed data from prompts, and rejects write/unknown names
immediately. Existing malformed negative fixtures now exhaust and assert all3
rounds, retaining zero-read assertions.

Six additional SDK-transport regressions demonstrated402/403/429 were hidden
by response/number-repair fallback. They now propagate without retries or model
change. Combined206 passed with lint. This closes local failure handling only;
independent incremental review, full unit and fresh candidate live remain pending.

Error-boundary candidate validation: independent read-only security APPROVE,
full unit3575 passed/1071 conditional skips, lint/format passed. Fresh real live
remains the next gate; no merge/freeze claimed.

Actual diagnostic capture showed unquoted JSON entity ID in get_holdings:
{"account_id": null, "product_id": prd_mmf}. Added this exact sanitized payload
regression: old99df716 fails, current repair passes; feedback now explicitly
requires double-quoted strings and null for missing values. Combined207 passed.
On bbeb765 with a read-only diagnostic observer, format repair recovered and
actual L1 yellow approval executed plan pln_01M68VD284HW85MEGV0ZQCSZ75. Fixed SQL
verified10000→9000 MMF units, cash1200000→1300000 cents, zero reserve, one
SETTLED1000-unit/100000-cent order, USED/YELLOW approval, one transfer and
operation, balanced ledger. This diagnostic evidence is separate from final
standard-entrypoint fresh-generation acceptance, which remains pending.

Exact malformed-response fixture and quote feedback: full unit3576 passed/1071
conditional skips; lint/format passed. Final standard-entrypoint live and full
workflow-equivalent exact-SHA CI remain pending.
