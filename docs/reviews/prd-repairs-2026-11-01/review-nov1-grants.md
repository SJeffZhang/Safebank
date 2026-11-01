# November 1 independent committed-grant lifecycle review

**APPROVE** for the scoped grant-archive unit at exact head `20a56d183fafcb09adc016ca14f24772f04e8b51`, parent `b27a3c802ce64c5ac3f01d5a5f3090a264e15e20`. No new actionable finding in grant_revocation.py or the two state archive hooks. Full S1 race/consumption behavior is a separate pending unit, not implicitly approved here.

## Exact archive / independent commands

Exported exact head with git archive to `/private/tmp/sbg-nov1-repairs/review-grants`. Reviewed only `services/gateway/app/grant_revocation.py`, the normal/orphan hooks in `state.py`, the NOV1 tests and acceptance; read the unchanged immediate state/caller/scheduler boundaries to establish the contract.

`PYTHONDONTWRITEBYTECODE=1 python -m pytest -c pyproject.toml /private/tmp/sbg-nov1-repairs/test_review_grants.py -q -p no:cacheprovider` from that archive: **23 passed in0.25s**, log `/private/tmp/sbg-nov1-repairs/review-grants-independent.log`. These are validation/SQL-construction controls using a fake connection, not SQL execution or rollback evidence. The first review harness compared SQLAlchemy BindParameter objects with literal strings; those eight harness assertion failures were corrected to inspect bound values before the accepted run, with no product change.

No DB, provider/API/live model, browser, Docker, deployment, root WIP or shared-document mutation occurred. Root's reported21 real PG results were inspected in test/acceptance form and remain root-owned evidence, not independently rerun.

## COMMITTED identity / caller boundary

Both new calls occur only after `_terminal_result` selects EXECUTED. Normal archive already locks/captures the customer-owned plan, origin, attempt, current revision and claims. Orphan archive separately requires OrphanExecutionIdentity, absent evaluation, UNKNOWN state and guarded CAS. `_terminal_result` revalidates the structured result, same plan ID, overall EXECUTED, committed_at and all EXECUTED step statuses for COMMITTED. CANCELLED routes to FAILED and does not invoke the new revocation hook.

The archive functions explicitly require W4's caller to establish actual L3 authenticity and captured identity; a locally constructed ExecutionResult is not proof of L3 execution. The new helper is internal, not an API or L1 tool. Existing production callers use the gateway-only L3 client and plan/customer-scoped execution/prepare/reconcile paths. This review does not replace W4's network/lock/attempt guarantees with local model validation.

Independent controls rejected missing/FAILED/uncommitted/wrong-plan/failed-step terminal results, no transaction, wrong result plan, mismatched resolved tool/target, and non-DELETED cancellation outcomes before grant DML. No new write authority is granted.

## Atomicity and consent preservation

The new helper uses the same AsyncConnection and requires an active transaction. It neither opens an independent transaction nor performs L3/network calls. Normal/orphan job CAS, plan/claim terminal updates and grant revocations are inside the caller's existing archive transaction; a helper error propagates, allowing the enclosing transaction to roll back all archive changes. Root's actual PG injected-after-grant-write tests provide runtime evidence of that rollback/recovery, which pure connection doubles cannot prove.

The actual PostgreSQL upsert sets only REVOKED and `coalesce(existing revoked_at, now())` on an owned-row conflict. It cannot replace original scope/hash/source-plan/risk/created_at, reactivate a grant, or overwrite the first revocation time. The conflict update has an explicit customer_id predicate, and no returned row becomes STATE_CHANGED; it does not report foreign-row conflict as success. Stable sorting avoids reversed instruction-row lock order. All cancellation outcomes are validated before any grant DML, and duplicate instruction IDs produce a single revocation write.

Independent SQL-construction controls assert these bound values/update fields/ownership predicate, plus no_op true/false, multi-cancel ordering/deduplication, conflict-error propagation and upfront invalid-second-outcome rejection. Actual concurrent SQL and cross-customer rollback remain PG gates.

## Missing lazy grant / orphan draft

When resolved steps exist, the helper requires exact step_id/tool/instruction_id agreement before revoking. A genuinely verified committed orphan may lack resolved_steps; that absence never permits plan resubmission or invented creation consent. It permits only a REVOKED tombstone from the true cancellation result, scoped to the captured plan customer. Its scope_json is explicitly a revocation marker, source_plan_id is the cancellation plan, and RED is a deny-only storage sentinel rather than an approval level.

Independent controls covered omitted/null/empty resolved_steps for both cancellation tools, retaining real `_terminal_result` validation. The produced SQL was always REVOKED with the marked revocation payload; conflict updates still preserve any original consent. No ACTIVE creation marker or new execution capability was constructed.

## No resurrection / S1 dependency

Within this unit, every grant write is deny-only. The unchanged S1 `_backfill_grant` baseline could return a local proposed ACTIVE dictionary after an ON CONFLICT DO NOTHING race even when the persisted row is REVOKED. Root explicitly assigned its fix to a separate S1 unit. This review approves the archive/tombstone primitive, not that old S1 return path or the combined end-to-end concurrency behavior. The separately supplied S1 head must be reviewed and integrated before a whole-system no-resurrection claim.

## Root PG gates / limits

The target test file's21 real-PG scenarios cover normal/UNKNOWN/orphan, existing/missing grants, write-after-failure rollback/recovery, wrong customer/target/tool/result/plan, no_op metadata preservation and cancelled failure with zero grant revocation. Root reports these passed and is running the full wave1_state regression.

If an exact integrated-head rerun is needed, root can use its already-authorized PG harness for `GATEWAY_DB_TESTS=1 python -m pytest tests/gateway/wave1_state/test_nov1_grant_revocation.py -q -p no:cacheprovider`, then the complete wave1_state folder. This reviewer did not run that command or provision a stack. No live formal cancellation, bank-state generation, final CI or freeze acceptance is claimed here.
