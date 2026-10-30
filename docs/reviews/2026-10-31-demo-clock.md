# D3 independent review (2026-10-31)

Reviewer: Codex read-only review_demo_clock; no code/file/deployment/model changes.

Initial REQUEST CHANGES:
- P1: UTC vs +08 period strings generated duplicate jobs for one real due instant; real PG reproduced.
- P2: shared background/advance scheduler dispatch could overtake rank order; counters missed background completions.

Final APPROVE: 0010_event_identity removes payload hash uniqueness, adds business source/instant
uniqueness; conflicting historical rows fail migration without deletion. Scanner reuses equivalent
instants. Shared local dispatch lock preserves ordering, with no database transaction across S1.
Counters use tracked L3 terminal records, including background completions. D3 console authentication,
demo-only mounting, error handling and original authorization boundaries remain intact.

Reviewer personally ran30 event-identity/scheduler/gateway-clock tests,15 lifecycle tests and diff check.
Coordinator full obligations+clock225 passed; frontend3 tests/typecheck/browser QA separately.

Read-only reviewer review_demo_generation separately APPROVE image rollback fix after requesting
fail-closed when previous images cannot be identified. Fixed prior overlays persist immutable image IDs;
failed switch and later demo-up retain old runtime/schema pairing. Reviewer15 lifecycle passed.

One review attempt hit Codex account usage limit and did not produce approval; user resumed after
reset, and the final review above completed. No DeepSeek quota error was retried.

Boundary: no live deployment by reviewers. Clock-advance job metadata is in process memory; after
restart a404 does not prove no event executed. This existing availability limit is explicit in ADR-012.

## Real fault follow-up

Actual cross-schema switch exposed Docker/containerd losing the replaced untagged old image,
so storing the image ID alone did not guarantee rollback. Added explicit unique retention tags
for all five old service images before build; a tag error/timeout aborts before build.
Old-code regression failed (zero retention calls); new lifecycle suite16 passed.
review_demo_generation independently re-reviewed this increment: APPROVE, personally16 passed,
no credential/privilege/data-delete regression. Actual cross-schema proof must be rerun.


## S1 source and merchant closure review

review_demo_generation APPROVE: exact seed CLI export/empty-gateway installation; immutable
instruction terms plus original first date; normal recovery requires genuine committed source
operation and matching successful public result/created ID/approved risk. Personally ran14 real-PG
and12 pure tests. Contract uses real L3 prepare/execute, not complete G4/G6/G8.

review_demo_clock found missing-grant refusal released its claim before terminal/audit persistence.
Coordinator reproduced absent row with a failing regression, removed premature release and verified
29 S1 tests including one audit on repeated refusal callbacks. Follow-up reviewer APPROVE, personally reran the refusal regression.
Candidate combined suite after this fix:718 passed; existing accounts/cards metadata-cycle warning.


Live follow-up: source cursors were not persisted from pure event state. New source-cursor
persistence runs in the same event transaction on execute/block, only for the run's own source.
Independent APPROVE; reviewer personally14 real-PG tests. Coordinator36 event/scheduler tests
passed. Extended four real two-bank contracts pass, including two periods for schedule/normal
merchant/cancelled merchant and an aborted event transaction preserving cursor/money/terminal state.
