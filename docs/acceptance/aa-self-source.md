# AA self-inclusion and participant-read source repair

Date: 2026-10-31. Owner: task-1 isolated implementation, branch `fix/aa-self-source`.
Base: `ccf5d3330b44f4d5049fbbdb5bf5d78ac352ba1b`.

## Actual baseline and scope

The coordinator supplied `/private/tmp/sbg-freeze-continuation/aa-actual-baseline.json`, from the actual local session `ses_01M68W8B48H8CEGE5VWCMQW2AS`, plan `pln_01M68W8X2T8ZJEE4V6962ERSS7`. The trace at `/private/tmp/sbg-freeze-live-20261031/.ds-logs/full-e2e-aa-bill-baseline-ccf5d33-jet-01/09.trace.json` exited 4 / DENY. Its persisted draft had memo=null, no include_self source, and invented IDs `pye_xiaolin/pye_xiaozhou/pye_xiaochen`; three NOT_FOUND findings caused DENY. G3 had already persisted the correct SPLIT_CREATE scope and IDs `pye_zs_f1/f2/f3`, total 40000, include_self=true. This implementer read the coordinator's saved evidence and made no live LLM or SQL request. The older plan on another machine was not queried.

The coordinator explicitly expanded task ownership to the AA prompt and prerequisite supplement branch in `gather.py`, after SQL exposed this earlier participant-read failure. Bill/date regions and shared files remain outside this change.

## Component contract

Input: a typed AA write request, genuine account/payee read envelopes, and a model PlanBody proposal. Output: read records retaining their actual source_ref/taint/version/time; a locally checked PlanDraft, still requiring L2 authorization.

- AA write gathering supplements missing, unattempted get_accounts/list_payees reads. It does not guess IDs or repeat successful/failed model attempts.
- The planner rejects missing/empty payee data and invented participant IDs. include_self requires a declared typed-utterance source; existing reference/span checks and bounded regeneration remain in force. The code does not create spans, refs, quantities, participants or memo values.
- L2 recognizes only create_split_request/include_self with strict True, from a persisted typed/SCOPED scope whose sole action is SPLIT_CREATE and whose include_self field is exact True, with the declared span normalizing exactly to `我和`. Existing JWT, customer, session, epoch, lifetime and lineage proof remains the prerequisite. No global boolean vocabulary change or arbitrary scope-value equality is added.

## Executed validation

All Python commands used `NO_PROXY=127.0.0.1,localhost`; model adapter tests used scripted HTTP transport only.

| Command / gate | Actual result |
|---|---|
| Existing neighboring planner baseline: `pytest tests/agent/test_plan_e2e_fixes.py tests/agent/test_lost_card_sources.py tests/agent/test_merge_list_sources.py -q` | 16 passed |
| Initial planner source red tests | 6 failed / 3 passed: missing, read-bool and context source proposals wrongly returned |
| Expanded planner/prerequisite red tests on unchanged production | 13 failed / 5 passed: also no/empty payee reads and missing AA supplemental reads |
| L2 initial pure provenance red selection | 1 failed / 10 passed; valid `我和` remained unknown |
| Expanded AA pure green selection, excluding real G3/PG and quantity gates | 32 passed / 11 deselected |
| `pytest tests/agent -m 'not db' -q` | 652 passed / 40 deselected; `/private/tmp/sbg-aa-agent-green.log` |
| `make lint contracts-check` | PASS; 504 formatted files, frozen contracts v1 unchanged |
| Full default pytest with all external DB/Linux secret gates explicitly disabled | 3608 passed / 1082 skipped, exit 0 (38.86s); `/private/tmp/sbg-aa-full-green.log` |

The good legacy AA fixture now declares truthful account, amount, participants and include_self sources. Its wrong-payee negative proposal and assertions remain intact.

## Coordinator-owned gates and limits

Eleven real G3→assistant JWT→load_for_plan→entity/provenance/intent PostgreSQL cases were written in `tests/gateway/w2/db/test_aa_self_source.py`: valid, absent source, false value, unrelated span, false/numeric/wrong-action persisted scope, stale scope snapshot, exact expiry, cross-session source and tampered total. The fourteen pure cases also reject card-control/replace reuse, broad span, unscoped/context evidence, mixed action and non-bool scope values. Pure tests substitute only the I/O loaders and do not prove real lineage.

This implementer did not run any PostgreSQL, Docker, deployment, live09, push, merge, tag or deletion operation. No skipped/deselected case is counted as passed. Coordinator must run real PG, independent review, actual live09 and exact-final-head full local CI before integration. `make demo` replay remains the separate coordinator/runtime task; this base has no demo target. No AGENTS, PRD, Makefile, compose or deployment configuration was edited here.

Coordinator integration: original candidate cb52eb6 independent read-only review
APPROVE; reviewer reproduced old14 failures/18 negative passes, current32 pure
passes. Root separately ran35 passed/11 actual-PG skips, not counted as actual
PG acceptance. Rebased onto merged105 a113787 as01ffa3a; actual PG/live/SQL and
final whole-workflow exact-SHA CI are still required.

Root actual-PG run initially produced35 passes/1 fixture failure: the wrong-action
case changed SPLIT_CREATE to CARD_LOST but omitted card_id, so the unchanged real
loader correctly rejected the incomplete scope with INTENT_UNCLEAR before the
intended provenance check. Added a real card_id to make that alternate action
complete; its equal AA bool must still be unknown. No production check changed.
Rerun all AA cases plus adjacent lost-card/redemption actual-PG tests:36 passed,
no skips, including all11 new real G3/JWT/lineage tests. Standard live09 and final
complete exact-head CI remain pending.

## Increment: AA command incorrectly classified as read-only

The coordinator's `/private/tmp/sbg-freeze-continuation/aa-992b5dd-readonly-failure.json` records an actual fresh run on exact head `992b5ddad6ac5be4f05d8692f49a60174d939a40`: G3 returned SCOPED, sole SPLIT_CREATE, correct AA fields and no clarification; checkpoint Understanding returned `intents=[split_bill]`, `needs_write=false`. No plan, split or execution was created. Root completed the earlier 36 actual-PG cases; this new failure is earlier than the plan/source stages and cannot be repaired by retrying live09 blindly.

Only `understand.py`, the new `tests/agent/test_aa_understanding.py`, and this acceptance note are changed in this increment. The coordinator's rebased commits and prior actual-PG fixture correction remain intact.

The system prompt distinguishes creating an AA collection request from pure calculations and progress queries. A bounded semantic check rejects only a false `needs_write` proposal when state contains exactly one typed utterance, parse_status=SCOPED, no clarification object or clarification ID, and the entire text has the observed narrow `昨晚聚餐<digits[.fraction]>元，我和<Chinese names separated by 、>AA` shape. It does not inspect unavailable UtteranceRef.scope fields, infer action kinds, or use arbitrary scope-field equality. Quoted/questions/calculation/progress/mixed instructions, context/hint/action/multiple inputs, missing/unclear parse state and actual clarification remain outside this check. Other expense names, time synonyms and omitted-self forms are deliberately uncovered by this repair.

A mismatch consumes the existing one-regeneration budget with fixed feedback, shared with schema failures. Exhaustion raises an explicit LlmToolError. There is no deterministic True substitution: the adapter returns the complete validated Understanding from an actual model response, including its original slots. No amounts, participants, sources, plan args, contracts, graph routes, auth paths or retry budgets are changed. Provider 402/403/429 propagates immediately, including during the semantic regeneration.

All tests use scripted HTTP only, with `NO_PROXY=127.0.0.1,localhost`:

| Gate | Actual result |
|---|---|
| New understanding tests on unchanged `992b5dd` | 9 failed / 26 passed; `/private/tmp/sbg-aa-understanding-red.log` |
| Same new tests after the narrow guard | 35 passed; `/private/tmp/sbg-aa-understanding-green.log` |
| Offline Agent suite (`-m 'not db'`) | 687 passed / 40 deselected; `/private/tmp/sbg-aa-understanding-agent.log` |
| `make lint contracts-check` | PASS, 505 formatted Python files, frozen v1 unchanged |
| Full default suite, external DB/Linux secret flags explicitly zero | 3643 passed / 1082 skipped, exit 0 (30.90s); `/private/tmp/sbg-aa-understanding-full.log` |

This implementer made no new real LLM/API, PG, Docker, deployment or publication request. The increment requires independent safety review and the coordinator's fresh live09 before any freeze acceptance claim; all skipped/deselected cases remain unaccepted by these pure runs.

Standard production992b5dd fresh live09 first failed UNKNOWN: actual checkpoint
showed G3 SCOPED/SPLIT_CREATE and no clarification, but LLM needs_write=false
(intents split_bill), so no plan/split/execution was created. This was not a
source-layer acceptance pass. A narrow whole-sentence semantic check now uses
the existing single regeneration budget for that mistaken read-only proposal;
it never sets needs_write itself or changes amounts/participants/slots/sources.
Read-only questions, context, other sentence shapes and actual clarifications
remain excluded. New35 tests: old9 failed/26 negative passed, corrected35 passed;
independent safety increment APPROVE and116 focused tests, root56 focused pass.
Full default3643 passed/1082 conditional skips; real PG36 previously passed and
no gateway code changed in this increment.

Final actual L1 e884e0c94c1b5558b372f3caed3f20e0072b9b69 on a second fresh
generation demo_20261031121900_17ea362a: original09 completed14sec/exit1,
ALLOW/EXECUTED plan pln_01M68YDQ5P89X3MR9VH44FMGEX. Independent fixed SQL
confirmed one40000-cent split, self10000, pye_zs_f1/f2/f3 each10000 UNPAID,
one SPLIT_CREATE operation/execution, unchanged account counters/transfers/
transactions/orders and balanced ledger. Exact self source is persisted typed
SCOPED text 我和, same session/customer, sole SPLIT_CREATE; session/utterance/
execution epoch and scope/utterance IDs match. Agent plans.authorization_epoch
is nullable by existing design; it is not a substitute for actual evidence.
SQL receipt: aa-self-source-live.json. Final documentation-and-code full-SHA
whole local CI remains the merge gate. No demo-v1 freeze/tag claimed.
