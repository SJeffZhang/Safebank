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
